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

第14弾: 控えメンバー(ベンチ)はlivetxt/ページには埋め込まれておらず、個別試合ページの
基点URL(baseUrl。#lineup等のアンカーがある方)にのみ載っている。そのため1試合につき
livetxt/とbaseUrlの2回リクエストする(SLEEP_BETWEEN_REQUESTS秒あける)。基点ページの
取得/抽出に失敗しても、livetxt/側で取れた得点/カード/交代は無駄にしない(bench_by_slugが
空のまま処理を続け、控えメンバーだけ前回分を維持するか空になる)。

第14弾: ハイライト動画はjleague.jp側(review/ページの「【公式】」動画、highlightVideoId)に
加えて、DAZN Japanの YouTube チャンネル(尺が長く内容も充実している、とのフィードバックを
反映)もscripts/youtube_highlights.py経由で検索し、daznVideoIdとして保存する。
YouTube Data API v3の無料枠(1日100検索まで)を使い切らないよう、試合ごとに
DAZN_SEARCH_COOLDOWN_HOURS間隔・DAZN_SEARCH_MAX_ATTEMPTS回までしか検索しない
(動画が見つかった後は検索しない。APIキー未設定の場合はこの機能全体を静かにスキップする)。

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
from match_events_parser import (  # noqa: E402
    extract_schedule_index,
    find_cards,
    find_formations,
    find_goals,
    find_highlight_video_id,
    find_lineup_members,
    find_subs,
)
from team_matching import load_master_teams, match_team_ja  # noqa: E402
from time_utils import JST  # noqa: E402
from youtube_highlights import load_api_key as load_youtube_api_key  # noqa: E402
from youtube_highlights import search_dazn_highlight  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

EVENTS_WINDOW_HOURS = 36
SLEEP_BETWEEN_MATCHES = 2.0
SLEEP_BETWEEN_REQUESTS = 1.0  # 第14弾: 同一試合内でlivetxt/基点ページの2回叩く間隔

# 第14弾: DAZN Japanのハイライト動画検索(YouTube Data API v3)のレート制御。
# 無料枠は1日100検索まで(search.list=100ユニット/回、枠は1日10,000ユニット)。
# 試合ごとに間隔をあけ、かつ試行回数の上限を設けることで枠を使い切らないようにする。
DAZN_SEARCH_COOLDOWN_HOURS = 6
DAZN_SEARCH_MAX_ATTEMPTS = 3


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
            # 第14弾: 控えメンバーはlivetxt/ではなく基点ページ(#lineup)側にしか埋め込まれていない
            "baseUrl": f"https://www.jleague.jp/match/{hit['league']}/{hit['year']}/{hit['code']}/",
            # 第14弾: ハイライト動画(YouTube)はreview/側にのみ埋め込まれている。試合終了前は
            # 存在しない/意味の無いページなので、finished(matches.json由来)で取得要否を判断する。
            "reviewUrl": f"https://www.jleague.jp/match/{hit['league']}/{hit['year']}/{hit['code']}/review/",
            "finished": bool(m.get("finished")),
            # 第14弾: DAZN Japanチャンネルの検索クエリ用(matches.json側のja表記)
            "homeJa": (m.get("home") or {}).get("ja"),
            "awayJa": (m.get("away") or {}).get("ja"),
        }
    return resolved, unresolved


def build_lineup_side(side: dict, bench_by_slug: dict[str, list[dict]], all_teams: list[dict]) -> dict:
    team = match_team_ja(side.get("teamName") or "", all_teams)
    players = sorted(side.get("players", []) or [], key=lambda p: p.get("positionX", 0))
    starter_ids = {str(p.get("id")) for p in players}

    # 第14弾: 控えメンバー。bench_by_slugにはスタメン+控えの全員(チームごとの出現順)が
    # 入っているので、formations側で既にスタメンと分かっているidを除いた残りを控えとみなす
    # (「先頭11人が控え」という順序を信用するより、スタメンidとの差集合の方が壊れにくい)。
    slug = (team or {}).get("jleagueSlug")
    bench_raw = bench_by_slug.get(slug, []) if slug else []
    bench = [p for p in bench_raw if p.get("id") not in starter_ids]

    return {
        "idTeam": team["idTeam"] if team else None,
        "formation": side.get("formation"),
        "players": [
            {"id": p.get("id"), "name": p.get("name"), "number": p.get("playerNumber")}
            for p in players
        ],
        "bench": [
            {"id": p.get("id"), "name": p.get("name"), "number": p.get("number"), "position": p.get("position")}
            for p in bench
        ],
    }


