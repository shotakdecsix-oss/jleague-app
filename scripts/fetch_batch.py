"""
3リーグ(J1/J2/J3)ぶんの試合データを取得し、data/processed/{league}_matches.json に書き出す。

CLI:
    python scripts/fetch_batch.py --league j2
    python scripts/fetch_batch.py --league j2 --rounds 1-5      # 部分取得(検証用)
    python scripts/fetch_batch.py --league all
    python scripts/fetch_batch.py --league all --incremental    # 増分取得(第11弾、GitHub Actions用)

第11弾: --incremental を付けると、既存の {league}_matches.json から「取得すべき節」を
リーグごとに自動判定する(determine_incremental_rounds())。毎回38節×3リーグを取り直すと
無料枠(private repoは月2000分)を圧迫するため、通常運転はこちらを使う。
既存データが無い(初回実行)リーグは全節取得にフォールバックする。--roundsと同時指定はできない。

やっていること(この順):
  1. マスタJSON(生のteams配列)を読む
  2. fetch_all_rounds()で1〜38節を取得(sleep_between=2.5固定)
  3. strSeason一致 かつ intRound!="0" のイベントだけ採用。落ちた件数はmeta.filteredOutに記録
  4. 日時はderive_kickoff_jst(strTimestamp)のみ。dateEvent/dateEventLocal/strTimeLocalは読まない
  5. strHomeTeam/strAwayTeamをマスタに照合。未一致が1件でもあればそのリーグは出力せず異常終了
  6. 表示名はマスタのja/shortを使う(APIの文字列はそのまま出力に入れない)
  7. strStatus=="FT" かつ両スコアが非null のときだけ finished=true
  8. data/processed/{league}_matches.json に書き出す。data/processed/meta.jsonに実行ログを追記
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_utils import DEFAULT_SLEEP_BETWEEN, FetchAllResult, Outcome, fetch_all_rounds  # noqa: E402
from team_matching import build_lookup, normalize_name  # noqa: E402
from time_utils import JST, derive_kickoff_jst  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

SEASON_PRIMARY = "2026-2027"
SEASON_FALLBACK = "2026"
TOTAL_ROUNDS = 38

LEAGUES = {
    "j1": {"idLeague": "4633", "master": MASTERS_DIR / "j1_teams_2026-27.json"},
    "j2": {"idLeague": "4824", "master": MASTERS_DIR / "j2_master_2026-27.json"},
    "j3": {"idLeague": "4967", "master": MASTERS_DIR / "j3_teams_2026-27.json"},
}


def parse_rounds(spec: str | None) -> list[int] | None:
    if spec is None:
        return None
    a, b = spec.split("-")
    return list(range(int(a), int(b) + 1))


# 増分取得(第11弾4章)で「未消化試合の節」を対象に含める時間窓。
# 「次節」をroundの値では括らない他の場所(他会場インパクト等)と違い、ここは節の取得要否を
# 決めるだけなので、時間窓に入った節を丸ごと対象にしてよい(1試合だけ拾うわけではないため)。
INCREMENTAL_WINDOW_DAYS = 14


def load_existing_matches(league: str) -> list[dict] | None:
    """既存の{league}_matches.jsonからmatches配列だけ読む。無ければNone(=初回実行、全節取得へフォールバック)。"""
    path = PROCESSED_DIR / f"{league}_matches.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("matches")


def determine_incremental_rounds(matches: list[dict] | None, now: datetime) -> list[int] | None:
    """
    増分取得の対象roundを決める(第11弾4章)。matchesが無ければ全節取得にフォールバックしてNoneを返す。

    対象 = 次の1と2の和集合:
      1. 未消化(finished=false)の試合のうち、kickoffJstが now から+14日以内のものが属する節
         (kickoffJstが無い=日程未定の試合は対象に含めない。日程が付いた瞬間にまた取りに行けばよいため)
      2. 直近で消化された試合(kickoffJstが最も新しいfinished=trueの試合)が属する節
         (スコア訂正・後追い反映の回収用)

    和集合が空(=シーズン開幕前や完全に何も無い状態)なら、最小1節は必ず取る:
    未消化試合の中でキックオフが最も早い節。それも無ければ最終節(全消化済み)。
    """
    if not matches:
        return None

    horizon = now + timedelta(days=INCREMENTAL_WINDOW_DAYS)
    rounds: set[int] = set()

    for m in matches:
        if m.get("finished") or m.get("round") is None:
            continue
        kj = m.get("kickoffJst")
        if not kj:
            continue
        try:
            dt = datetime.fromisoformat(kj)
        except ValueError:
            continue
        if dt <= horizon:
            rounds.add(m["round"])

    finished = [m for m in matches if m.get("finished") and m.get("kickoffJst") and m.get("round") is not None]
    if finished:
        latest = max(finished, key=lambda m: m["kickoffJst"])
        rounds.add(latest["round"])

    if not rounds:
        pending = [m for m in matches if not m.get("finished") and m.get("round") is not None]
        if pending:
            earliest = min(pending, key=lambda m: m.get("kickoffJst") or "9999-99-99")
            rounds.add(earliest["round"])
        else:
            all_rounds = [m["round"] for m in matches if m.get("round") is not None]
            if all_rounds:
                rounds.add(max(all_rounds))

    return sorted(rounds) if rounds else None


def load_master_raw_teams(league: str) -> list[dict]:
    raw = json.loads(LEAGUES[league]["master"].read_text(encoding="utf-8"))
    return raw["teams"]


def process_league(league: str, rounds: list[int] | None, sleep_between: float = DEFAULT_SLEEP_BETWEEN) -> dict:
    """1リーグぶん取得・整形する(書き込みはしない、呼び出し元に判断を委ねる)。"""
    id_league = LEAGUES[league]["idLeague"]
    master_teams = load_master_raw_teams(league)
    lookup = build_lookup(master_teams)

    season_used = SEASON_PRIMARY
    fetch_result = fetch_all_rounds(
        league, id_league, season_used,
        total_rounds=TOTAL_ROUNDS, rounds=rounds, sleep_between=sleep_between,
    )
    attempts = [fetch_result]

    # シーズン文字列のフォールバックは「APIは正常に応答しているが2026-2027に該当データが無い」
    # ときだけ意味がある。fetch_result.aborted(レート制限等で中断)のときにフォールバックすると、
    # 制限を踏んでいる最中にもう一周ぶん投げに行って状況を悪化させるので、abortedなら諦める。
    if not fetch_result.ok_rounds and not fetch_result.aborted:
        print(
            f"[warn] league={league}: season={season_used} で有効な節が0件(異常終了ではない)。"
            f"フォールバック season={SEASON_FALLBACK} を試す",
            file=sys.stderr,
        )
        season_used = SEASON_FALLBACK
        fetch_result = fetch_all_rounds(
            league, id_league, season_used,
            total_rounds=TOTAL_ROUNDS, rounds=rounds, sleep_between=sleep_between,
        )
        attempts.append(fetch_result)

    total_request_count = sum(a.request_count for a in attempts)
    total_rounds_attempted = sum(len(a.results) for a in attempts)
    total_429 = sum(a.count_429 for a in attempts)

    raw_events: list[dict] = []
    for rr in fetch_result.results:
        raw_events.extend(rr.events)

    filtered_out = 0
    kickoff_tbd_count = 0
    unmatched_names: set[str] = set()
    matches_by_id: dict[str, dict] = {}

    for ev in raw_events:
        if ev.get("strSeason") != season_used or ev.get("intRound") in (None, "0"):
            filtered_out += 1
            continue

        home_name, away_name = ev.get("strHomeTeam"), ev.get("strAwayTeam")
        home_hit = lookup.get(normalize_name(home_name or ""))
        away_hit = lookup.get(normalize_name(away_name or ""))
        if home_hit is None:
            unmatched_names.add(home_name or "(空)")
        if away_hit is None:
            unmatched_names.add(away_name or "(空)")
        if home_hit is None or away_hit is None:
            continue  # 未一致は最後にまとめて報告して異常終了させる。ここではスキップして集計は続ける

        home_team, _ = home_hit
        away_team, _ = away_hit

        # 延期試合の日程差し替え中は strTimestamp が空になることがある(実在確認済み)。
        # 試合自体は日程表示から消したくないので、日時だけnull(kickoffTbd=true)にして残す。
        ts = ev.get("strTimestamp")
        if ts:
            kickoff = derive_kickoff_jst(ts)
            kickoff_iso, kickoff_date, kickoff_time, kickoff_tbd = kickoff.iso, kickoff.date, kickoff.time, False
        else:
            kickoff_iso = kickoff_date = kickoff_time = None
            kickoff_tbd = True
            kickoff_tbd_count += 1

        status = ev.get("strStatus")
        home_score_raw, away_score_raw = ev.get("intHomeScore"), ev.get("intAwayScore")
        finished = status == "FT" and home_score_raw is not None and away_score_raw is not None
        home_score = int(home_score_raw) if finished else None
        away_score = int(away_score_raw) if finished else None

        match = {
            "idEvent": ev["idEvent"],
            "round": int(ev["intRound"]),
            "kickoffJst": kickoff_iso,
            "kickoffDate": kickoff_date,
            "kickoffTime": kickoff_time,
            "kickoffTbd": kickoff_tbd,
            "status": status,
            "finished": finished,
            "home": {
                "idTeam": home_team["idTeam"],
                "ja": home_team["ja"],
                "short": home_team.get("short"),
                "score": home_score,
            },
            "away": {
                "idTeam": away_team["idTeam"],
                "ja": away_team["ja"],
                "short": away_team.get("short"),
                "score": away_score,
            },
            "idVenue": ev.get("idVenue"),
        }
        matches_by_id[ev["idEvent"]] = match  # 同一idEventは後勝ちで上書き(重複排除)

    # 日時未定(kickoffJst=None)は末尾へ。Noneが混ざるとTypeErrorになるためフォールバックキーを用意する。
    matches = sorted(matches_by_id.values(), key=lambda m: m["kickoffJst"] or "9999")

    return {
        "league": league,
        "id_league": id_league,
        "season_used": season_used,
        "fetch_result": fetch_result,
        "request_count": total_request_count,
        "rounds_attempted": total_rounds_attempted,
        "count_429": total_429,
        "filtered_out": filtered_out,
        "kickoff_tbd_count": kickoff_tbd_count,
        "unmatched_names": unmatched_names,
        "matches": matches,
    }


def write_league_output(league: str, proc: dict) -> None:
    fr: FetchAllResult = proc["fetch_result"]
    meta = {
        "league": league,
        "idLeague": proc["id_league"],
        "season": proc["season_used"],
        "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
        "totalRounds": TOTAL_ROUNDS,
        "okRounds": fr.ok_rounds,
        "badRounds": [
            {"round": r.round, "outcome": r.outcome.value, "detail": r.detail}
            for r in fr.bad_rounds
        ],
        "aborted": fr.aborted,
        "count429": proc["count_429"],  # フォールバック含む累積(process_leagueで集計済み)
        "filteredOut": proc["filtered_out"],
        "kickoffTbdCount": proc["kickoff_tbd_count"],  # strTimestamp欠損(延期の日程差し替え中)の件数
        "matchCount": len(proc["matches"]),
    }
    out = {"meta": meta, "matches": proc["matches"]}
    out_path = PROCESSED_DIR / f"{league}_matches.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[info] {out_path} に {len(proc['matches'])}試合を書き出し "
        f"(okRounds={len(fr.ok_rounds)}/{TOTAL_ROUNDS}, count429={proc['count_429']}, "
        f"filteredOut={proc['filtered_out']}, kickoffTbd={proc['kickoff_tbd_count']})"
    )


def update_run_meta(leagues: list[str], total_requests: int, total_rounds_attempted: int, total_429: int, duration_sec: float) -> None:
    """
    data/processed/meta.jsonに実行ログを追記する。

    requests: 実際にAPIへ投げたHTTPリクエストの総数(429/5xx/タイムアウトのリトライ、
              シーズンフォールバックでの撃ち直し全て込み)。日次の累積制限を運用しながら見るための値。
    rounds  : 試行した節の延べ数(節数ベース。requestsとは別軸)。
    """
    meta_path = PROCESSED_DIR / "meta.json"
    if meta_path.exists():
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        data = {"runs": []}
    data.setdefault("runs", []).append({
        "at": datetime.now(JST).isoformat(timespec="seconds"),
        "leagues": leagues,
        "requests": total_requests,
        "rounds": total_rounds_attempted,
        "count429": total_429,
        "durationSec": round(duration_sec, 1),
    })
    data["runs"] = data["runs"][-30:]  # 直近30件だけ残す
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[info] {meta_path} に実行ログを追記(直近{len(data['runs'])}件保持)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Jリーグ試合データ取得バッチ")
    parser.add_argument("--league", choices=["j1", "j2", "j3", "all"], required=True)
    parser.add_argument("--rounds", default=None, help='部分取得。例: "1-5"')
    parser.add_argument(
        "--incremental", action="store_true",
        help="既存データから取得すべき節をリーグごとに自動判定する(第11弾。--roundsとは併用不可)",
    )
    parser.add_argument("--sleep-between", type=float, default=DEFAULT_SLEEP_BETWEEN)
    args = parser.parse_args()

    if args.incremental and args.rounds:
        print("[error] --incremental と --rounds は同時に指定できません", file=sys.stderr)
        sys.exit(2)

    target_leagues = ["j1", "j2", "j3"] if args.league == "all" else [args.league]
    fixed_rounds = parse_rounds(args.rounds)  # --incremental時はNone(リーグごとに別途決める)

    start = time.time()
    total_requests = 0       # 実HTTPリクエスト数(リトライ・フォールバック込み)
    total_rounds_attempted = 0  # 試行した節の延べ数
    total_429 = 0
    had_failure = False

    for league in target_leagues:
        if args.incremental:
            rounds = determine_incremental_rounds(load_existing_matches(league), datetime.now(JST))
        else:
            rounds = fixed_rounds
        print(f"\n=== {league} 取得開始 (rounds={rounds or f'1-{TOTAL_ROUNDS}'}) ===")
        proc = process_league(league, rounds, sleep_between=args.sleep_between)
        fr: FetchAllResult = proc["fetch_result"]
        total_requests += proc["request_count"]
        total_rounds_attempted += proc["rounds_attempted"]
        total_429 += proc["count_429"]

        if proc["unmatched_names"]:
            had_failure = True
            print(
                f"[error] league={league}: 未一致チーム名 {len(proc['unmatched_names'])}件 -> "
                f"出力しません: {sorted(proc['unmatched_names'])}",
                file=sys.stderr,
            )
            continue

        if fr.aborted:
            had_failure = True
            print(
                f"[error] league={league}: 取得が中断されたため出力しません ({fr.abort_reason})",
                file=sys.stderr,
            )
            continue

        write_league_output(league, proc)

    duration = time.time() - start
    update_run_meta(target_leagues, total_requests, total_rounds_attempted, total_429, duration)

    if had_failure:
        print("\n[error] 一部リーグで異常終了しました。上記ログを確認してください。", file=sys.stderr)
        sys.exit(1)

    print(f"\n完了: {target_leagues} / 所要{duration:.1f}秒 / 429発生{total_429}回")


if __name__ == "__main__":
    main()
