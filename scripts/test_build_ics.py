"""
scripts/build_ics.py のオフラインテスト。
python scripts/test_build_ics.py で実行する(pytest不使用、標準ライブラリのみ)。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_ics  # noqa: E402


# ---------- 単体レベルのヘルパー ----------

def _match(
    id_event="1001",
    round_no=3,
    kickoff_jst="2026-08-22T14:00:00+09:00",
    kickoff_date="2026-08-22",
    kickoff_tbd=False,
    finished=False,
    home=("100", "湘南", None),
    away=("200", "千葉", None),
    id_venue="16580",
):
    return {
        "idEvent": id_event,
        "round": round_no,
        "kickoffJst": kickoff_jst,
        "kickoffDate": kickoff_date,
        "kickoffTime": "14:00",
        "kickoffTbd": kickoff_tbd,
        "status": "Not Started",
        "finished": finished,
        "home": {"idTeam": home[0], "ja": home[1] + "ベルマーレ", "short": home[1], "score": home[2]},
        "away": {"idTeam": away[0], "ja": away[1] + "FC", "short": away[1], "score": away[2]},
        "idVenue": id_venue,
    }


def test_resolve_event_summary_unfinished():
    m = _match()
    e = build_ics.resolve_event("j2", m, {}, {"venues": {}})
    assert e["summary"] == "湘南 vs 千葉（J2 第3節）", e["summary"]
    assert e["dtstart"] == "20260822T050000Z", e["dtstart"]  # JST14:00 -> UTC05:00
    assert e["dtend"] == "20260822T070000Z", e["dtend"]
    assert e["allDay"] is False
    assert e["hasAlarm"] is True


def test_resolve_event_summary_finished_includes_score():
    m = _match(finished=True, home=("100", "湘南", 2), away=("200", "千葉", 1))
    e = build_ics.resolve_event("j2", m, {}, {"venues": {}})
    assert e["summary"] == "湘南 2-1 千葉（J2 第3節）", e["summary"]


def test_resolve_event_tbd_is_all_day_no_alarm():
    m = _match(kickoff_tbd=True)
    e = build_ics.resolve_event("j2", m, {}, {"venues": {}})
    assert e["allDay"] is True
    assert e["dtstart"] == "20260822", e["dtstart"]
    assert e["dtend"] == "20260823", e["dtend"]
    assert "時刻未定" in e["summary"], e["summary"]
    assert e["hasAlarm"] is False


def test_resolve_venue_j2_uses_venues_dict():
    m = _match(id_venue="16580")
    master = {"venues": {"16580": {"ja": "レモンガススタジアム平塚"}}}
    e = build_ics.resolve_event("j2", m, {}, master)
    assert e["location"] == "レモンガススタジアム平塚", e["location"]
    assert "公式サイト" not in e["description"]  # J2の正規解決には確認注記を付けない


def test_resolve_venue_j2_missing_venue_omits_location():
    m = _match(id_venue="99999")
    master = {"venues": {"16580": {"ja": "レモンガススタジアム平塚"}}}
    e = build_ics.resolve_event("j2", m, {}, master)
    assert e["location"] is None


def test_resolve_venue_j1_uses_home_venue_ja_with_caveat():
    m = _match()
    home_team = {"homeVenueJa": "レモンガススタジアム平塚", "jleagueSlug": "shonan"}
    e = build_ics.resolve_event("j1", m, home_team, {})
    assert e["location"] == "レモンガススタジアム平塚"
    assert "公式サイトで確認してください" in e["description"], e["description"]
    assert "shonan" in e["description"]


def test_resolve_venue_j1_no_home_venue_omits_location_no_caveat():
    m = _match()
    e = build_ics.resolve_event("j1", m, {}, {})
    assert e["location"] is None
    assert "公式サイト" not in e["description"]


# ---------- SEQUENCE永続化(最重要) ----------

def test_seq_starts_at_zero_for_new_event():
    state = {}
    resolved = build_ics.resolve_event("j2", _match(), {}, {"venues": {}})
    build_ics.update_state_entry(state, "1001", resolved)
    assert state["1001"]["seq"] == 0
    assert state["1001"]["status"] == "CONFIRMED"


def test_seq_unchanged_when_nothing_changes():
    state = {}
    resolved = build_ics.resolve_event("j2", _match(), {}, {"venues": {}})
    build_ics.update_state_entry(state, "1001", resolved)
    build_ics.update_state_entry(state, "1001", resolved)  # 2回目、同じ内容
    assert state["1001"]["seq"] == 0


def test_seq_bumps_when_dtstart_changes():
    state = {}
    m1 = _match(kickoff_jst="2026-08-22T14:00:00+09:00")
    resolved1 = build_ics.resolve_event("j2", m1, {}, {"venues": {}})
    build_ics.update_state_entry(state, "1001", resolved1)

    m2 = _match(kickoff_jst="2026-08-23T14:00:00+09:00")  # 延期
    resolved2 = build_ics.resolve_event("j2", m2, {}, {"venues": {}})
    build_ics.update_state_entry(state, "1001", resolved2)

    assert state["1001"]["seq"] == 1, state["1001"]
    assert state["1001"]["dtstart"] == "20260823T050000Z"


def test_seq_bumps_when_score_appears_in_summary():
    state = {}
    m1 = _match(finished=False)
    build_ics.update_state_entry(state, "1001", build_ics.resolve_event("j2", m1, {}, {"venues": {}}))
    m2 = _match(finished=True, home=("100", "湘南", 2), away=("200", "千葉", 1))
    build_ics.update_state_entry(state, "1001", build_ics.resolve_event("j2", m2, {}, {"venues": {}}))
    assert state["1001"]["seq"] == 1
    assert "2-1" in state["1001"]["summary"]


# ---------- build_all() 結合テスト(実ファイルI/O) ----------

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _setup_fixture(tmp: Path, matches: list[dict]):
    masters_dir = tmp / "data" / "masters"
    processed_dir = tmp / "data" / "processed"
    _write_json(
        masters_dir / "j2_master.json",
        {
            "meta": {"season": "2026-2027"},
            "teams": [
                {"idTeam": "100", "ja": "湘南ベルマーレ", "short": "湘南", "jleagueSlug": "shonan"},
                {"idTeam": "200", "ja": "ジェフ千葉", "short": "千葉", "jleagueSlug": "chiba"},
            ],
            "venues": {"16580": {"ja": "レモンガススタジアム平塚"}},
        },
    )
    _write_json(masters_dir / "j1.json", {"meta": {"season": "2026-2027"}, "teams": []})
    _write_json(masters_dir / "j3.json", {"meta": {"season": "2026-2027"}, "teams": []})
    _write_json(processed_dir / "j2_matches.json", {"matches": matches})
    _write_json(processed_dir / "j1_matches.json", {"matches": []})
    _write_json(processed_dir / "j3_matches.json", {"matches": []})

    orig = {
        "MASTER_FILES": build_ics.MASTER_FILES,
        "PROCESSED_DIR": build_ics.PROCESSED_DIR,
        "ICS_STATE_PATH": build_ics.ICS_STATE_PATH,
    }
    build_ics.MASTER_FILES = {
        "j1": masters_dir / "j1.json",
        "j2": masters_dir / "j2_master.json",
        "j3": masters_dir / "j3.json",
    }
    build_ics.PROCESSED_DIR = processed_dir
    build_ics.ICS_STATE_PATH = tmp / "data" / "history" / "ics_state.json"
    return orig


def _restore(orig):
    build_ics.MASTER_FILES = orig["MASTER_FILES"]
    build_ics.PROCESSED_DIR = orig["PROCESSED_DIR"]
    build_ics.ICS_STATE_PATH = orig["ICS_STATE_PATH"]


def test_build_all_writes_ics_per_club_and_state_file():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        orig = _setup_fixture(tmp, [_match()])
        try:
            club_count, cancelled = build_ics.build_all(dist_dir=tmp / "dist")
            assert club_count == 2, club_count
            assert cancelled == 0
            shonan_ics = (tmp / "dist" / "ics" / "100.ics").read_text(encoding="utf-8")
            assert "BEGIN:VEVENT" in shonan_ics
            assert "UID:1001@jleague-app" in shonan_ics
            assert build_ics.ICS_STATE_PATH.exists()
            state = json.loads(build_ics.ICS_STATE_PATH.read_text(encoding="utf-8"))
            assert state["1001"]["seq"] == 0
        finally:
            _restore(orig)


def test_build_all_second_run_bumps_seq_on_postponement():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        orig = _setup_fixture(tmp, [_match(kickoff_jst="2026-08-22T14:00:00+09:00")])
        try:
            build_ics.build_all(dist_dir=tmp / "dist")
            # 延期: キックオフ日時が変わる
            _write_json(
                build_ics.PROCESSED_DIR / "j2_matches.json",
                {"matches": [_match(kickoff_jst="2026-08-23T14:00:00+09:00")]},
            )
            build_ics.build_all(dist_dir=tmp / "dist")
            state = json.loads(build_ics.ICS_STATE_PATH.read_text(encoding="utf-8"))
            assert state["1001"]["seq"] == 1, state["1001"]
            ics_text = (tmp / "dist" / "ics" / "100.ics").read_text(encoding="utf-8")
            assert "SEQUENCE:1" in ics_text
        finally:
            _restore(orig)


def test_build_all_unchanged_run_keeps_seq_zero():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        orig = _setup_fixture(tmp, [_match()])
        try:
            build_ics.build_all(dist_dir=tmp / "dist")
            build_ics.build_all(dist_dir=tmp / "dist")  # 2回目、変更なし
            state = json.loads(build_ics.ICS_STATE_PATH.read_text(encoding="utf-8"))
            assert state["1001"]["seq"] == 0
        finally:
            _restore(orig)


def test_build_all_disappeared_match_becomes_cancelled_and_persists():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        orig = _setup_fixture(tmp, [_match()])
        try:
            build_ics.build_all(dist_dir=tmp / "dist")
            # 試合が消える(idEventがmatches.jsonから無くなる)
            _write_json(build_ics.PROCESSED_DIR / "j2_matches.json", {"matches": []})
            club_count, cancelled = build_ics.build_all(dist_dir=tmp / "dist")
            assert cancelled == 1
            state = json.loads(build_ics.ICS_STATE_PATH.read_text(encoding="utf-8"))
            assert state["1001"]["status"] == "CANCELLED"
            assert state["1001"]["seq"] == 1
            ics_text = (tmp / "dist" / "ics" / "100.ics").read_text(encoding="utf-8")
            assert "STATUS:CANCELLED" in ics_text
            assert "UID:1001@jleague-app" in ics_text  # VEVENT自体は削除されず残る

            # さらにもう一度生成しても CANCELLED のまま(再度seqは上がらない)
            build_ics.build_all(dist_dir=tmp / "dist")
            state2 = json.loads(build_ics.ICS_STATE_PATH.read_text(encoding="utf-8"))
            assert state2["1001"]["status"] == "CANCELLED"
            assert state2["1001"]["seq"] == 1
        finally:
            _restore(orig)


def test_build_all_zero_matches_gives_empty_calendars_no_crash():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        orig = _setup_fixture(tmp, [])
        try:
            club_count, cancelled = build_ics.build_all(dist_dir=tmp / "dist")
            assert club_count == 2
            assert cancelled == 0
            text = (tmp / "dist" / "ics" / "100.ics").read_text(encoding="utf-8")
            assert "BEGIN:VCALENDAR" in text and "END:VCALENDAR" in text
            assert "BEGIN:VEVENT" not in text
        finally:
            _restore(orig)


# ---------- RFC5545構造(折り返し・CRLF・エスケープ・UTF-8境界) ----------

def test_ics_escape_special_chars():
    raw = "湘南, vs; 千葉\\home"
    escaped = build_ics.ics_escape(raw)
    assert escaped == "湘南\\, vs\\; 千葉\\\\home", escaped


def test_fold_line_keeps_lines_within_75_octets():
    long_summary = "SUMMARY:" + ("湘南ベルマーレ " * 20)
    folded = build_ics.fold_line(long_summary)
    physical_lines = folded.split("\r\n")
    assert len(physical_lines) > 1
    for line in physical_lines:
        assert len(line.encode("utf-8")) <= 75, (len(line.encode("utf-8")), line)
    # 継続行は先頭が1個の半角スペース
    for line in physical_lines[1:]:
        assert line.startswith(" ")


def test_fold_line_never_splits_utf8_multibyte_char():
    long_summary = "SUMMARY:" + ("湘" * 60)
    folded = build_ics.fold_line(long_summary)
    for line in folded.split("\r\n"):
        content = line[1:] if line.startswith(" ") else line
        # デコードできる(=マルチバイト境界を割っていない)ことを確認
        content.encode("utf-8")  # ここまでは既にstr、壊れていればそもそもfold_line内でエラーになっている
    rejoined = "".join(l[1:] if l.startswith(" ") else l for l in folded.split("\r\n"))
    assert rejoined == long_summary


def test_render_ics_uses_crlf_and_ends_with_crlf():
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "END:VCALENDAR"]
    text = build_ics.render_ics(lines)
    assert "\r\n" in text
    assert text.endswith("\r\n")
    assert "\n\n" not in text.replace("\r\n", "")  # 素の\nが紛れ込んでいない
    assert text.count("\r\n") >= len(lines)


def test_build_vevent_no_valarm_for_allday():
    entry = {
        "seq": 0, "status": "CONFIRMED", "dtstart": "20260822", "dtend": "20260823",
        "allDay": True, "summary": "湘南 vs 千葉（J2 第3節・時刻未定）", "location": None,
        "description": "", "hasAlarm": False, "homeIdTeam": "100", "awayIdTeam": "200",
    }
    lines = build_ics.build_vevent("1001", entry, "20260820T000000Z")
    text = "\n".join(lines)
    assert "DTSTART;VALUE=DATE:20260822" in text
    assert "DTEND;VALUE=DATE:20260823" in text
    assert "BEGIN:VALARM" not in text


def test_build_vevent_has_valarm_for_timed_event():
    entry = {
        "seq": 0, "status": "CONFIRMED", "dtstart": "20260822T050000Z", "dtend": "20260822T070000Z",
        "allDay": False, "summary": "湘南 vs 千葉（J2 第3節）", "location": "レモンガススタジアム平塚",
        "description": "キックオフ 14:00", "hasAlarm": True, "homeIdTeam": "100", "awayIdTeam": "200",
    }
    lines = build_ics.build_vevent("1001", entry, "20260820T000000Z")
    text = "\n".join(lines)
    assert "BEGIN:VALARM" in text
    assert "TRIGGER:-PT1H" in text
    assert "LOCATION:レモンガススタジアム平塚" in text


def test_build_vevent_cancelled_has_no_valarm():
    entry = {
        "seq": 1, "status": "CANCELLED", "dtstart": "20260822T050000Z", "dtend": "20260822T070000Z",
        "allDay": False, "summary": "湘南 vs 千葉（J2 第3節）", "location": None,
        "description": "", "hasAlarm": True, "homeIdTeam": "100", "awayIdTeam": "200",
    }
    lines = build_ics.build_vevent("1001", entry, "20260820T000000Z")
    text = "\n".join(lines)
    assert "STATUS:CANCELLED" in text
    assert "BEGIN:VALARM" not in text


def test_season_short():
    assert build_ics.season_short("2026-2027") == "2026-27"
    assert build_ics.season_short("weird") == "weird"


# ---------- ランナー ----------

def main():
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"OK   {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"ERROR {name}: {type(e).__name__}: {e}")

    print()
    if failed:
        print(f"{len(failed)}/{len(tests)}件失敗: {failed}")
        sys.exit(1)
    print(f"全{len(tests)}件OK")


if __name__ == "__main__":
    main()
