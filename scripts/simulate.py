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

data/history/{league}_probability_history.json への追記もここで行う(第8弾A)。
別スクリプトに分けず、simulation.jsonを書いた直後にこのプロセス内で完結させる
(update_all.pyの手順を増やさずに済むため)。--no-historyで無効化できる。

他会場インパクト(第8弾B)もここで行う。「各試合×勝分敗ごとに個別のシミュレーションを回す」実装は
しない(10試合×3通り×10000試行が非現実的なため)。既存の10000試行ループを1回だけ回しながら、
対象試合それぞれの当該試行での結果(H/D/A)を見て、その結果別に各クラブの区分到達回数を数える
(counts[試合][結果][クラブ][区分])。これにより同時進行する他会場結果同士の相関が自然に保たれる
(条件付き確率としても正しい)。「次節」はroundでは括らない(延期でroundが実際の日付とずれるため)。
未消化試合のうち最も早いkickoffJstを基準に、72時間以内にキックオフする試合(kickoffTbdは除く)を
対象とする。data/processed/{league}_impact.json に書き出す。

CLI:
    python scripts/simulate.py --league j2
    python scripts/simulate.py --league j2 --trials 100 --seed 42
    python scripts/simulate.py --league j2 --no-history
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta
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
HISTORY_DIR = BASE_DIR / "data" / "history"
HISTORY_SCHEMA_VERSION = 1

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

# 他会場インパクト(第8弾B)の「次節」判定パラメータ
IMPACT_WINDOW_HOURS = 72
IMPACT_MAX_MATCHES = 12
IMPACT_NOTE = "確率差は0.5ポイント刻みの目安。層別により1条件あたり約3000試行のため誤差±1ポイント程度。"


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


def find_impact_window(pending_matches: list[dict], max_matches: int = IMPACT_MAX_MATCHES) -> list[dict]:
    """
    他会場インパクトの対象となる「次節」試合群を求める。roundの値では括らない
    (延期でroundが実際のキックオフ日とずれるため。J3琉球vs北九州の延期が実例)。
    未消化試合のうち最も早いkickoffJstをアンカーとし、そこから72時間以内にキックオフする試合を対象とする。
    kickoffTbd(時刻未定)の試合はアンカー候補にも対象にも含めない。中断期明け等の過密日程で
    対象が膨れすぎないよう、max_matchesで上限を切る。
    """
    candidates = [
        m for m in pending_matches
        if m.get("kickoffJst") and not m.get("kickoffTbd") and m.get("idEvent")
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda m: m["kickoffJst"])
    anchor = datetime.fromisoformat(candidates[0]["kickoffJst"])
    window_end = anchor + timedelta(hours=IMPACT_WINDOW_HOURS)
    window = [m for m in candidates if datetime.fromisoformat(m["kickoffJst"]) <= window_end]
    return window[:max_matches]


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
    impact_matches: list[dict] | None = None,
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

    # 他会場インパクト(第8弾B-1): 「各試合×勝分敗ごとに個別シミュレーションを回す」のではなく、
    # 既存のこの1回のループの中で、対象試合の当該試行での結果(H/D/A)ごとに区分到達回数を数える
    # (層別集計)。メモリは対象試合数×3通り×クラブ数×区分数程度で一定、1パスで完結する。
    impact_ids = [m["idEvent"] for m in (impact_matches or [])]
    impact_trial_counts: dict[str, dict[str, int]] = {eid: {"H": 0, "D": 0, "A": 0} for eid in impact_ids}
    impact_zone_counts: dict[str, dict[str, dict[str, dict[str, int]]]] = {
        eid: {o: {tid: {key: 0 for key in zones} for tid in all_ids} for o in ("H", "D", "A")}
        for eid in impact_ids
    }

    rnd = random.Random(seed)

    for trial in range(1, trials + 1):
        sim_matches = list(finished_matches)
        trial_outcomes: dict[str, str] = {}
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

            eid = m.get("idEvent")
            if eid in impact_trial_counts:
                outcome = "H" if hs > as_ else ("A" if hs < as_ else "D")
                trial_outcomes[eid] = outcome
                impact_trial_counts[eid][outcome] += 1

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
                            for eid, outcome in trial_outcomes.items():
                                impact_zone_counts[eid][outcome][tid][key] += 1
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
            "currentPoints": current_records[tid].points,
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

    impact_out = []
    for m in (impact_matches or []):
        eid = m["idEvent"]
        tc = impact_trial_counts[eid]
        conditional = {}
        for tid in all_ids:
            conditional[tid] = {}
            for outcome in ("H", "D", "A"):
                n = tc[outcome]
                if n == 0:
                    conditional[tid][outcome] = {key: None for key in zones}
                else:
                    conditional[tid][outcome] = {
                        key: impact_zone_counts[eid][outcome][tid][key] / n for key in zones
                    }
        impact_out.append({
            "idEvent": eid,
            "kickoffJst": m.get("kickoffJst"),
            "home": {"idTeam": m["home"]["idTeam"], "short": team_lookup.get(m["home"]["idTeam"], {}).get("short", "")},
            "away": {"idTeam": m["away"]["idTeam"], "short": team_lookup.get(m["away"]["idTeam"], {}).get("short", "")},
            "trialCounts": tc,
            "conditional": conditional,
        })

    return {
        "trials": trials,
        "seed": seed,
        "basedOnMatches": len(finished_matches),
        "remainingMatches": len(pending_matches),
        "leagueAvgGoals": league_avg_goals,
        "homeAdvantage": hfa,
        "teams": teams_out,
        "impactMatches": impact_out,
        "impactZoneKeys": list(zones.keys()),
        "baselineByTeam": {tid: {key: zone_counts[tid][key] / trials if zone_counts else None for key in zones} for tid in all_ids},
    }


