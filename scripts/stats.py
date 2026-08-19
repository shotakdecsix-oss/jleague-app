"""
スタッツ＋リーグ内順位。ネットワークアクセスなし。
data/processed/{league}_matches.json と、(あれば)data/processed/club_extra.json を読んで計算する。

方針:
- 公式サイトから取れる指標(club_extra.jsonのclubStats)は再計算せず、value/rankをそのまま使う
  (metrics/teams両方に source: "official" を持たせる)
- 公式に無い指標だけ自前で計算し、リーグ内順位も自分で付ける(source: "computed")
- club_extra.jsonが無い/対象クラブのデータが無い場合は、公式指標を出さず自前計算分だけ出力する
  (壊れない。フロント側も同じ理由でstats.json自体が無くても動くようにすること)

攻撃力/守備力レーティング・期待勝点(xPoints)は poisson_model.py の共通ロジックを使う。
simulate.py(モンテカルロ)と二重実装しない。

順位付けのルール(E-3):
- 同値は同順位、次は飛ぶ(1, 2, 2, 4)
- betterIsHighがfalseの指標は昇順で1位
- played/drawには順位を付けない(rank=null固定)
- played==0のクラブが混在する場合、平均系(gaPerGame/pointsPerGame)は0として扱ったうえで
  最下位側にまとめて並べる(0/0の見かけの好成績が上位に来るのを防ぐ)。
  全クラブplayed==0のときは、全員0の値で自然に同順位(1位)になる

CLI:
    python scripts/stats.py --league j2
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poisson_model import (  # noqa: E402
    compute_league_stats,
    compute_ratings,
    expected_goals,
    match_outcome_probs,
    seed_all_teams,
)
from standings import TeamRecord, build_records, load_master_teams  # noqa: E402
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

# (key, label, betterIsHigh, format, group)
# betterIsHigh=None は「順位を付けない」指標(played/draw)。
COMPUTED_METRICS: list[tuple[str, str, bool | None, str, str]] = [
    ("points", "勝点", True, "int", "基礎"),
    ("played", "消化", None, "int", "基礎"),
    ("win", "勝", True, "int", "基礎"),
    ("draw", "分", None, "int", "基礎"),
    ("loss", "敗", False, "int", "基礎"),
    ("gf", "得点", True, "int", "基礎"),
    ("ga", "失点", False, "int", "基礎"),
    ("gd", "得失点差", True, "signedInt", "基礎"),
    ("gaPerGame", "平均失点", False, "float2", "平均"),
    ("pointsPerGame", "平均勝点", True, "float2", "平均"),
    ("homePoints", "ホーム勝点", True, "int", "平均"),
    ("awayPoints", "アウェイ勝点", True, "int", "平均"),
    ("blanks", "無得点", False, "int", "平均"),
    ("form5Points", "直近5試合の勝点", True, "int", "平均"),
    ("attackRating", "攻撃力", True, "float2", "モデル"),
    ("defenseRating", "守備力", False, "float2", "モデル"),
    ("xPoints", "期待勝点", True, "float2", "モデル"),
    ("pointsOverX", "勝点の上振れ", True, "signed2", "モデル"),
    ("remainingDifficulty", "残り日程の難易度", False, "float2", "モデル"),
]
NO_RANK_KEYS = {"played", "draw"}
RATE_METRIC_KEYS = {"gaPerGame", "pointsPerGame"}  # played==0の特別扱い対象

# 公式スタッツの自前rank突き合わせで使うタイ処理方式。
# 実データで検証したところ、公式は基本的に同値同順位・次は飛ぶ方式(standard_rank)だが、
# cleanSheetだけ同値同順位・次は飛ばない方式(dense_rank)になっていた(60クラブ×6指標を突き合わせ、
# cleanSheetはdense_rankで全クラブ一致・standard_rankでは12〜13クラブ不一致、他の5指標は逆に
# standard_rankでほぼ完全一致した)。指標ごとに方式が違う理由は不明だが、実データにこの規則性が
# 一貫して見られたためハードコードする。
OFFICIAL_RANK_METHOD_OVERRIDE = {"cleanSheet": "dense"}


def standard_rank(values: dict[str, float], better_is_high: bool) -> dict[str, int]:
    """同値は同順位、次は飛ぶ(1, 2, 2, 4)方式。"""
    ordered = sorted(values.keys(), key=lambda tid: values[tid], reverse=better_is_high)
    ranks: dict[str, int] = {}
    prev_val = None
    prev_rank = 0
    for i, tid in enumerate(ordered, start=1):
        v = values[tid]
        if prev_val is not None and v == prev_val:
            ranks[tid] = prev_rank
        else:
            ranks[tid] = i
            prev_rank = i
        prev_val = v
    return ranks


def dense_rank(values: dict[str, float], better_is_high: bool) -> dict[str, int]:
    """
    同値は同順位、次は飛ばない(1, 1, 2, 3)方式(dense rank)。
    公式サイトのclubStats.rankがこの方式であることを実データで確認済み
    (例: cleanSheetで value=1のクラブが8つ並んでも、次のvalue=0クラブ群はrank=2になる。
    rank=9[8+1]にはならない)。自前計算指標(E-3のstandard_rank)とは意図的に方式を変えている。
    """
    ordered = sorted(values.keys(), key=lambda tid: values[tid], reverse=better_is_high)
    ranks: dict[str, int] = {}
    prev_val = None
    rank = 0
    for tid in ordered:
        v = values[tid]
        if prev_val is None or v != prev_val:
            rank += 1
        ranks[tid] = rank
        prev_val = v
    return ranks


def rank_rate_metric(records: dict[str, TeamRecord], values: dict[str, float], better_is_high: bool) -> dict[str, int]:
    """
    played>0とplayed==0を分離し、played==0は最下位側にまとめて並べる版のstandard_rank。
    (0/0を「たまたま良い値」として上位に出さないため。全員played==0ならstandard_rankにフォールバックし、
    全員同値=同順位[1位]に自然に収束する)
    """
    gt0 = [tid for tid in values if records[tid].played > 0]
    eq0 = [tid for tid in values if records[tid].played == 0]
    if gt0 and eq0:
        ranks = standard_rank({tid: values[tid] for tid in gt0}, better_is_high)
        worst = len(gt0) + 1
        for tid in eq0:
            ranks[tid] = worst
        return ranks
    return standard_rank(values, better_is_high)


def _team_points(my_score: int, opp_score: int) -> int:
    if my_score > opp_score:
        return 3
    if my_score == opp_score:
        return 1
    return 0


def compute_team_metrics(
    idTeam: str,
    finished_for_team: list[dict],
    pending_for_team: list[dict],
    ratings: dict[str, tuple[float, float]],
    league_avg_goals: float,
    hfa: float,
    rec: TeamRecord,
) -> dict:
    """1クラブぶんの自前計算指標(生の値。丸めない)。"""
    home_points = 0
    away_points = 0
    blanks = 0
    x_points = 0.0

    for m in finished_for_team:
        is_home = m["home"]["idTeam"] == idTeam
        mine = m["home"] if is_home else m["away"]
        opp = m["away"] if is_home else m["home"]
        pts = _team_points(mine["score"], opp["score"])
        if is_home:
            home_points += pts
        else:
            away_points += pts
        if mine["score"] == 0:
            blanks += 1

        atk_h, def_h = ratings[m["home"]["idTeam"]]
        atk_a, def_a = ratings[m["away"]["idTeam"]]
        lam_h, lam_a = expected_goals(atk_h, def_a, atk_a, def_h, league_avg_goals, hfa)
        p_h, p_d, p_a = match_outcome_probs(lam_h, lam_a)
        x_points += (3 * p_h + p_d) if is_home else (3 * p_a + p_d)

    mark_points = {"W": 3, "D": 1, "L": 0}
    form5_points = sum(mark_points[m] for m in rec.recent5())

    played = rec.played
    ga_per_game = (rec.ga / played) if played > 0 else 0.0
    points_per_game = (rec.points / played) if played > 0 else 0.0

    atk, de = ratings[idTeam]
    points_over_x = rec.points - x_points

    if pending_for_team:
        diffs = []
        for m in pending_for_team:
            is_home = m["home"]["idTeam"] == idTeam
            opp_id = m["away"]["idTeam"] if is_home else m["home"]["idTeam"]
            opp_atk, opp_def = ratings[opp_id]
            base = (opp_atk + (2 - opp_def)) / 2
            # ホーム/アウェイ補正: 自分がホームなら相対的に楽になる(1/hfa)、アウェイなら相手の
            # ホームアドバンテージを受ける分きつくなる(hfa)
            factor = (1.0 / hfa) if is_home else hfa
            diffs.append(base * factor)
        remaining_difficulty = sum(diffs) / len(diffs)
    else:
        remaining_difficulty = None

    return {
        "points": rec.points,
        "played": rec.played,
        "win": rec.win,
        "draw": rec.draw,
        "loss": rec.loss,
        "gf": rec.gf,
        "ga": rec.ga,
        "gd": rec.gd,
        "gaPerGame": ga_per_game,
        "pointsPerGame": points_per_game,
        "homePoints": home_points,
        "awayPoints": away_points,
        "blanks": blanks,
        "form5Points": form5_points,
        "attackRating": atk,
        "defenseRating": de,
        "xPoints": x_points,
        "pointsOverX": points_over_x,
        "remainingDifficulty": remaining_difficulty,
    }


def _round_for_format(value, fmt: str):
    if value is None:
        return None
    if fmt in ("int", "signedInt"):
        return int(round(value))
    if fmt in ("float2", "signed2"):
        return round(value, 2)
    if fmt == "percent1":
        return round(value, 1)
    return value  # "raw"(公式値)はそのまま


def build_stats(
    matches: list[dict],
    master_teams: list[dict],
    club_extra_clubs: dict | None,
    league: str = "?",
    log=print,
) -> dict:
    finished = [m for m in matches if m.get("finished")]
    pending = [m for m in matches if not m.get("finished")]

    records = seed_all_teams(build_records(matches), master_teams)
    league_avg_goals, hfa = compute_league_stats(finished)
    ratings = compute_ratings(records, league_avg_goals)

    all_ids = [t["idTeam"] for t in master_teams]
    team_lookup = {t["idTeam"]: t for t in master_teams}

    raw_values: dict[str, dict] = {}
    for tid in all_ids:
        finished_for_team = [m for m in finished if m["home"]["idTeam"] == tid or m["away"]["idTeam"] == tid]
        pending_for_team = [m for m in pending if m["home"]["idTeam"] == tid or m["away"]["idTeam"] == tid]
        raw_values[tid] = compute_team_metrics(
            tid, finished_for_team, pending_for_team, ratings, league_avg_goals, hfa, records[tid]
        )

    # 順位付け(計算指標のみ。公式指標は再計算せず後段でそのままマージする)
    computed_ranks: dict[str, dict[str, int | None]] = {tid: {} for tid in all_ids}
    for key, _label, better_is_high, _fmt, _group in COMPUTED_METRICS:
        if key in NO_RANK_KEYS:
            for tid in all_ids:
                computed_ranks[tid][key] = None
            continue

        if key == "remainingDifficulty":
            # 残り試合が0のクラブ(=値がNone)は順位対象から除外する
            rankable = {tid: raw_values[tid][key] for tid in all_ids if raw_values[tid][key] is not None}
            rank_map = standard_rank(rankable, better_is_high) if rankable else {}
        else:
            values = {tid: raw_values[tid][key] for tid in all_ids}
            if key in RATE_METRIC_KEYS:
                rank_map = rank_rate_metric(records, values, better_is_high)
            else:
                rank_map = standard_rank(values, better_is_high)

        for tid in all_ids:
            computed_ranks[tid][key] = rank_map.get(tid)

    # 公式指標の定義を1クラブぶんから抽出(同一リーグ内はキー構成が揃っている前提。
    # fetch_official.pyの実データ検証でリーグごとに固定であることを確認済み)
    official_metrics_def: list[tuple[str, str, bool, str, str]] = []
    if club_extra_clubs:
        for tid in all_ids:
            c = club_extra_clubs.get(tid)
            cs = c.get("clubStats") if c else None
            if cs and cs.get("items"):
                for item in cs["items"]:
                    fmt = "percent1" if item["key"] == "ballRate" else "raw"
                    official_metrics_def.append((item["key"], item["label"] or item["key"], True, fmt, "公式スタッツ"))
                break

    # 公式指標について、自前でも同一リーグ内順位を計算し突き合わせる。
    # 採用は公式優先(欠損クラブだけ自前で補う)。食い違いは警告ログに出す(seasonKey取り違え等の検知手段にもなる)。
    official_rank_adopted: dict[str, dict[str, int | None]] = {tid: {} for tid in all_ids}
    official_rank_check: dict[str, dict[str, dict]] = {tid: {} for tid in all_ids}
    for key, _label, better_is_high, _fmt, _group in official_metrics_def:
        official_value_of: dict[str, float] = {}
        official_rank_of: dict[str, int | None] = {}
        for tid in all_ids:
            c = club_extra_clubs.get(tid) if club_extra_clubs else None
            cs = c.get("clubStats") if c else None
            item = next((it for it in cs["items"] if it["key"] == key), None) if cs else None
            if item is not None:
                official_value_of[tid] = item["value"]
                official_rank_of[tid] = item["rank"]

        method = OFFICIAL_RANK_METHOD_OVERRIDE.get(key, "standard")
        rank_fn = dense_rank if method == "dense" else standard_rank
        computed_rank_of = rank_fn(official_value_of, better_is_high) if official_value_of else {}

        for tid in all_ids:
            off_rank = official_rank_of.get(tid)
            comp_rank = computed_rank_of.get(tid)
            adopted = off_rank if off_rank is not None else comp_rank
            official_rank_adopted[tid][key] = adopted
            official_rank_check[tid][key] = {"rankOfficial": off_rank, "rankComputed": comp_rank}

            if off_rank is not None and comp_rank is not None and off_rank != comp_rank:
                ja = team_lookup.get(tid, {}).get("ja", tid)
                log(
                    f"[warn] {league} {key}: 公式rank={off_rank} 自前rank={comp_rank}"
                    f"（採用: 公式, クラブ={ja}）",
                    file=sys.stderr,
                )

    metrics_out = [
        {"key": k, "label": lbl, "betterIsHigh": bih, "format": fmt, "group": grp, "source": "computed"}
        for k, lbl, bih, fmt, grp in COMPUTED_METRICS
    ] + [
        {"key": k, "label": lbl, "betterIsHigh": bih, "format": fmt, "group": grp, "source": "official"}
        for k, lbl, bih, fmt, grp in official_metrics_def
    ]
    format_of = {m["key"]: m["format"] for m in metrics_out}

    teams_out = []
    for tid in all_ids:
        info = team_lookup.get(tid, {})
        values: dict[str, object] = {}
        ranks: dict[str, object] = {}

        for key, *_ in COMPUTED_METRICS:
            values[key] = _round_for_format(raw_values[tid][key], format_of[key])
            ranks[key] = computed_ranks[tid][key]

        rank_checks: dict[str, dict] = {}
        if club_extra_clubs:
            c = club_extra_clubs.get(tid)
            cs = c.get("clubStats") if c else None
            if cs:
                for item in cs["items"]:
                    values[item["key"]] = _round_for_format(item["value"], format_of.get(item["key"], "raw"))
                    ranks[item["key"]] = official_rank_adopted[tid].get(item["key"], item["rank"])
                    rank_checks[item["key"]] = official_rank_check[tid].get(item["key"], {})

        teams_out.append({
            "idTeam": tid,
            "ja": info.get("ja", ""),
            "short": info.get("short", ""),
            "values": values,
            "ranks": ranks,
            "officialRankCheck": rank_checks,
        })

    return {
        "metrics": metrics_out,
        "teams": teams_out,
        "basedOnMatches": len(finished),
        "clubCount": len(all_ids),
    }


def load_matches(league: str) -> dict:
    path = PROCESSED_DIR / f"{league}_matches.json"
    if not path.exists():
        print(f"[error] {path} が無い。先に fetch_batch.py --league {league} を実行すること", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def load_club_extra() -> dict | None:
    """club_extra.jsonは無くても動く(公式スタッツを出さないだけ)。"""
    path = PROCESSED_DIR / "club_extra.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("clubs")
    except (json.JSONDecodeError, OSError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="スタッツ＋リーグ内順位(ネットワークアクセスなし)")
    parser.add_argument("--league", choices=["j1", "j2", "j3"], required=True)
    args = parser.parse_args()

    matches_data = load_matches(args.league)
    master_teams = load_master_teams(args.league)
    club_extra_clubs = load_club_extra()

    result = build_stats(matches_data["matches"], master_teams, club_extra_clubs, league=args.league)

    out = {
        "meta": {
            "league": args.league,
            "season": matches_data.get("meta", {}).get("season", "2026-2027"),
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "basedOnMatches": result["basedOnMatches"],
            "clubCount": result["clubCount"],
            "note": (
                "順位は同値同順位、次は飛ぶ方式(1,2,2,4)。シュート数はデータソースに存在しないため対象外。"
                "公式スタッツ(source:official)はJリーグ公式サイトの値・順位をそのまま使用(非公開データ。"
                "club_extra.jsonが無い場合はこのグループ自体を省略)"
            ),
        },
        "metrics": result["metrics"],
        "teams": result["teams"],
    }

    out_path = PROCESSED_DIR / f"{args.league}_stats.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    official_count = sum(1 for m in result["metrics"] if m["source"] == "official")
    print(
        f"[info] {out_path} に書き出し "
        f"(クラブ数={result['clubCount']}, 指標数={len(result['metrics'])}"
        f"[公式{official_count}件], 消化{result['basedOnMatches']}試合)"
    )


if __name__ == "__main__":
    main()
