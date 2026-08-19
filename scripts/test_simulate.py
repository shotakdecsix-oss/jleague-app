"""
simulate.py の検証。ネットワーク不要、小さな合成データのみ(実データの380試合を毎回回すと遅いため)。

実行方法:
    python scripts/test_simulate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simulate import compute_league_stats, compute_ratings, mode_rank_from_counts, run_simulation, seed_all_teams  # noqa: E402
from standings import build_records  # noqa: E402


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
PROMOTION_RULES = {"autoPromotion": [1], "playoff": [2, 3], "relegation": [4]}


def test_same_seed_gives_identical_result() -> None:
    """同じseedで2回実行すると完全に同じ結果になること。"""
    matches = [
        M("A", 2, "B", 1),
        M("C", 1, "D", 1),
        M("A", None, "C", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("B", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("A", None, "D", None, finished=False, kickoff="2026-02-08T10:00:00+09:00"),
    ]
    r1 = run_simulation(matches, TEAMS_4, PROMOTION_RULES, trials=300, seed=42, progress=False)
    r2 = run_simulation(matches, TEAMS_4, PROMOTION_RULES, trials=300, seed=42, progress=False)

    assert r1["leagueAvgGoals"] == r2["leagueAvgGoals"]
    assert r1["homeAdvantage"] == r2["homeAdvantage"]
    for t1, t2 in zip(r1["teams"], r2["teams"]):
        assert t1["idTeam"] == t2["idTeam"]
        assert t1["expectedPoints"] == t2["expectedPoints"], "同じseedなら勝点期待値もビット単位で一致するはず"
        assert t1["rankDistribution"] == t2["rankDistribution"]
        assert t1["autoPromotion"] == t2["autoPromotion"]
        assert t1["champion"] == t2["champion"]
        assert t1["medianRank"] == t2["medianRank"]
        assert t1["modeRank"] == t2["modeRank"]
        assert t1["modeRankProb"] == t2["modeRankProb"]
    print("OK: 同じseedで2回実行すると完全に同じ結果になる")


def test_rank_distribution_sums_to_one_per_team() -> None:
    """全クラブのrankDistributionの合計がそれぞれ1.0(誤差1e-9以内)になること。"""
    matches = [
        M("A", 2, "B", 1),
        M("C", 1, "D", 1),
        M("A", None, "C", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("B", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
    ]
    r = run_simulation(matches, TEAMS_4, PROMOTION_RULES, trials=500, seed=7, progress=False)
    for t in r["teams"]:
        s = sum(t["rankDistribution"])
        assert abs(s - 1.0) < 1e-9, f"{t['idTeam']}のrankDistribution合計が1.0からずれている: {s}"
        assert len(t["rankDistribution"]) == len(TEAMS_4)
    print("OK: 全クラブのrankDistribution合計が1.0(誤差1e-9以内)")


def test_per_rank_sum_across_teams() -> None:
    """
    各順位について、全クラブぶんの確率を足すと1.0になること。
    ただしタイがある順位では「タイのときは全員がその順位」という設計なので1.0を超える
    (そして、その分だけ別の順位が0になる。順位の総和は常にクラブ数と一致する)。
    ここではその両方を確定的なデータ(pending試合0件)で明示する。
    """
    # --- ケース1: タイ無し(pending試合0件、finishedのみで完全に決着) ---
    # run_simulationはmaster_teams(ロスター)に無いチームIDが出るとKeyErrorになる
    # (本番のmatches.jsonは必ずマスタのidTeamしか含まないため)。
    # テストデータもダミーの外部チームを使わず、ロスター内(A/B/C/D)だけで組む。
    no_tie_matches = [
        M("A", 3, "B", 0),  # A: pts3,gd3,gf3(1位) / B: pts0,gd-3,gf0(4位候補)
        M("C", 1, "D", 0),  # C: pts3,gd1,gf1(2位) / D: pts0,gd-1,gf0(3位候補)
    ]
    # 順位: A(3,3,3) > C(3,1,1) > D(0,-1,0) > B(0,-3,0) の完全な単独順位になる
    r_no_tie = run_simulation(no_tie_matches, TEAMS_4, None, trials=50, seed=1, progress=False)
    n = len(TEAMS_4)
    for rank_idx in range(n):
        total = sum(t["rankDistribution"][rank_idx] for t in r_no_tie["teams"])
        assert abs(total - 1.0) < 1e-9, f"タイ無しなら各順位の合計は1.0のはず(rank={rank_idx + 1}): {total}"
    print("OK: タイが無いケースでは各順位の全クラブ合計が1.0")

    # --- ケース2: A・Bが完全タイ、C・Dも完全タイ(いずれも直接対決なし)、pending試合0件 ---
    # ロスター内(A/B/C/D)だけで、A-Bを対戦させずに(=直接対決0試合を作る)タイを再現する。
    tie_matches = [
        M("A", 2, "C", 0),  # A: pts3,gd2,gf2 / C: pts0,gd-2,gf0
        M("B", 2, "D", 0),  # B: pts3,gd2,gf2(Aと完全タイ、直接対決なし) / D: pts0,gd-2,gf0(Cと完全タイ、直接対決なし)
    ]
    r_tie = run_simulation(tie_matches, TEAMS_4, None, trials=50, seed=1, progress=False)
    # 順位: A/B=1位タイ(2位は誰もいない), C/D=3位タイ(4位は誰もいない)
    rank1_total = sum(t["rankDistribution"][0] for t in r_tie["teams"])
    rank2_total = sum(t["rankDistribution"][1] for t in r_tie["teams"])
    rank3_total = sum(t["rankDistribution"][2] for t in r_tie["teams"])
    rank4_total = sum(t["rankDistribution"][3] for t in r_tie["teams"])
    assert abs(rank1_total - 2.0) < 1e-9, f"1位はA/Bタイで2.0になるはず(仕様通り): {rank1_total}"
    assert abs(rank2_total - 0.0) < 1e-9, f"1位に2クラブいるので2位は誰も居らず0.0のはず: {rank2_total}"
    assert abs(rank3_total - 2.0) < 1e-9, f"3位はC/Dタイで2.0になるはず: {rank3_total}"
    assert abs(rank4_total - 0.0) < 1e-9, f"3位に2クラブいるので4位は誰も居らず0.0のはず: {rank4_total}"
    # 個々のクラブのrankDistribution合計は(タイの有無に関わらず)必ず1.0
    for t in r_tie["teams"]:
        assert abs(sum(t["rankDistribution"]) - 1.0) < 1e-9
    print("OK: タイがある順位は合計が1.0を超える(ここでは2.0)ことを明示。個々のクラブ単位では変わらず1.0")


def test_zero_finished_matches_gives_neutral_ratings() -> None:
    """消化0試合の人工データを与えると、全クラブのattackRating/defenseRatingが1.0になること。"""
    matches = [
        M("A", None, "B", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("C", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
    ]
    r = run_simulation(matches, TEAMS_4, PROMOTION_RULES, trials=1, seed=1, progress=False)
    for t in r["teams"]:
        assert t["attackRating"] == 1.0, f"{t['idTeam']}のattackRatingは1.0のはず: {t['attackRating']}"
        assert t["defenseRating"] == 1.0, f"{t['idTeam']}のdefenseRatingは1.0のはず: {t['defenseRating']}"
    assert r["leagueAvgGoals"] > 0, "0試合時のフォールバック値が使われているはず"
    print("OK: 消化0試合ならattackRating/defenseRatingは全クラブ1.0")

    # compute_ratings単体でも確認(縮約の境界ケース)
    records = seed_all_teams(build_records([]), TEAMS_4)
    league_avg, hfa = compute_league_stats([])
    ratings = compute_ratings(records, league_avg)
    assert all(a == 1.0 and d == 1.0 for a, d in ratings.values())
    print("OK: compute_ratings単体でもn_i=0のクラブは(1.0, 1.0)")


def test_auto_promotion_plus_playoff_does_not_exceed_one() -> None:
    """autoPromotion + playoff が1.0を超えないこと(昇格圏とPO圏は排他)。"""
    matches = [
        M("A", 2, "B", 1),
        M("C", 1, "D", 1),
        M("A", None, "C", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("B", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("A", None, "D", None, finished=False, kickoff="2026-02-08T10:00:00+09:00"),
        M("B", None, "C", None, finished=False, kickoff="2026-02-08T10:00:00+09:00"),
    ]
    r = run_simulation(matches, TEAMS_4, PROMOTION_RULES, trials=500, seed=99, progress=False)
    for t in r["teams"]:
        total = (t["autoPromotion"] or 0) + (t["playoff"] or 0)
        assert total <= 1.0 + 1e-9, f"{t['idTeam']}: autoPromotion+playoffが1.0を超えている: {total}"
        assert 0.0 <= (t["relegation"] or 0) <= 1.0
    print("OK: autoPromotion + playoff は全クラブで1.0を超えない")


def test_no_promotion_rules_gives_null_zones() -> None:
    """promotionRulesが無いリーグ(J1・J3相当)ではautoPromotion/playoff/relegationがnullになること。"""
    matches = [
        M("A", 2, "B", 1),
        M("A", None, "C", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
    ]
    r = run_simulation(matches, TEAMS_4, None, trials=20, seed=1, progress=False)
    for t in r["teams"]:
        assert t["champion"] is None
        assert t["autoPromotion"] is None
        assert t["playoff"] is None
        assert t["relegation"] is None
        assert t["expectedRank"] is not None
        assert t["expectedPoints"] is not None
        # promotionRulesが無くても「予想順位」ブロックに必要な指標は必ず出る
        assert t["medianRank"] is not None
        assert t["rankP10"] is not None
        assert t["rankP90"] is not None
        assert t["modeRank"] is not None
        assert t["modeRankProb"] is not None
    print("OK: promotionRules無しのリーグではchampion/autoPromotion/playoff/relegationがnull")


def test_champion_zone_without_auto_promotion() -> None:
    """
    J1のように autoPromotion が無く champion だけのルールでも、championキーを走査して
    正しく確率化されること(simulate.py側がautoPromotion決め打ちをやめたことの確認)。
    """
    matches = [
        M("A", 2, "B", 1),
        M("C", 1, "D", 1),
        M("A", None, "C", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("B", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
    ]
    rules = {"champion": [1], "relegation": [4]}
    r = run_simulation(matches, TEAMS_4, rules, trials=300, seed=3, progress=False)
    for t in r["teams"]:
        assert t["autoPromotion"] is None, "autoPromotionキーが無いルールではnullのまま"
        assert t["playoff"] is None
        assert t["champion"] is not None and 0.0 <= t["champion"] <= 1.0
        assert t["relegation"] is not None and 0.0 <= t["relegation"] <= 1.0
    # 1位タイの試行があり得るため、championの合計は1.0以上になりうる(rankDistributionの
    # 同順位合計と同じ理屈。test_per_rank_sum_across_teamsを参照)。ここでは0より大きいことだけ確認する。
    total_champion = sum(t["champion"] for t in r["teams"])
    assert total_champion >= 1.0 - 1e-9, total_champion
    print("OK: autoPromotion無し・championのみのルールでもchampion確率が正しく出る")


def test_mode_rank_matches_rank_distribution_argmax() -> None:
    """
    modeRankが、rankDistribution(確率の配列)の最大値の位置と一致すること。
    実装(mode_rank_from_counts)を使わず、rankDistributionから素朴に読み直して照合する
    (同じロジックを2回書いて自己一致するだけのテストにならないようにするため)。
    """
    matches = [
        M("A", 2, "B", 1),
        M("C", 1, "D", 1),
        M("A", None, "C", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("B", None, "D", None, finished=False, kickoff="2026-02-01T10:00:00+09:00"),
        M("A", None, "D", None, finished=False, kickoff="2026-02-08T10:00:00+09:00"),
        M("B", None, "C", None, finished=False, kickoff="2026-02-08T10:00:00+09:00"),
    ]
    r = run_simulation(matches, TEAMS_4, PROMOTION_RULES, trials=2000, seed=5, progress=False)
    for t in r["teams"]:
        dist = t["rankDistribution"]
        naive_max_prob = max(dist)
        # 最大値を取る順位が複数ありうるので、modeRankが「最大値のどれか」であることを確認する
        assert dist[t["modeRank"] - 1] == naive_max_prob, t
        assert abs(t["modeRankProb"] - naive_max_prob) < 1e-9, t
        assert t["rankP10"] <= t["medianRank"] <= t["rankP90"], t
    print("OK: modeRankはrankDistributionの最大値の位置と一致する")


def test_mode_rank_tie_break_prefers_expected_rank() -> None:
    """
    mode_rank_from_counts単体のテスト。最大値が複数の順位で同値のときは、
    expected_rankに近いほうを選ぶこと(add-projected-table-rev2.mdの明示的な仕様)。
    """
    # 1位と4位がどちらもカウント5で同率最大。expected_rankが1位寄りなら1位を選ぶはず。
    counts = [5, 1, 1, 5]
    rank, count = mode_rank_from_counts(counts, expected_rank=1.5)
    assert rank == 1 and count == 5, (rank, count)

    # expected_rankが4位寄りなら4位を選ぶはず。
    rank2, count2 = mode_rank_from_counts(counts, expected_rank=3.5)
    assert rank2 == 4 and count2 == 5, (rank2, count2)

    # タイが無い素直なケースでは単純に最大値の順位を返す。
    rank3, count3 = mode_rank_from_counts([1, 2, 8, 1], expected_rank=3.0)
    assert rank3 == 3 and count3 == 8, (rank3, count3)
    print("OK: 最頻値が複数順位で同率のときはexpected_rankに近いほうを選ぶ")


def test_percentile_rank_matches_deterministic_distribution() -> None:
    """
    pending試合0件(=1trialで結果が完全に決まる)なら、medianRank/rankP10/rankP90は
    全て実際の最終順位と一致すること(分布に幅が無いケースの境界確認)。
    """
    matches = [
        M("A", 3, "B", 0),  # A: pts3,gd3 (1位) / B: pts0,gd-3 (4位)
        M("C", 1, "D", 0),  # C: pts3,gd1 (2位) / D: pts0,gd-1 (3位)
    ]
    r = run_simulation(matches, TEAMS_4, None, trials=1, seed=1, progress=False)
    by_id = {t["idTeam"]: t for t in r["teams"]}
    expected_rank_of = {"A": 1, "C": 2, "D": 3, "B": 4}
    for tid, rank in expected_rank_of.items():
        t = by_id[tid]
        assert t["medianRank"] == rank, t
        assert t["rankP10"] == rank, t
        assert t["rankP90"] == rank, t
    print("OK: 決定的な分布ではmedianRank/rankP10/rankP90が全て実際の順位と一致する")


def main() -> None:
    tests = [
        test_same_seed_gives_identical_result,
        test_rank_distribution_sums_to_one_per_team,
        test_per_rank_sum_across_teams,
        test_zero_finished_matches_gives_neutral_ratings,
        test_auto_promotion_plus_playoff_does_not_exceed_one,
        test_no_promotion_rules_gives_null_zones,
        test_champion_zone_without_auto_promotion,
        test_mode_rank_matches_rank_distribution_argmax,
        test_mode_rank_tie_break_prefers_expected_rank,
        test_percentile_rank_matches_deterministic_distribution,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
