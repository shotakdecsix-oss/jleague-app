"""
scripts/build_calendar.py のオフラインテスト。
python scripts/test_build_calendar.py で実行する(pytest不使用、標準ライブラリのみ)。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_calendar as build_calendar_module  # noqa: E402
from build_calendar import build_calendar, shrink_match, sort_key  # noqa: E402


def M(id_event, kickoff, home, away, round_no=1, finished=False, kickoff_tbd=False):
    return {
        "idEvent": id_event,
        "round": round_no,
        "kickoffJst": kickoff,
        "kickoffTbd": kickoff_tbd,
        "status": "FT" if finished else "NS",
        "finished": finished,
        "home": {"idTeam": home[0], "ja": home[1] + "FC", "short": home[1], "score": home[2] if finished else None},
        "away": {"idTeam": away[0], "ja": away[1] + "FC", "short": away[1], "score": away[2] if finished else None},
    }


def _write_fixture(tmp: Path, per_league: dict) -> dict:
    """{league: [match,...]} を tmp/{league}_matches.json に書き、元のPROCESSED_DIRを返す(復元用)。"""
    for league, matches in per_league.items():
        data = {"meta": {"season": "2026-2027"}, "matches": matches}
        (tmp / f"{league}_matches.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    orig = build_calendar_module.PROCESSED_DIR
    build_calendar_module.PROCESSED_DIR = tmp
    return orig


def test_shrink_match_keeps_null_score_for_unfinished() -> None:
    """未消化試合のscoreはnullのまま出ること(0にフォールバックしていないこと)。"""
    m = M("e1", "2026-08-22T14:00:00+09:00", ("A", "Aクラブ", None), ("B", "Bクラブ", None), finished=False)
    s = shrink_match(m, "j2")
    assert s["h"][2] is None, s
    assert s["a"][2] is None, s
    print("OK: 未消化試合のscoreはnull(0にフォールバックしない)")


def test_shrink_match_includes_finished_score() -> None:
    m = M("e1", "2026-08-22T14:00:00+09:00", ("A", "Aクラブ", 2), ("B", "Bクラブ", 1), finished=True)
    s = shrink_match(m, "j1")
    assert s["h"] == ["A", "Aクラブ", 2], s
    assert s["a"] == ["B", "Bクラブ", 1], s
    assert s["l"] == "j1"
    print("OK: 消化済み試合はスコアが正しく短縮キーに入る")


def test_sort_by_kickoff_ascending_and_league_tiebreak() -> None:
    """kickoffJst昇順。同時刻はj1->j2->j3の順であること。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fixture = {
            "j1": [M("j1a", "2026-08-22T14:00:00+09:00", ("A", "A", None), ("B", "B", None))],
            "j2": [M("j2a", "2026-08-22T14:00:00+09:00", ("C", "C", None), ("D", "D", None))],
            "j3": [M("j3a", "2026-08-22T14:00:00+09:00", ("E", "E", None), ("F", "F", None))],
            # j1だけ別時刻(より早い)を混ぜて昇順チェックも兼ねる
        }
        fixture["j1"].append(M("j1b", "2026-08-21T10:00:00+09:00", ("G", "G", None), ("H", "H", None)))
        orig = _write_fixture(tmp, fixture)
        try:
            out = build_calendar()
        finally:
            build_calendar_module.PROCESSED_DIR = orig

        ids = [m["e"] for m in out["matches"]]
        # 8/21のj1bが最初、その後8/22の同時刻3件はj1->j2->j3の順
        assert ids == ["j1b", "j1a", "j2a", "j3a"], ids
        print("OK: kickoffJst昇順ソート、同時刻はj1->j2->j3の順")


def test_null_kickoff_matches_are_grouped_at_end() -> None:
    """kickoffJstがnull(日程未定)の試合は配列末尾にまとまること。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fixture = {
            "j1": [
                M("dated1", "2026-08-22T14:00:00+09:00", ("A", "A", None), ("B", "B", None)),
                M("tbd1", None, ("C", "C", None), ("D", "D", None)),
            ],
            "j2": [M("dated2", "2026-08-21T14:00:00+09:00", ("E", "E", None), ("F", "F", None))],
            "j3": [M("tbd2", None, ("G", "G", None), ("H", "H", None))],
        }
        orig = _write_fixture(tmp, fixture)
        try:
            out = build_calendar()
        finally:
            build_calendar_module.PROCESSED_DIR = orig

        ids = [m["e"] for m in out["matches"]]
        # 日付ありの2件(dated2が先、dated1が後)のあとに、null2件(j1->j3の順)が続く
        assert ids == ["dated2", "dated1", "tbd1", "tbd2"], ids
        assert out["matches"][-1]["k"] is None
        assert out["matches"][-2]["k"] is None
        print("OK: kickoffJstがnullの試合は末尾にまとまる")


def test_meta_counts_sum_matches_total() -> None:
    """3リーグの試合数合計がmeta.countsの合計と一致すること。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fixture = {
            "j1": [M(f"j1-{i}", f"2026-08-{22+i:02d}T14:00:00+09:00", ("A", "A", None), ("B", "B", None)) for i in range(3)],
            "j2": [M(f"j2-{i}", f"2026-08-{22+i:02d}T14:00:00+09:00", ("C", "C", None), ("D", "D", None)) for i in range(2)],
            "j3": [M(f"j3-{i}", f"2026-08-{22+i:02d}T14:00:00+09:00", ("E", "E", None), ("F", "F", None)) for i in range(4)],
        }
        orig = _write_fixture(tmp, fixture)
        try:
            out = build_calendar()
        finally:
            build_calendar_module.PROCESSED_DIR = orig

        assert out["meta"]["counts"] == {"j1": 3, "j2": 2, "j3": 4}, out["meta"]["counts"]
        assert sum(out["meta"]["counts"].values()) == len(out["matches"])
        print("OK: meta.countsの合計がmatches件数と一致する")


def test_main_writes_compact_json_without_indent() -> None:
    """main()が実際にファイルへ書き出し、indentなしのJSON(1行)であること。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fixture = {
            "j1": [M("e1", "2026-08-22T14:00:00+09:00", ("A", "A", None), ("B", "B", None))],
            "j2": [], "j3": [],
        }
        orig = _write_fixture(tmp, fixture)
        try:
            build_calendar_module.main()
        finally:
            build_calendar_module.PROCESSED_DIR = orig

        out_path = tmp / "calendar.json"
        assert out_path.exists()
        text = out_path.read_text(encoding="utf-8")
        assert "\n" not in text, "indentなし(1行)で出力されるはず"
        data = json.loads(text)
        assert len(data["matches"]) == 1
        print("OK: main()はindentなしの1行JSONを書き出す")


def main() -> None:
    tests = [
        test_shrink_match_keeps_null_score_for_unfinished,
        test_shrink_match_includes_finished_score,
        test_sort_by_kickoff_ascending_and_league_tiebreak,
        test_null_kickoff_matches_are_grouped_at_end,
        test_meta_counts_sum_matches_total,
        test_main_writes_compact_json_without_indent,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