def load_history(league: str) -> dict:
    """既存の確率推移履歴を読む。無ければ空の骨組みを返す(初回実行)。"""
    path = HISTORY_DIR / f"{league}_probability_history.json"
    if not path.exists():
        return {"meta": {}, "dates": [], "basedOnMatches": [], "teams": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def append_history_snapshot(
    history: dict,
    league: str,
    season: str,
    snapshot_date: str,
    based_on_matches: int,
    teams: list[dict],
    zone_keys_present: list[str],
    updated_at_jst: str,
) -> dict:
    """
    履歴(列指向)に1スナップショットぶんを追記する。historyは呼び出し元のload_history()の戻り値を
    そのまま渡し、このまま書き換えて返す(純粋関数ではないが、ファイルI/Oはしないのでテストしやすい)。

    - snapshot_dateが既存の最終日付と同じなら、その日のインデックスを上書きする(appendしない)。
      バッチが1日に複数回走っても同日に何点も打たれないようにするため。
    - 新しいチーム/新しい日付のどちらであっても、まず全チームの配列をn_dates長にnullパディングしてから
      今回のチームぶんだけ値を書き込む、という2段構えにすることで特別扱いを減らしている。
    - zone_keys_present に無いゾーン(例: J1のautoPromotion)は、そのリーグの全チームでキーごと省略する。
    """
    dates = history.setdefault("dates", [])
    based = history.setdefault("basedOnMatches", [])
    team_hist = history.setdefault("teams", {})

    if dates and dates[-1] == snapshot_date:
        idx = len(dates) - 1
        based[idx] = based_on_matches
    else:
        dates.append(snapshot_date)
        based.append(based_on_matches)
        idx = len(dates) - 1

    n_dates = len(dates)
    metric_keys = ["rank", "points", "expectedPoints"] + list(zone_keys_present)

    # 既存の全チーム(今回のteamsに含まれないものも含む)の配列を、まず日付数ぶんnullパディングする。
    # これにより「新しい日付が増えた」ケースが自然に処理される(既存チームは末尾にnullが足される)。
    for entry in team_hist.values():
        for key in metric_keys:
            arr = entry.setdefault(key, [])
            while len(arr) < n_dates:
                arr.append(None)

    # 今回のチームぶんだけ、その日のインデックスに実際の値を書き込む。
    # 新規チーム(team_histにまだ無い)は、ここでn_dates長の全nullエントリが作られてから値が入るので、
    # 結果として「先頭からidxの手前まではnull、idxだけ値あり」という先頭パディングになる。
    for t in teams:
        tid = t["idTeam"]
        entry = team_hist.get(tid)
        if entry is None:
            entry = {"ja": t.get("ja", ""), "short": t.get("short", "")}
            for key in metric_keys:
                entry[key] = [None] * n_dates
            team_hist[tid] = entry
        else:
            entry["ja"] = t.get("ja", entry.get("ja", ""))
            entry["short"] = t.get("short", entry.get("short", ""))

        entry["rank"][idx] = t.get("currentRank")
        entry["points"][idx] = t.get("currentPoints")
        entry["expectedPoints"][idx] = t.get("expectedPoints")
        for key in zone_keys_present:
            entry[key][idx] = t.get(key)

    history["meta"] = {
        "league": league,
        "season": season,
        "schemaVersion": HISTORY_SCHEMA_VERSION,
        "updatedAtJst": updated_at_jst,
    }
    return history


def save_history(league: str, history: dict) -> Path:
    path = HISTORY_DIR / f"{league}_probability_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


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
    parser.add_argument("--no-history", action="store_true", help="data/history/の確率推移履歴を書かない(テスト・アドホック実行用)")
    args = parser.parse_args()

    matches_data = load_matches(args.league)
    master = load_master(args.league)
    master_teams = master["teams"]
    promotion_rules = master.get("promotionRules")

    pending_matches = [m for m in matches_data["matches"] if not m.get("finished")]
    impact_window = find_impact_window(pending_matches)

    t0 = time.time()
    result = run_simulation(
        matches_data["matches"], master_teams, promotion_rules,
        trials=args.trials, seed=args.seed, progress=not args.quiet,
        impact_matches=impact_window,
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
            "currentPoints": t["currentPoints"],
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

    zone_keys = result["impactZoneKeys"]

    def cond_rounded(c: dict | None) -> dict | None:
        if c is None:
            return None
        return {key: r_or_none(c[key], 3) for key in zone_keys}

    impact_matches_rounded = []
    for m in result["impactMatches"]:
        conditional_rounded = {
            tid: {outcome: cond_rounded(m["conditional"][tid][outcome]) for outcome in ("H", "D", "A")}
            for tid in m["conditional"]
        }
        impact_matches_rounded.append({
            "idEvent": m["idEvent"],
            "kickoffJst": m["kickoffJst"],
            "home": m["home"],
            "away": m["away"],
            "trialCounts": m["trialCounts"],
            "conditional": conditional_rounded,
        })

    if impact_window:
        window_start = impact_window[0]["kickoffJst"]
        window_end = (
            datetime.fromisoformat(window_start) + timedelta(hours=IMPACT_WINDOW_HOURS)
        ).isoformat(timespec="seconds")
    else:
        window_start = None
        window_end = None

    impact_out = {
        "meta": {
            "league": args.league,
            "generatedAtJst": out["meta"]["generatedAtJst"],
            "trials": args.trials,
            "windowStartJst": window_start,
            "windowEndJst": window_end,
            "note": IMPACT_NOTE,
        },
        "baseline": {
            tid: {key: r_or_none(v, 3) for key, v in probs.items()}
            for tid, probs in result["baselineByTeam"].items()
        },
        "matches": impact_matches_rounded,
    }
    impact_path = PROCESSED_DIR / f"{args.league}_impact.json"
    impact_path.write_text(json.dumps(impact_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[info] {impact_path} に書き出し (対象試合{len(impact_matches_rounded)}件)")

    if not args.no_history:
        history = load_history(args.league)
        zone_keys_present = list(zone_rank_sets(promotion_rules).keys())
        snapshot_date = out["meta"]["generatedAtJst"].split("T")[0]
        history = append_history_snapshot(
            history, args.league, out["meta"]["season"], snapshot_date,
            result["basedOnMatches"], teams_rounded, zone_keys_present,
            out["meta"]["generatedAtJst"],
        )
        hist_path = save_history(args.league, history)
        print(f"[info] {hist_path} に履歴追記 (date={snapshot_date}, dates数={len(history['dates'])})")


if __name__ == "__main__":
    main()