def build_lineups(
    formations: list[dict] | None, bench_by_slug: dict[str, list[dict]], all_teams: list[dict]
) -> dict | None:
    """formationsは基本"前半0分"時点の1件のみ(それ以降のスタメン変更は無い前提)。"""
    if not formations:
        return None
    f = formations[0]
    home_raw, away_raw = f.get("homeTeam"), f.get("awayTeam")
    if not home_raw or not away_raw:
        return None
    return {
        "home": build_lineup_side(home_raw, bench_by_slug, all_teams),
        "away": build_lineup_side(away_raw, bench_by_slug, all_teams),
    }


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

    # 第14弾: 控えメンバーはlivetxt/側には無く、基点ページ(baseUrl)側にしか埋め込まれていない。
    # この2回目の取得が失敗しても、得点/カード/交代(既に取得済み)は無駄にしない。
    bench_by_slug: dict[str, list[dict]] = {}
    base_url = resolved.get("baseUrl")
    if base_url:
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        base_html = fetch_html(base_url)
        if base_html is not None:
            bench_by_slug = find_lineup_members(extract_next_chunks(base_html))

    lineups = build_lineups(find_formations(chunks), bench_by_slug, all_teams)

    # 第14弾: ハイライト動画(YouTube)はreview/側にのみ埋め込まれている。試合終了前は
    # 存在しないページなので、finished(matches.json由来)が立っている試合だけ取得しにいく
    # (無駄なリクエストを避けるため)。動画がまだ用意されていない場合はNoneのままでよい
    # (次回以降のバッチで拾えればよい安全弁対象)。
    highlight_video_id = None
    review_url = resolved.get("reviewUrl")
    if resolved.get("finished") and review_url:
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        review_html = fetch_html(review_url)
        if review_html is not None:
            highlight_video_id = find_highlight_video_id(extract_next_chunks(review_html))

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

    old = existing_events.get(id_event)

    total = len(goals) + len(cards) + len(subs)
    old_total = (
        len(old.get("goals", [])) + len(old.get("cards", [])) + len(old.get("subs", []))
        if old
        else 0
    )
    if total == 0 and old_total > 0:
        failed.append({"idEvent": id_event, "reason": "regression_zero_events", "url": resolved["url"]})
        return None

    # スタメンは試合中に変わらない前提なので、今回抜けなくても前回分があればそのまま維持する
    # (マークアップの一時的な揺れで消してしまわないための安全弁。得点/カード/交代の退行防止と同じ考え方)。
    if lineups is None and old:
        lineups = old.get("lineups")
    # 控えメンバーだけが今回取れなかった場合(基点ページの取得/抽出失敗)も、前回分があれば
    # 片側ずつ維持する(スタメンは取れているのに控えだけ消えるのを防ぐ)。
    elif lineups and old and old.get("lineups"):
        old_lineups = old["lineups"]
        for side in ("home", "away"):
            if lineups.get(side) and not lineups[side].get("bench") and old_lineups.get(side, {}).get("bench"):
                lineups[side]["bench"] = old_lineups[side]["bench"]

    # ハイライト動画も同様に、今回取れなかった(まだ公開されていない/取得失敗)場合は前回分を維持する
    if highlight_video_id is None and old:
        highlight_video_id = old.get("highlightVideoId")

    # 第14弾: DAZN Japanのハイライト動画をYouTube Data API v3で検索する。
    # 無料枠(1日100回)を使い切らないよう、試合ごとにクールダウンと試行回数の上限を設ける。
    # ・既に見つかっている(dazn_video_idがある)なら検索しない
    # ・試合が終わっていない(finishedでない)うちは検索しない(まだ動画が無いのが確実なため)
    # ・試行回数がDAZN_SEARCH_MAX_ATTEMPTSに達したら諦める(いつまでも上がらない試合もあるため)
    # ・前回の検索から DAZN_SEARCH_COOLDOWN_HOURS 時間経つまでは再検索しない
    # ・APIキーが未設定の間は、試行回数もクールダウンも消費しない(キー設定前に無駄撃ちした
    #   「なし」判定のせいで、キー設定後もクールダウン待ちになってしまうのを防ぐため)
    dazn_video_id = old.get("daznVideoId") if old else None
    dazn_attempts = (old.get("daznSearchAttempts") if old else None) or 0
    dazn_last_searched = old.get("daznLastSearchedAtJst") if old else None

    dazn_key_present = load_youtube_api_key() is not None
    if resolved.get("finished") and dazn_video_id is None and dazn_attempts < DAZN_SEARCH_MAX_ATTEMPTS and not dazn_key_present:
        print(f"[fetch_match_events] {id_event}: YOUTUBE_API_KEY未設定のためDAZN検索をスキップ", file=sys.stderr)

    if (
        resolved.get("finished")
        and dazn_video_id is None
        and dazn_attempts < DAZN_SEARCH_MAX_ATTEMPTS
        and dazn_key_present
    ):
        cooldown_elapsed = True
        if dazn_last_searched:
            try:
                last_dt = datetime.fromisoformat(dazn_last_searched)
                cooldown_elapsed = (
                    datetime.now(JST) - last_dt
                ).total_seconds() >= DAZN_SEARCH_COOLDOWN_HOURS * 3600
            except ValueError:
                cooldown_elapsed = True
        if cooldown_elapsed:
            found = search_dazn_highlight(resolved.get("homeJa"), resolved.get("awayJa"))
            dazn_attempts += 1
            dazn_last_searched = datetime.now(JST).isoformat(timespec="seconds")
            if found:
                dazn_video_id = found

    return {
        "code": resolved["code"],
        "url": resolved["url"],
        "fetchedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
        "goals": goals,
        "cards": cards,
        "subs": subs,
        "highlightVideoId": highlight_video_id,
        "daznVideoId": dazn_video_id,
        "daznSearchAttempts": dazn_attempts,
        "daznLastSearchedAtJst": dazn_last_searched,
        "lineups": lineups,
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
