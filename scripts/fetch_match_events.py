"""
第13弾: 進行中・直近の試合について、jleague.jp公式サイトから得点者・カード・選手交代を
取得し、data/processed/{league}_match_events.json に書き出す。

CORS制約によりブラウザから直接jleague.jpへアクセスできないことを実機で確認済み
(docs/prompt-jleague-impl-13.md参照)。このためサーバー側(GitHub Actions/手動実行)から
スクレイピングし、静的JSONとしてdistに含める方式を取る。

前提・安全策(第11弾matches.jsonの事故を踏まえたルール):
  - EVENTS_WINDOW_HOURS時間以内にキックオフした試合だけを対象にする(節約と、
    古い試合を毎回引き直さないため)。ウィンドウ外に出た試合の既存データはそのまま残す。
  - 日程一覧ページ(https://www.jleague.jp/match/{league}/)で「対戦カード -> 6桁コード」を
    解決できなかった候補はスキップし、既存データ(あれば)には一切触らない。
  - livetxt/ の取得やパースに失敗した試合も同様にスキップし、既存データは残す。
  - 取得自体は成功(HTTP 200)したが得点/カード/交代が合計0件で、かつ既存データには
    1件以上あった場合は「退行」とみなして上書きしない(サイト側マークアップ変更などで
    正規表現が空振りしている可能性を考慮した安全弁)。

対戦カード -> コードの解決は、日程一覧ページに出る「その節」のチーム名(ja表記)を
scripts/team_matching.match_team_ja() でmatches.json側のidTeamに変換し、
home/away両方のidTeamが一致するものを採用する(コードそのものから日付は分かるが、
それだけでは対戦カードを特定できないため)。

CLI:
    python scripts/fetch_match_events.py --league j2
    python scripts/fetch_match_events.py --league all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_batch import LEAGUES  # noqa: E402  (idLeague/masterパスの定義を再利用)
from fetch_official import HEADERS, TIMEOUT, extract_next_chunks  # noqa: E402
from match_events_parser import extract_schedule_index, find_cards, find_goals, find_subs  # noqa: E402
from team_matching import load_master_teams, match_team_ja  # noqa: E402
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

EVENTS_WINDOW_HOURS = 36
SLEEP_BETWEEN_MATCHES = 2.0


def fetch_html(url: str) -> str | None:
    import requests

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:  # noqa: BLE001
        print(f"[warn] 取得失敗: {url} ({e})", file=sys.stderr)
        return None
    return resp.text


def load_all_teams() -> list[dict]:
    all_teams: list[dict] = []
    for league, cfg in LEAGUES.items():
        raw = json.loads(cfg["master"].read_text(encoding="utf-8"))
        all_teams.extend(load_master_teams(league, raw))
    return all_teams


def load_matches(league: str) -> list[dict]:
    path = PROCESSED_DIR / f"{league}_matches.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("matches", [])


def load_existing_events(league: str) -> dict:
    path = PROCESSED_DIR / f"{league}_match_events.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("events", {})


def pick_candidates(matches: list[dict], now: datetime) -> list[dict]:
    """キックオフ済み、かつnowからEVENTS_WINDOW_HOURS時間以内にキックオフした試合だけを対象にする。"""
    horizon = now - timedelta(hours=EVENTS_WINDOW_HOURS)
    out = []
    for m in matches:
        kj = m.get("kickoffJst")
        if not kj:
            continue
        try:
            dt = datetime.fromisoformat(kj)
        except ValueError:
            continue
        if horizon <= dt <= now:
            out.append(m)
    return out


def resolve_codes(
    league: str, candidates: list[dict], all_teams: list[dict]
) -> tuple[dict[str, dict], list[str]]:
    """
    候補試合(idEvent)ごとに、日程一覧ページから対戦カード一致でコードを解決する。
    戻り値: (idEvent -> {"league","year","code","url"}, 解決できなかったidEventのリスト)
    """
    index_url = f"https://www.jleague.jp/match/{league}/"
    html = fetch_html(index_url)
    if html is None:
        return {}, [m["idEvent"] for m in candidates]

    chunks = extract_next_chunks(html)
    entries = extract_schedule_index(chunks)

    resolved_entries = []
    for e in entries:
        home_team = match_team_ja(e["home"], all_teams)
        away_team = match_team_ja(e["away"], all_teams)
        if home_team is None or away_team is None:
            continue
        resolved_entries.append((home_team["idTeam"], away_team["idTeam"], e))

    resolved: dict[str, dict] = {}
    unresolved: list[str] = []
    for m in candidates:
        hit = next(
            (
                e
                for h_id, a_id, e in resolved_entries
                if h_id == m["home"]["idTeam"] and a_id == m["away"]["idTeam"]
            ),
            None,
        )
        if hit is None:
            unresolved.append(m["idEvent"])
            continue
        resolved[m["idEvent"]] = {
            "league": hit["league"],
            "year": hit["year"],
            "code": hit["code"],
            "url": f"https://www.jleague.jp/match/{hit['league']}/{hit['year']}/{hit['code']}/livetxt/",
        }
    return resolved, unresolved


def parse_and_merge(
    id_event: str, resolved: dict, all_teams: list[dict], existing_events: dict, failed: list[dict]
) -> dict | None:
    html = fetch_html(resolved["url"])
    if html is None:
        failed.append({"idEvent": id_event, "reason": "fetch_failed", "url": resolved["url"]})
        return None

    chunks = extract_next_chunks(html)
    goals = find_goals(chunks)
    cards = find_cards(chunks)
    subs = find_subs(chunks)

    def resolve_club(name: str | None) -> str | None:
        if not name:
            return None
        team = match_team_ja(name, all_teams)
        return team["idTeam"] if team else None

    for g in goals:
        g["idTeam"] = resolve_club(g.get("club"))
    for c in cards:
        c["idTeam"] = resolve_club(c.get("club"))
    for s in subs:
        s["idTeam"] = resolve_club(s.get("club"))

    total = len(goals) + len(cards) + len(subs)
    old = existing_events.get(id_event)
    old_total = (
        len(old.get("goals", [])) + len(old.get("cards", [])) + len(old.get("subs", []))
        if old
        else 0
    )
    if total == 0 and old_total > 0:
        failed.append({"idEvent": id_event, "reason": "regression_zero_events", "url": resolved["url"]})
        return None

    return {
        "code": resolved["code"],
        "url": resolved["url"],
        "fetchedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
        "goals": goals,
        "cards": cards,
        "subs": subs,
    }


def write_events(league: str, events: dict, meta: dict) -> None:
    meta["eventCount"] = len(events)
    out = {"meta": meta, "events": events}
    out_path = PROCESSED_DIR / f"{league}_match_events.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[info] {out_path}: candidates={meta['candidateCount']} fetched={meta['fetchedCount']} "
        f"failed={len(meta['failed'])} total_events={len(events)}"
    )


def process_league(league: str, all_teams: list[dict], now: datetime) -> None:
    matches = load_matches(league)
    candidates = pick_candidates(matches, now)
    existing = load_existing_events(league)
    events = dict(existing)  # コピーして開始。候補以外の既存データには一切触らない

    meta = {
        "league": league,
        "generatedAtJst": now.isoformat(timespec="seconds"),
        "windowHours": EVENTS_WINDOW_HOURS,
        "candidateCount": len(candidates),
        "fetchedCount": 0,
        "failed": [],
    }

    if not candidates:
        write_events(league, events, meta)
        return

    resolved, unresolved = resolve_codes(league, candidates, all_teams)
    for id_event in unresolved:
        meta["failed"].append({"idEvent": id_event, "reason": "code_not_found"})

    resolved_items = list(resolved.items())
    for i, (id_event, r) in enumerate(resolved_items):
        result = parse_and_merge(id_event, r, all_teams, existing, meta["failed"])
        if result is not None:
            events[id_event] = result
            meta["fetchedCount"] += 1
        if i < len(resolved_items) - 1:
            time.sleep(SLEEP_BETWEEN_MATCHES)

    write_events(league, events, meta)


def main() -> None:
    parser = argparse.ArgumentParser(description="jleague.jp試合詳細(得点/カード/交代)取得バッチ")
    parser.add_argument("--league", choices=["j1", "j2", "j3", "all"], required=True)
    args = parser.parse_args()

    target_leagues = ["j1", "j2", "j3"] if args.league == "all" else [args.league]
    all_teams = load_all_teams()
    now = datetime.now(JST)

    for league in target_leagues:
        print(f"\n=== {league} 試合詳細取得開始 ===")
        process_league(league, all_teams, now)


if __name__ == "__main__":
    main()
