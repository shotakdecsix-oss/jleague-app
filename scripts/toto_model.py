"""
くじ指定試合に、アプリが既に持っているポアソンモデルの予想を当てる。

予想の対象はリーグ戦(J1/J2/J3の同一リーグ内)だけにしている。
天皇杯・ルヴァン杯のようにリーグをまたぐ試合は予想しない:
  attackRating / defenseRating は「そのリーグの平均」に対する相対値なので、
  J1のレーティングとJ3のレーティングを同じ式に入れても意味のある数字にならない。
  リーグ間の強さ差を推定できるだけのデータが今のアプリには無いため、
  無理に数字を出さず「対象外」として扱う。

遡及シミュレーション(もし買っていたら)では ratings_as_of(cutoff=試合日) を使う。
cutoff を指定しないと「結果を全部見たあとのレーティングで過去を予想する」ことに
なり、的中率が実際より高く出る。ここを間違えると数字が嘘になるので注意。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from poisson_model import (  # noqa: E402
    compute_league_stats,
    compute_ratings,
    expected_goals,
    match_outcome_probs,
    poisson_pmf,
    seed_all_teams,
)
from standings import build_records, load_master_teams  # noqa: E402
from team_matching import match_team_ja  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED = BASE_DIR / "data" / "processed"
LEAGUES = ("j1", "j2", "j3")

MIN_FINISHED = 20   # リーグ全体でこの消化数に満たない時期は予想を出さない(開幕直後)
GOAL3_MAX = 3       # toto GOAL3 のマークは 0/1/2/3点以上 の4区分


def load_context() -> dict:
    ctx: dict = {"_allTeams": []}
    for lg in LEAGUES:
        data = json.loads((PROCESSED / f"{lg}_matches.json").read_text(encoding="utf-8"))
        master = load_master_teams(lg)
        for t in master:
            t = dict(t)
            t["_league"] = lg
            ctx["_allTeams"].append(t)
        ctx[lg] = {"matches": data["matches"], "master": master}
    return ctx


def ratings_as_of(ctx: dict, league: str, cutoff: str | None = None) -> dict | None:
    """cutoff(YYYY-MM-DD)より前に終わった試合だけでレーティングを作る。"""
    data = ctx[league]
    finished = [m for m in data["matches"] if m.get("finished")]
    if cutoff:
        finished = [m for m in finished if m["kickoffDate"] < cutoff]
    if len(finished) < MIN_FINISHED:
        return None
    records = seed_all_teams(build_records(finished), data["master"])
    avg, hfa = compute_league_stats(finished)
    return {
        "ratings": compute_ratings(records, avg),
        "avg": avg,
        "hfa": hfa,
        "basedOn": len(finished),
    }


def resolve_match(ctx: dict, date_iso: str, home_name: str, away_name: str) -> tuple[str | None, dict | None]:
    """指定試合表の1行を、アプリが持つリーグ戦の試合に対応づける。

    date_iso は YYYY-MM-DD。年まで一致させること。
    指定試合表のPDFには年が書かれていない(「9/12」としか書かれていない)ので
    月日だけで照合すると別シーズンの同月日の試合を拾う。実際、2026年4月29日の
    指定試合が2027年4月29日の試合(今季の日程)に誤マッチした。年は回号の並びから
    build_toto.assign_years() で決めている。
    """
    ht = match_team_ja(home_name, ctx["_allTeams"])
    at = match_team_ja(away_name, ctx["_allTeams"])
    if not ht or not at or ht["_league"] != at["_league"]:
        return None, None
    lg = ht["_league"]
    for m in ctx[lg]["matches"]:
        if m["kickoffDate"] != date_iso:
            continue
        if m["home"]["idTeam"] == ht["idTeam"] and m["away"]["idTeam"] == at["idTeam"]:
            return lg, m
    return None, None


def _score_dist(lam: float) -> list[float]:
    d = [poisson_pmf(k, lam) for k in range(GOAL3_MAX)]
    d.append(max(0.0, 1.0 - sum(d)))
    return [round(x, 4) for x in d]


def outcome_of(match: dict) -> str | None:
    """totoのマーク記号で結果を返す。1=ホーム勝ち / 0=引き分け / 2=アウェイ勝ち。"""
    if not match.get("finished"):
        return None
    h = match["home"].get("score")
    a = match["away"].get("score")
    if h is None or a is None:
        return None
    return "1" if h > a else ("0" if h == a else "2")


def predict(match: dict, rt: dict) -> dict | None:
    hid = match["home"]["idTeam"]
    aid = match["away"]["idTeam"]
    ratings = rt["ratings"]
    if hid not in ratings or aid not in ratings:
        return None
    atk_h, def_h = ratings[hid]
    atk_a, def_a = ratings[aid]
    lam_h, lam_a = expected_goals(atk_h, def_a, atk_a, def_h, rt["avg"], rt["hfa"])
    p_h, p_d, p_a = match_outcome_probs(lam_h, lam_a)
    # max_goals で打ち切った残余があるので合計1に正規化する(くじの確率として使うため)
    tot = p_h + p_d + p_a
    probs = {"1": p_h / tot, "0": p_d / tot, "2": p_a / tot}
    pick = max(probs, key=probs.get)
    ranked = sorted(probs.values(), reverse=True)
    return {
        "lambdaHome": round(lam_h, 3),
        "lambdaAway": round(lam_a, 3),
        "probs": {k: round(v, 4) for k, v in probs.items()},
        "pick": pick,
        "confidence": round(ranked[0], 4),
        "margin": round(ranked[0] - ranked[1], 4),   # 1番手と2番手の差。小さいほど「割れている」
        "scoreHome": _score_dist(lam_h),
        "scoreAway": _score_dist(lam_a),
        "basedOn": rt["basedOn"],
    }
