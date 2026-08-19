"""
stats.py の検証。ネットワーク不要、小さな合成データのみ。

実行方法:
    python scripts/test_stats.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import build_stats  # noqa: E402


def M(home: str, hs: int, away: str, as_: int, finished: bool = True, kickoff: str | None = "2026-01-01T00:00:00+09:00") -> dict:
    return {
        "finished": finished,
        "kickoffJst": kickoff,
        "home": {"idTeam": home, "score": hs if finished else None},
        "away": {"idTeam": away, "score": as_ if finished else None},
    }


TEAMS_4 = [
    {"idTeam": "A", "ja": "Aクラブ", "short": "A"},
    {"idTeam": "B", "ja": "Bクラブ", "short": "B"},
    {"idTeam": "C", "ja": "Cクラブ", "short": "C"},
    {"idTeam": "D", "ja": "Dクラブ", "short": "D"},
]


def team(result: dict, idTeam: str) -> dict:
    return next(t for t in result["teams"] if t["idTeam"] == idTeam)


def test_same_value_gives_same_rank_and_next_is_skipped() -> None:
    """同値のクラブが同順位になり、その次が飛ぶこと(例: 1, 1, 3, 3)。"""
    # A,Bは3点ずつで同値(1位タイ)、C,Dは0点ずつで同値(3位タイ、2位は誰も居らず飛ぶ)
    matches = [
        M("A", 1, "B", 0),  # A:勝点3 / B:勝点0
        M("C", 1, "D", 0),  # C:勝点3 / D:勝点0
    ]
    result = build_stats(matches, TEAMS_4, None)
    ranks = {t["idTeam"]: t["ranks"]["points"] for t in result["teams"]}
    assert ranks["A"] == 1 and ranks["C"] == 1, ranks
    assert ranks["B"] == 3 and ranks["D"] == 3, ranks
    print("OK: 同値は同順位になり、次の順位は飛ぶ(1,1,3,3)")


def test_better_is_high_false_smallest_value_is_rank1() -> None:
    """betterIsHigh:falseの指標(ga=失点)で、値が小さいクラブが1位になること。"""
    matches = [
        M("A", 0, "B", 0),  # 引き分け: A失点0(Bの得点) / B失点0(Aの得点) -> 両者とも最小
        M("C", 5, "D", 1),  # C失点1(Dの得点) / D失点5(Cの得点) -> Dが最大
    ]
    result = build_stats(matches, TEAMS_4, None)
    ranks = {t["idTeam"]: t["ranks"]["ga"] for t in result["teams"]}
    values = {t["idTeam"]: t["values"]["ga"] for t in result["teams"]}
    assert values["A"] == 0 and values["B"] == 0
    assert ranks["A"] == 1 and ranks["B"] == 1, ranks
    assert ranks["D"] == 4, ranks  # 失点最大 = 最下位
    print("OK: betterIsHigh=falseの指標は値が小さいほうが1位になる")


def test_home_plus_away_points_equals_points() -> None:
    """homePoints + awayPoints == points が全クラブで成立すること。"""
    matches = [
        M("A", 2, "B", 1),   # A:ホーム勝ち(3) B:アウェイ負け(0)
        M("C", 0, "A", 0),   # C:ホーム分け(1) A:アウェイ分け(1)
        M("B", 3, "D", 3),   # B:ホーム分け(1) D:アウェイ分け(1)
        M("D", 1, "C", 2),   # D:ホーム負け(0) C:アウェイ勝ち(3)
    ]
    result = build_stats(matches, TEAMS_4, None)
    for t in result["teams"]:
        v = t["values"]
        assert v["homePoints"] + v["awayPoints"] == v["points"], t
    print("OK: homePoints + awayPoints は全クラブでpointsと一致する")


def test_blanks_matches_hand_computed_fixed_data() -> None:
    """blanks(無得点試合数)が手計算した固定データと一致すること。"""
    matches = [
        M("A", 0, "B", 1),  # A:無得点(1回目)
        M("A", 0, "C", 0),  # A:無得点(2回目) / C:無得点(1回目)
        M("A", 2, "D", 0),  # A:得点あり(通算blanks=2のまま)
    ]
    result = build_stats(matches, TEAMS_4, None)
    blanks = {t["idTeam"]: t["values"]["blanks"] for t in result["teams"]}
    assert blanks["A"] == 2, blanks
    assert blanks["C"] == 1, blanks
    assert blanks["B"] == 0, blanks
    print("OK: blanksが手計算した固定データと一致する")


def test_official_clean_sheet_is_passed_through_not_recomputed() -> None:
    """公式のcleanSheet(source:official)は再計算せず、club_extra.jsonの値・順位をそのまま使うこと。"""
    matches = [M("A", 1, "B", 0)]
    club_extra = {
        "A": {"clubStats": {"seasonKey": "2026-9", "items": [
            {"key": "cleanSheet", "label": "無失点試合総数", "value": 5, "rank": 1},
            {"key": "ballRate", "label": "平均ボール保持率", "value": 55.5, "rank": 2},
        ]}},
        "B": {"clubStats": {"seasonKey": "2026-9", "items": [
            {"key": "cleanSheet", "label": "無失点試合総数", "value": 2, "rank": 3},
            {"key": "ballRate", "label": "平均ボール保持率", "value": 44.4, "rank": 4},
        ]}},
    }
    result = build_stats(matches, TEAMS_4, club_extra)
    a = team(result, "A")
    assert a["values"]["cleanSheet"] == 5, a["values"]
    assert a["ranks"]["cleanSheet"] == 1, a["ranks"]
    assert a["values"]["ballRate"] == 55.5
    metric_keys = [m["key"] for m in result["metrics"]]
    assert "cleanSheet" in metric_keys and "ballRate" in metric_keys
    official_metrics = [m for m in result["metrics"] if m["source"] == "official"]
    assert len(official_metrics) == 2
    computed_keys = [m["key"] for m in result["metrics"] if m["source"] == "computed"]
    assert "cleanSheet" not in computed_keys, "cleanSheetは公式優先で自前計算しないはず"
    print("OK: 公式のcleanSheet/ballRateは再計算せずvalue/rankをそのまま使う")


def test_all_clubs_zero_played_returns_zero_without_exception() -> None:
    """全クラブplayed==0のとき、例外を投げずに全指標0(該当するもの)で返ること。"""
    matches = [
        M("A", None, "B", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("C", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
    ]
    result = build_stats(matches, TEAMS_4, None)  # 例外が出ないことそのものが重要な検証
    for t in result["teams"]:
        v = t["values"]
        assert v["played"] == 0
        assert v["points"] == 0
        assert v["gaPerGame"] == 0.0
        assert v["pointsPerGame"] == 0.0
        assert v["attackRating"] == 1.0
        assert v["defenseRating"] == 1.0
        assert v["xPoints"] == 0.0
    print("OK: 全クラブplayed==0でも例外を投げず全指標0(相当)で返る")


def test_played_zero_mixed_with_played_gt0_ranks_worst() -> None:
    """played==0のクラブが混在する場合、平均系(gaPerGame等)は最下位側にまとめて並ぶこと。"""
    matches = [
        M("A", 0, "B", 3),  # A: 失点3多く、しかしplayedあり。平均失点(betterIsHigh=false)は良くない値
        # C, Dはpending試合のみ(played=0)
        M("C", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
    ]
    result = build_stats(matches, TEAMS_4, None)
    ranks = {t["idTeam"]: t["ranks"]["gaPerGame"] for t in result["teams"]}
    # played>0のA,Bは1,2位のどちらかを分け合い、played==0のC,Dは最下位側(3位タイ)にまとまるはず
    assert ranks["C"] == ranks["D"] == 3, ranks
    assert set([ranks["A"], ranks["B"]]) <= {1, 2}, ranks
    print("OK: played==0のクラブは平均系の順位で最下位側にまとまる(0/0の見かけ好成績を防ぐ)")


def main() -> None:
    tests = [
        test_same_value_gives_same_rank_and_next_is_skipped,
        test_better_is_high_false_smallest_value_is_rank1,
        test_home_plus_away_points_equals_points,
        test_blanks_matches_hand_computed_fixed_data,
        test_official_clean_sheet_is_passed_through_not_recomputed,
        test_all_clubs_zero_played_returns_zero_without_exception,
        test_played_zero_mixed_with_played_gt0_ranks_worst,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
