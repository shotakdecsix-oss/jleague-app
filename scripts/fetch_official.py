"""
Jリーグ公式サイト(jleague.jp)のクラブページから、ニュース・選手一覧・クラブスタッツ・
得点/アシスト/デュエル勝利数ランキング・シーズン別成績を取得する。

各クラブについて https://www.jleague.jp/club/{slug}/ を1回GETするだけでよい。
このサイトはNext.jsのApp Routerでサーバーレンダリングされており、
<script>self.__next_f.push([1,"..."])</script> というストリーミングペイロードの中に
必要なデータがすべてJSON形式で埋め込まれている(選手一覧専用ページへの追加アクセスは不要。
data/tmp/sample_club_top.html・sample_club_player.html で選手データが完全に一致することを確認済み)。

取得失敗(HTTPエラー・想定したキーが見つからない等)は、そのクラブだけスキップして続行する。
1クラブ取得するごとに SLEEP_BETWEEN_CLUBS 秒待つ(相手サーバーへの配慮)。

クラブのURLスラッグは新規に管理せず、data/masters/*.json の teams[].playersUrl
(例: "https://www.jleague.jp/club/shonan/#player") から抽出して再利用する。

CLI:
    python scripts/fetch_official.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

SITE_ORIGIN = "https://www.jleague.jp"

MASTER_FILES = {
    "j1": MASTERS_DIR / "j1_teams_2026-27.json",
    "j2": MASTERS_DIR / "j2_master_2026-27.json",
    "j3": MASTERS_DIR / "j3_teams_2026-27.json",
}

SLEEP_BETWEEN_CLUBS = 2.0
TIMEOUT = 20.0
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jleague-app personal use"}

NEXT_F_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)
CHUNK_LINE_RE = re.compile(r"^([0-9a-f]+):(.*)$")


def load_clubs() -> list[dict]:
    """全リーグのマスタから idTeam/ja/league/slug のリストを作る。"""
    clubs: list[dict] = []
    for league, path in MASTER_FILES.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        for t in data["teams"]:
            m = re.search(r"club/([^/]+)/", t.get("playersUrl", ""))
            if not m:
                continue
            clubs.append({"idTeam": t["idTeam"], "ja": t["ja"], "league": league, "slug": m.group(1)})
    return clubs


def extract_next_chunks(html: str) -> dict[str, str]:
    """Next.jsのRSCストリーミングペイロードを結合し、チャンクID -> 生JSON文字列 の辞書にする。"""
    payloads = NEXT_F_RE.findall(html)
    full = "".join(json.loads(p) for p in payloads)
    chunks: dict[str, str] = {}
    for line in full.split("\n"):
        m = CHUNK_LINE_RE.match(line)
        if m:
            chunks[m.group(1)] = m.group(2)
    return chunks


def _find_key(obj, key: str, found: list) -> None:
    if isinstance(obj, dict):
        if key in obj:
            found.append(obj[key])
        for v in obj.values():
            _find_key(v, key, found)
    elif isinstance(obj, list):
        for item in obj:
            _find_key(item, key, found)


def search_chunks(chunks: dict[str, str], key: str) -> list:
    """
    全チャンクをJSONとしてパースし、keyを持つ値を再帰的に集める。
    チャンクIDはリクエストのたびに変わりうるため、IDに依存せずキー名で探す。
    """
    found: list = []
    for val in chunks.values():
        if f'"{key}"' not in val:
            continue
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            continue
        _find_key(parsed, key, found)
    return found


def strip_date_prefix(s):
    """Next.jsのDateシリアライズ("$D2026-01-01T...")からプレフィックスを外す。"""
    if isinstance(s, str) and s.startswith("$D"):
        return s[2:]
    return s


def parse_publish_date(raw: str | None) -> str | None:
    """'2026-08-07 09:00:00+00' 形式(UTC)をJSTのISO8601文字列に変換する。"""
    if not raw:
        return None
    try:
        normalized = re.sub(r"([+-]\d{2})$", r"\1:00", raw.replace(" ", "T"))
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).isoformat()
    except (ValueError, TypeError):
        return None


def build_media_url(key_visual) -> str | None:
    """
    ニュースのサムネイル画像の絶対URLを組み立てる。

    keyVisual.url は "/api/media/file/{filename}" というAPIパスだが、実際にページ上で
    <img src=...> に使われているのは "/images/media/{filename}" というパス
    (data/tmp/sample_club_top.htmlの実際のimgタグで確認済み。APIパスとは別物)。
    そのため filename から組み立てる。filenameが無ければ url からファイル名部分だけ抜き出す。
    """
    if not isinstance(key_visual, dict):
        return None
    filename = key_visual.get("filename")
    if not filename:
        url = key_visual.get("url")
        if not url:
            return None
        filename = url.rsplit("/", 1)[-1]
    if not filename:
        return None
    return f"{SITE_ORIGIN}/images/media/{filename}"


def parse_club_page(html: str) -> dict:
    """クラブページのHTMLから news/players/clubStats/leaders/seasonalPerformances を抽出する。"""
    chunks = extract_next_chunks(html)

    # ニュース: "newsList"を持つ候補のうちlist型で最長のもの(トップページの埋め込みは直近数件のみ)
    news_candidates = [v for v in search_chunks(chunks, "newsList") if isinstance(v, list)]
    news_raw = max(news_candidates, key=len, default=[])
    news = []
    for n in news_raw:
        if not (isinstance(n, dict) and n.get("title")):
            continue
        key_visual = n.get("keyVisual")
        news.append(
            {
                "id": n.get("id"),
                "title": n.get("title"),
                "publishedJst": parse_publish_date(n.get("publishDisplayDate")),
                "imageUrl": build_media_url(key_visual),
            }
        )

    # 選手一覧: playerIdを持つ辞書のリストのうち最長のもの
    player_candidates = [
        v
        for v in search_chunks(chunks, "players")
        if isinstance(v, list) and v and isinstance(v[0], dict) and "playerId" in v[0]
    ]
    players_raw = max(player_candidates, key=len, default=[])
    players = [
        {
            "playerId": p.get("playerId"),
            "name": p.get("playerName"),
            "nameEn": p.get("playerNameEn"),
            "uniformNo": p.get("uniformNo"),
            "position": p.get("position"),
            "birthPlace": p.get("birthPlace"),
            "birthday": strip_date_prefix(p.get("birthday")),
            "height": p.get("height"),
            "weight": p.get("weight"),
            "isHomeGrown": p.get("isHomeGrown"),
            "totalGameCount": p.get("totalGameCount"),
            "totalGoalCount": p.get("totalGoalCount"),
        }
        for p in players_raw
        if isinstance(p, dict)
    ]

    # クラブスタッツ: 辞書の最初のシーズンキー(=サイトが現在表示しているもの)を採用
    stats_candidates = [v for v in search_chunks(chunks, "clubStatsInLeague") if isinstance(v, dict)]
    club_stats = None
    if stats_candidates and stats_candidates[0]:
        d = stats_candidates[0]
        season_key, season_stats = next(iter(d.items()))
        if isinstance(season_stats, dict):
            club_stats = {
                "seasonKey": season_key,
                "items": [
                    {"key": k, "label": v.get("label"), "value": v.get("rawValue"), "rank": v.get("rank")}
                    for k, v in season_stats.items()
                    if isinstance(v, dict)
                ],
            }

    # 得点/アシスト/デュエル勝利数トップ選手(同じくシーズンキーの最初を採用)
    ranking_candidates = [v for v in search_chunks(chunks, "playerStatsRanking") if isinstance(v, dict)]
    leaders = None
    if ranking_candidates and ranking_candidates[0]:
        d = ranking_candidates[0]
        _, season_ranking = next(iter(d.items()))
        if isinstance(season_ranking, dict):
            leaders = {
                cat: {
                    "label": info.get("label"),
                    "players": [
                        {
                            "rank": p.get("rank"),
                            "playerId": p.get("playerId"),
                            "name": p.get("name"),
                            "statsValue": p.get("statsValue"),
                        }
                        for p in info.get("players", [])
                        if isinstance(p, dict)
                    ],
                }
                for cat, info in season_ranking.items()
                if isinstance(info, dict) and cat != "clubName"
            }

    # シーズン別成績: 全グループ(リーグ戦/天皇杯/ルヴァン等)を集約し、
    # 「n位」表記のもの(=リーグ戦の順位)だけ残す。カップ戦の"ベスト8"等は対象外。
    perf_groups = search_chunks(chunks, "seasonalPerformances")
    seasonal = []
    seen = set()
    for items in perf_groups:
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            if not isinstance(result, dict):
                continue
            label = result.get("resultLabel", "")
            if not (isinstance(label, str) and label.endswith("位")):
                continue
            key = (item.get("year"), label)
            if key in seen:
                continue
            seen.add(key)
            seasonal.append(
                {
                    "year": item.get("year"),
                    "leagueName": result.get("leagueName"),
                    "resultLabel": label,
                }
            )

    return {
        "news": news,
        "players": players,
        "clubStats": club_stats,
        "leaders": leaders,
        "seasonalPerformances": seasonal,
    }


def fetch_club_html(slug: str) -> str:
    """失敗したら例外を投げる(呼び出し元でキャッチしてそのクラブだけスキップする設計)。"""
    import requests

    url = f"https://www.jleague.jp/club/{slug}/"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def build_club_extra(clubs: list[dict], fetch_fn=fetch_club_html, sleep_fn=time.sleep, log=print) -> dict:
    """
    全クラブぶんを取得する。ファイルI/Oはしない(テスト用に分離)。
    fetch_fnを差し替えれば実際のHTTPアクセス無しでテストできる。
    """
    out_clubs: dict[str, dict] = {}
    failed: list[str] = []

    for c in clubs:
        try:
            html = fetch_fn(c["slug"])
            data = parse_club_page(html)
            out_clubs[c["idTeam"]] = data
            log(f"[info] {c['ja']}({c['slug']}): news={len(data['news'])} players={len(data['players'])}")
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {c['ja']}({c['slug']}) の取得に失敗: {e}", file=sys.stderr)
            failed.append(f"{c['idTeam']}({c['ja']})")
        sleep_fn(SLEEP_BETWEEN_CLUBS)

    return {
        "meta": {
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "clubCount": len(clubs),
            "failed": failed,
        },
        "clubs": out_clubs,
    }


def main() -> None:
    clubs = load_clubs()
    if not clubs:
        print("[error] マスタからクラブのURLスラッグが1件も取れなかった", file=sys.stderr)
        sys.exit(1)

    out = build_club_extra(clubs)

    out_path = PROCESSED_DIR / "club_extra.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[info] {out_path} に書き出し "
        f"(clubCount={out['meta']['clubCount']}, failed={len(out['meta']['failed'])})"
    )


if __name__ == "__main__":
    main()
