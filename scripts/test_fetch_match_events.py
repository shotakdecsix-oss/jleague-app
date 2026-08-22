"""
scripts/match_events_parser.py と scripts/fetch_match_events.py のオフラインテスト。
python scripts/test_fetch_match_events.py で実行する(pytest不使用、標準ライブラリのみ)。

data/tmp/sample_match_*.html は scripts/save_sample_html.py の実行結果で、.gitignore対象
(リポジトリにコミットされない)。無ければサンプル依存のテストはスキップし、他のテストだけ実行する。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_match_events import EVENTS_WINDOW_HOURS, build_lineups, parse_and_merge, pick_candidates  # noqa: E402
from match_events_parser import (  # noqa: E402
    extract_schedule_index,
    find_cards,
    find_formations,
    find_goals,
    find_subs,
)
from team_matching import load_master_teams  # noqa: E402
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = BASE_DIR / "data" / "tmp"


def _match(id_event="E1", kickoff_jst="2026-08-22T14:00:00+09:00", home_id="T_A", away_id="T_B"):
    return {
        "idEvent": id_event,
        "kickoffJst": kickoff_jst,
        "home": {"idTeam": home_id, "ja": "ホーム"},
        "away": {"idTeam": away_id, "ja": "アウェイ"},
    }


# ---------- pick_candidates: 時間窓の判定 ----------

def test_pick_candidates_window():
    now = datetime.fromisoformat("2026-08-22T18:00:00+09:00")
    matches = [
        _match("in_window", "2026-08-22T14:00:00+09:00"),   # 4時間前。窓内
        _match("just_now", "2026-08-22T17:59:00+09:00"),    # 直前。窓内
        _match("too_old", "2026-08-20T18:00:00+09:00"),     # 48時間前。窓外
        _match("future", "2026-08-22T20:00:00+09:00"),      # まだキックオフ前。対象外
        _match("no_kickoff", None),                          # 日時未定。対象外
    ]
    matches[4]["kickoffJst"] = None
    picked = {m["idEvent"] for m in pick_candidates(matches, now)}
    assert picked == {"in_window", "just_now"}, picked
    print(f"OK: pick_candidates() が窓({EVENTS_WINDOW_HOURS}時間)内の試合だけを選ぶことを確認")


# ---------- parse_and_merge: 退行(0件上書き)防止の安全弁 ----------

def test_parse_and_merge_regression_guard(monkeypatch=None):
    fake_master = {
        "teams": [
            {"idTeam": "T_A", "en": "a", "aliases": [], "aliasesJa": [], "ja": "ホーム"},
            {"idTeam": "T_B", "en": "b", "aliases": [], "aliasesJa": [], "ja": "アウェイ"},
        ]
    }
    all_teams = load_master_teams("j2", fake_master)
    existing = {"E1": {"goals": [{"minute": "10"}], "cards": [], "subs": []}}

    import fetch_match_events as fme

    # HTMLが取れたが目印文字列が無い(=0件になる)ケースを模擬する
    original_fetch_html = fme.fetch_html
    fme.fetch_html = lambda url: "<html>マークアップが変わって何も拾えないページ</html>"
    try:
        failed = []
        result = parse_and_merge("E1", {"code": "000000", "url": "dummy"}, all_teams, existing, failed)
        assert result is None, "既存データがあるのに0件で上書きしてしまっている"
        assert failed and failed[0]["reason"] == "regression_zero_events", failed
        print("OK: 0件への退行時は既存データを上書きしないことを確認")

        # 初回(既存データが無い)は0件でも正当な結果として上書きしてよい
        failed2 = []
        result2 = parse_and_merge("E2", {"code": "000000", "url": "dummy"}, all_teams, {}, failed2)
        assert result2 is not None and result2["goals"] == []
        assert not failed2
        print("OK: 既存データが無い試合の0件は正当な結果として書き込まれることを確認")
    finally:
        fme.fetch_html = original_fetch_html


# ---------- lineups: 抽出できなかった回は前回分を維持する ----------

def test_lineups_carry_forward_when_missing():
    fake_master = {
        "teams": [
            {"idTeam": "T_A", "en": "a", "aliases": [], "aliasesJa": [], "ja": "ホーム"},
            {"idTeam": "T_B", "en": "b", "aliases": [], "aliasesJa": [], "ja": "アウェイ"},
        ]
    }
    all_teams = load_master_teams("j2", fake_master)
    old_lineups = {"home": {"idTeam": "T_A", "formation": "4-4-2", "players": [{"id": 1, "name": "選手A", "number": "9"}]},
                   "away": {"idTeam": "T_B", "formation": "4-3-3", "players": []}}
    existing = {"E1": {"goals": [], "cards": [], "subs": [], "lineups": old_lineups}}

    import fetch_match_events as fme

    original_fetch_html = fme.fetch_html
    # ゴール等の目印もformationsも無い、マークアップが変わったページを模擬する
    fme.fetch_html = lambda url: "<html>何も拾えないページ</html>"
    try:
        failed = []
        result = parse_and_merge("E1", {"code": "000000", "url": "dummy"}, all_teams, existing, failed)
        assert result is not None, "得点等が0件でも既存データありのケースはregression guardに掛からないはず(lineups確認用の別データなので)"
        assert result["lineups"] == old_lineups, "formationsが取れなかった回は前回のlineupsを維持するはず"
        print("OK: 出場メンバーが今回取れなくても前回分を維持することを確認")
    finally:
        fme.fetch_html = original_fetch_html


def test_parsers_against_samples():
    livetxt = SAMPLE_DIR / "sample_match_livetxt.html"
    review = SAMPLE_DIR / "sample_match_review.html"
    schedule = SAMPLE_DIR / "sample_match_schedule.html"
    if not (livetxt.exists() and review.exists() and schedule.exists()):
        print("SKIP: data/tmp/sample_match_*.html が無いためサンプル依存テストをスキップ"
              "(python scripts/save_sample_html.py で生成できます)")
        return

    from fetch_official import extract_next_chunks

    chunks = extract_next_chunks(livetxt.read_text(encoding="utf-8"))
    goals, cards, subs = find_goals(chunks), find_cards(chunks), find_subs(chunks)
    assert len(goals) == 5, f"livetxt: 得点5件のはずが{len(goals)}件"
    assert len(cards) == 3, f"livetxt: カード3件のはずが{len(cards)}件"
    assert len(subs) == 10, f"livetxt: 交代10件のはずが{len(subs)}件"
    print("OK: sample_match_livetxt.html の得点/カード/交代の件数を確認")

    chunks_r = extract_next_chunks(review.read_text(encoding="utf-8"))
    goals_r = find_goals(chunks_r)
    assert len(goals_r) == 5, f"review: 得点5件のはずが{len(goals_r)}件"
    print("OK: sample_match_review.html の得点件数を確認")

    for label, chunks_x in [("livetxt", chunks), ("review", chunks_r)]:
        formations = find_formations(chunks_x)
        assert formations and len(formations) == 1, f"{label}: formationsが1件取れるはず"
        home, away = formations[0]["homeTeam"], formations[0]["awayTeam"]
        assert len(home["players"]) == 11 and len(away["players"]) == 11, f"{label}: 出場メンバーは11人ずつのはず"
        assert home.get("formation") and away.get("formation"), f"{label}: フォーメーション文字列が空"
    print("OK: 出場メンバー(スタメン11人・フォーメーション)をlivetxt/review両方で確認")

    chunks_s = extract_next_chunks(schedule.read_text(encoding="utf-8"))
    entries = extract_schedule_index(chunks_s)
    assert len(entries) == 10, f"schedule: 10試合のはずが{len(entries)}件"
    codes = {e["code"]: (e["home"], e["away"]) for e in entries}
    assert codes.get("082217") == ("テゲバジャーロ宮崎", "湘南ベルマーレ"), codes.get("082217")
    print("OK: sample_match_schedule.html の対戦カード<->コード対応を確認")


if __name__ == "__main__":
    test_pick_candidates_window()
    test_parse_and_merge_regression_guard()
    test_lineups_carry_forward_when_missing()
    test_parsers_against_samples()
    print("\n全テスト完了")
