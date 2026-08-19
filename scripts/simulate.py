"""
昇格確率のモンテカルロ・シミュレーション。

data/processed/{league}_matches.json を読み、未消化の試合をポアソン分布で
何度もシミュレーションして最終順位の分布を出す。

モデルの限界(必ず理解した上で使うこと):
  得点は独立ポアソン分布と仮定している。実際のサッカーでは0-0や1-1のような
  低スコアの引き分けは独立仮定より起きやすい(Dixon-Coles補正なし)。
  ここではスコアだけしか無いデータでできる範囲の近似にとどめている。
  「確率のニュアンス」を掴むためのものであり、精密な予測ではない。

順位決定ロジックは standings.py の build_records()/rank_teams() をそのまま再利用する。
二重実装しない(モンテカルロ側だけ別のタイブレーク仕様になるバグを避けるため)。

CLI:
    python scripts/simulate.py --league j2
    python scripts/simulate.py --league j2 --trials 100 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poisson_model import (  # noqa: E402
    compute_league_stats,
    compute_ratings,
    expected_goals,
    poisson,
    seed_all_teams,
)
from standings import build_records, rank_teams  # noqa: E402
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

MASTER_FILES = {
    "j1": MASTERS_DIR / "j1_teams_2026-27.json",
    "j2": MASTERS_DIR / "j2_master_2026-27.json",
    "j3": MASTERS_DIR / "j3_teams_2026-27.json",
}

DEFAULT_TRIALS = 10000
DEFAULT_SEED = 42

# promotionRulesの区分キー。値が無い(null/キー無し)区分は結果からnullで返す。
# リーグ名やラベル文字列では判定しない(将来リーグの区分が変わっても壊れないように、キーの有無だけで判定する)。
ZONE_KEYS = ["champion", "autoPromotion", "playoff", "relegation"]


def zone_rank_sets(promotion_rules: dict | None) -> dict[str, set[int]]:
    """promotionRulesから、区分名 -> 順位集合 の辞書を作る。値がnull/キー無しの区分は含めない。"""
    if not promotion_rules:
        return {}
    zones: dict[str, set[int]] = {}
    for key in ZONE_KEYS:
        ranks = promotion_rules.get(key)
        if ranks:
            zones[key] = set(ranks)
    return zones


def percentile_rank(counts: list[int], trials: int, p: float) -> int:
    """
    順位ごとの試行回数配列(counts[0]=1位の回数, ...)から、p分位点の順位(整数)を求める。
    累積試行回数が最初に trials*p 以上になった順位を返す(離散分布のパーセンタイル)。
    """
    threshold = p * trials
    cum = 0
    for i, c in enumerate(counts):
        cum += c
        if cum >= threshold:
            return i + 1
    return len(counts)


def mode_rank_from_counts(counts: list[int], expected_rank: float) -> tuple[int, int]:
    """
    rankDistribution(counts, 0-indexed)の最頻値(=最もあり得る最終順位)を求める。
    最大値を取る順位が複数あるとき(タイ)は、expected_rank(平均順位)に近いほうを採用する。
    戻り値は (1-indexedの順位, その順位になった試行回数)。
    """
    max_count = max(counts)
    candidates = [i for i, c in enumerate(counts) if c == max_count]  # 0-indexed
    best = min(candidates, key=lambda i: abs((i + 1) - expected_rank))
    return best + 1, counts[best]


def rank_groups_to_rank_of(groups: list[list[str]]) -> dict[str, int]:
    """rank_teams()の出力(タイのグループ列)を idTeam -> 競技順位(1,1,3,...) に変換する。"""
    rank_of: dict[str, int] = {}
    r = 1
    for group in groups:
        for tid in group:
            rank_of[tid] = r
        r += len(group)
    return rank_of


def run_simulation(
    matches: list[dict],
    master_teams: list[dict],
    promotion_rules: dict | None,
    trials: int,
    seed: int,
    progress: bool = True,
) -> dict:
    finished_matches = [m for m in matches if m.get("finished")]
    pending_matches = [m for m in matches if not m.get("finished")]

    league_avg_goals, hfa = compute_league_stats(finished_matches)

    current_records = seed_all_teams(build_records(finished_matches), master_teams)
    ratings = compute_ratings(current_records, league_avg_goals)

    current_groups = rank_teams(current_records, finished_matches)
    current_rank_of = rank_groups_to_rank_of(current_groups)

    all_ids = [t["idTeam"] for t in master_teams]
    n_teams = len(all_ids)

    rank_counts: dict[str, list[int]] = {tid: [0] * n_teams for tid in all_ids}
    points_sum: dict[str, float] = {tid: 0.0 for tid in all_ids}
    zones = zone_rank_sets(promotion_rules)
    zone_counts: dict[str, dict[str, int]] | None = None
    if zones:
        zone_counts = {tid: {key: 0 for key in zones} for tid in all_ids}

    rnd = random.Random(seed)

    for trial in range(1, trials + 1):
        sim_matches = list(finished_matches)
        for m in pending_matches:
            home_id, away_id = m["home"]["idTeam"], m["away"]["idTeam"]
            atk_h, def_h = ratings[home_id]
            atk_a, def_a = ratings[away_id]
            lam_h, lam_a = expected_goals(atk_h, def_a, atk_a, def_h, league_avg_goals, hfa)
            hs = poisson(lam_h, rnd)
            as_ = poisson(lam_a, rnd)
            sim_matches.append({
                "finished": True,
                "kickoffJst": m.get("kickoffJst"),
                "home": {"idTeam": home_id, "score": hs},
                "away": {"idTeam": away_id, "score": as_},
            })

        sim_records = seed_all_teams(build_records(sim_matches), master_teams)
        groups = rank_teams(sim_records, sim_matches)

        r = 1
        for group in groups:
            for tid in group:
                rank_counts[tid][r - 1] += 1
                points_sum[tid] += sim_records[tid].points
                if zone_counts is not None:
                    for key, ranks in zones.items():
                        if r in ranks:
                            zone_counts[tid][key] += 1
            r += len(group)

        if progress and trial % 1000 == 0:
            print(f"[info] {trial}/{trials}", file=sys.stderr)

    # 注意: ここでは丸めない(round()はJSON出力直前のmain()側だけでやる)。
    # rankDistributionを丸めてから合計を検算すると1e-9以内には収まらなくなるため、
    # 生の値をそのまま返す。テスト(test_simulate.py)もこの生の値を検証する。
    def zone_prob(tid: str, key: str) -> float | None:
        if zone_counts is None or key not in zone_counts[tid]:
            return None
        return zone_counts[tid][key] / trials

    team_lookup = {t["idTeam"]: t for t in master_teams}
    teams_out = []
    for tid in all_ids:
        counts = rank_counts[tid]
        rank_distribution = [c / trials for c in counts]
        expected_rank = sum((i + 1) * c for i, c in enumerate(counts)) / trials
        mode_rank, mode_count = mode_rank_from_counts(counts, expected_rank)
        atk, de = ratings[tid]
        info = team_lookup.get(tid, {})
        entry = {
            "idTeam": tid,
            "ja": info.get("ja", ""),
            "short": info.get("short", ""),
            "currentRank": current_rank_of.get(tid),
            "expectedPoints": points_sum[tid] / trials,
            "expectedRank": expected_rank,
            "medianRank": percentile_rank(counts, trials, 0.5),
            "rankP10": percentile_rank(counts, trials, 0.10),
            "rankP90": percentile_rank(counts, trials, 0.90),
            "modeRank": mode_rank,
            "modeRankProb": mode_count / trials,
            "champion": zone_prob(tid, "champion"),
            "autoPromotion": zone_prob(tid, "autoPromotion"),
            "playoff": zone_prob(tid, "playoff"),
            "relegation": zone_prob(tid, "relegation"),
            "rankDistribution": rank_distribution,
            "attackRating": atk,
            "defenseRating": de,
        }
        teams_out.append(entry)

    teams_out.sort(key=lambda t: t["expectedRank"])

    return {
        "trials": trials,
        "seed": seed,
        "basedOnMatches": len(finished_matches),
        "remainingMatches": len(pending_matches),
        "leagueAvgGoals": league_avg_goals,
        "homeAdvantage": hfa,
        "teams": teams_out,
    }


def load_matches(league: str) -> dict:
    path = PROCESSED_DIR / f"{league}_matches.json"
    if not path.exists():
        print(f"[error] {path} が無い。先に fetch_batch.py --league {league} を実行すること", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def load_master(league: str) -> dict:
    return json.loads(MASTER_FILES[league].read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="昇格確率モンテカルロ・シミュレーション")
    parser.add_argument("--league", choices=["j1", "j2", "j3"], required=True)
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--quiet", action="store_true", help="進捗ログを出さない")
    args = parser.parse_args()

    matches_data = load_matches(args.league)
    master = load_master(args.league)
    master_teams = master["teams"]
    promotion_rules = master.get("promotionRules")

    t0 = time.time()
    result = run_simulation(
        matches_data["matches"], master_teams, promotion_rules,
        trials=args.trials, seed=args.seed, progress=not args.quiet,
    )
    elapsed = time.time() - t0

    def r_or_none(x, ndigits):
        return None if x is None else round(x, ndigits)

    teams_rounded = []
    for t in result["teams"]:
        teams_rounded.append({
            "idTeam": t["idTeam"],
            "ja": t["ja"],
            "short": t["short"],
            "currentRank": t["currentRank"],
            "expectedPoints": round(t["expectedPoints"], 1),
            "expectedRank": round(t["expectedRank"], 2),
            "medianRank": t["medianRank"],
            "rankP10": t["rankP10"],
            "rankP90": t["rankP90"],
            "modeRank": t["modeRank"],
            "modeRankProb": round(t["modeRankProb"], 3),
            "champion": r_or_none(t["champion"], 3),
            "autoPromotion": r_or_none(t["autoPromotion"], 3),
            "playoff": r_or_none(t["playoff"], 3),
            "relegation": r_or_none(t["relegation"], 3),
            "rankDistribution": [round(p, 4) for p in t["rankDistribution"]],
            "attackRating": round(t["attackRating"], 2),
            "defenseRating": round(t["defenseRating"], 2),
        })

    out = {
        "meta": {
            "league": args.league,
            "season": matches_data.get("meta", {}).get("season", "2026-2027"),
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "trials": result["trials"],
            "seed": result["seed"],
            "basedOnMatches": result["basedOnMatches"],
            "remainingMatches": result["remainingMatches"],
            "leagueAvgGoals": round(result["leagueAvgGoals"], 3),
            "homeAdvantage": round(result["homeAdvantage"], 3),
            "modelNote": (
                "得点は独立ポアソン。Dixon-Coles補正なし。序盤は縮約(K=6)によりリーグ平均に強く寄る"
            ),
        },
        "teams": teams_rounded,
    }

    out_path = PROCESSED_DIR / f"{args.league}_simulation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[info] {out_path} に書き出し "
        f"(trials={args.trials}, 所要{elapsed:.1f}秒, 消化{result['basedOnMatches']}試合/残り{result['remainingMatches']}試合)"
    )


if __name__ == "__main__":
    main()
