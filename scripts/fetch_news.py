"""
クラブ・OB選手のニュースを、複数の情報源から集めて news.json に書き出す。

情報源は4種類:
  1. Google News RSS  -- クラブ名(ja + aliasesJa)とOB選手名で個別に検索する(sourceType: "google")
  2. リーグ全体のRSSフィード(ゲキサカ国内・ゲキサカ海外・サッカーキング) -- 1回取得して、
     記事のタイトル+概要にクラブ名(ja/aliasesJaのみ。英語aliasesとshortは使わない)が
     含まれるかで、該当クラブに振り分ける(sourceType: "feed")。
     第33弾: どのクラブにも当たらなかった記事は捨てずに news.json の "world" に貯める。
     サッカーキングのフィードは実測(2026-08-31)で直近8件が8件とも海外サッカーで、
     Jリーグから海外へ出た選手の動向がそこに入っていたのに、クラブ名が無いという理由だけで
     全部落ちていた。アプリのマイニュース(キーワード検索)がこの箱も対象にする。
  3. サッカーダイジェストWeb(soccerdigestweb.com) -- 第14弾で追加。RSS配信が無いため、クラブ別の
     記事一覧ページ(https://www.soccerdigestweb.com/tag_list/tag_search=1&tag_id=<tag_id>)を
     直接HTML取得して.entryブロックを正規表現で抜き出す(sourceType: "soccerdigest")。
     tag_idはクラブごとに個別に調べる必要があり(サイト側に全60クラブの一覧ページが見当たらな
     かったため)、data/config/soccerdigest_tags.json に分かっている分だけ手で記載する
     (2026-08-22時点は湘南ベルマーレのみ。他クラブは同様のURLを開いて確認できたら追加)。
     robots.txt確認済み(2026-08時点): User-agent:* に対する/tag_list/や/news/detail/への
     Disallowは無い(SEO系ボット個別のDisallow: /、msnbot/bingbotのCrawl-delayのみ)。
     ページ1(最新15件程度)だけを見る。ページネーションまでは追わない(定期実行で十分追いつける)。
  4. クラブ公式サイトのニュース(club_extra.json) -- こちらはこのスクリプトでは扱わない。
     非公開前提のファイルであり、フロント側で別途表示している。

data/config/watchlist.json (手で編集するファイル) の teams は、Google Newsを個別クエリする
クラブを絞るために使う。空配列の場合は全60クラブが対象になる(記載したクラブに絞りたい
場合だけidTeamを書く)。ゲキサカ/サッカーキングの振り分けはteamsに関係なく常に全クラブが対象。
Google Newsのクエリ語はja+aliasesJaのうち、カタカナのみの愛称か3文字以上の語に限定する
(「鹿島」のような2文字の漢字語だけだと無関係な検索結果を拾いやすいため。この絞り込みは
クエリの組み立てだけに適用するもので、記事をクラブへ振り分ける際のマッチング側は
team_matching.match_teams_in_text() が別途、短い語も含めて最長一致消費法で正しく扱う)。

重複排除: URLの正規化(トラッキングパラメータ除去)が一致、またはタイトル完全一致のものは
同一記事とみなし、優先順位(ゲキサカ→サッカーキング→Google News)の高い方を残す。

累積: news.json は毎回上書きではなく、既存の内容に新規取得分をマージして書き出す
(RSSは直近数十件しか返らないため、1回の実行だけでは記事が増えない)。公開から60日を
超えた記事は掃除する。meta.totalItems/meta.newItemsで増加を確認できるようにしてある。

取得失敗(HTTPエラー・XMLパース失敗)は、そのクエリ/フィードだけスキップして続行する。
ニュースが取れないことでバッチ全体を落とさないこと。

CLI:
    python scripts/fetch_news.py
"""

from __future__ import annotations

import html as html_lib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402
from team_matching import load_master_teams, match_teams_in_text  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
CONFIG_PATH = BASE_DIR / "data" / "config" / "watchlist.json"
SOCCERDIGEST_CONFIG_PATH = BASE_DIR / "data" / "config" / "soccerdigest_tags.json"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

MASTER_FILES = {
    "j1": MASTERS_DIR / "j1_teams_2026-27.json",
    "j2": MASTERS_DIR / "j2_master_2026-27.json",
    "j3": MASTERS_DIR / "j3_teams_2026-27.json",
}

RSS_URL_TMPL = "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
MAX_ITEMS = 20
MAX_ITEMS_PER_TEAM = 100
# 第33弾: クラブに紐づかない記事(海外サッカーなど)を貯める枠の上限。
# クラブ別と違って全キーワードで共有する1本の箱なので、多めに持たせる。
# 1件およそ300バイトなので、400件でも120KB程度しか増えない。
MAX_ITEMS_WORLD = 400
NEWS_MAX_AGE_DAYS = 60
SLEEP_BETWEEN_QUERIES = 2.0
TIMEOUT = 15.0
HEADERS = {"User-Agent": "jleague-app news fetcher (personal use)"}

_KATAKANA_ONLY = re.compile(r"^[ァ-ヶー]+$")

# リーグ全体を扱うRSSフィード。robots.txt を確認済み(2026-08時点):
#   gekisaka: User-agent:* は /search* のみ disallow。/feed は対象外
#   soccer-king: User-agent:* は /js/ /nk- /movie のみ disallow。Crawl-delay: 10 が指定されている
FEED_SOURCES = [
    {"name": "ゲキサカ", "url": "https://web.gekisaka.jp/feed?category=domestic"},
    # 第33弾: 海外サッカー専用フィード。category=foreign で
    # 「ゲキサカ[講談社] › 海外サッカー」が返る(2026-08-31に実物で確認)。
    # 海外移籍した元Jリーグの選手を追うのが目的。
    {"name": "ゲキサカ(海外)", "url": "https://web.gekisaka.jp/feed?category=foreign"},
    {"name": "サッカーキング", "url": "https://www.soccer-king.jp/feed"},
]
FEED_CRAWL_DELAY = {"サッカーキング": 10.0}  # robots.txtのCrawl-delayに合わせる(既定はSLEEP_BETWEEN_QUERIES)

# 第14弾: サッカーダイジェストWeb(クラブ別ページ、RSSが無いのでHTMLを直接見にいく)
SOCCERDIGEST_NAME = "サッカーダイジェストWeb"
SOCCERDIGEST_TAG_URL_TMPL = "https://www.soccerdigestweb.com/tag_list/tag_search=1&tag_id={tag_id}"
_SD_ENTRY_SPLIT_RE = re.compile(r'<div class="entry">')
_SD_TITLE_LINK_RE = re.compile(r'<p class="title"><a href="([^"]+)">([^<]*)</a></p>')
_SD_DATE_RE = re.compile(r'<span class="date">(\d{4})年(\d{1,2})月(\d{1,2})日</span>')
_SD_IMAGE_RE = re.compile(r'<div class="pic">.*?<img src="([^"]+)"', re.DOTALL)

SOURCE_TYPE_PRIORITY = {"official": 0, "soccerdigest": 1, "feed": 2, "google": 3}
FEED_NAME_PRIORITY = {"ゲキサカ": 0, "サッカーキング": 1}

_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "yclid"}


def load_all_teams() -> list[dict]:
    """3リーグ全クラブを、team_matching.load_master_teams()で揃えた共通形式のlistにする。"""
    all_teams: list[dict] = []
    for league, path in MASTER_FILES.items():
        raw = json.loads(path.read_text(encoding="utf-8"))
        all_teams.extend(load_master_teams(league, raw))
    return all_teams


def build_news_queries(team: dict) -> list[str]:
    """
    Google News検索用のクエリ語を作る。ja(正式名)は常に含める。
    aliasesJaは、カタカナのみの愛称か3文字以上の語だけ採用する
    (「鹿島」のような2文字の漢字語は無関係な検索結果を拾いやすいため除外)。
    """
    candidates = [team.get("ja")] + list(team.get("aliasesJa", []))
    queries: list[str] = []
    seen: set[str] = set()
    for term in candidates:
        if not term or term in seen:
            continue
        if _KATAKANA_ONLY.match(term) or len(term) >= 3:
            queries.append(term)
            seen.add(term)
    return queries


def normalize_url(url: str) -> str:
    """重複排除の判定用に、トラッキングパラメータ(utm_*等)とフラグメントを除いたURLを作る。"""
    if not url:
        return url
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), urlencode(kept), ""))


def dedupe_news_items(items: list[dict]) -> list[dict]:
    """
    URL正規化が一致、またはタイトル完全一致のものを重複とみなして落とす。
    先に出てきたものを残すので、呼び出し側は残したい優先順(=優先度の高い順)に並べて渡すこと。
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []
    for item in items:
        url_key = normalize_url(item.get("link", ""))
        title_key = (item.get("title") or "").strip()
        if (url_key and url_key in seen_urls) or (title_key and title_key in seen_titles):
            continue
        if url_key:
            seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        out.append(item)
    return out


def _priority_key(item: dict) -> tuple[int, int]:
    stype = item.get("sourceType", "google")
    type_rank = SOURCE_TYPE_PRIORITY.get(stype, 9)
    feed_rank = FEED_NAME_PRIORITY.get(item.get("source"), 9) if stype == "feed" else 0
    return (type_rank, feed_rank)


def merge_and_dedupe(items: list[dict], max_items: int = MAX_ITEMS_PER_TEAM) -> list[dict]:
    """優先順位順で重複排除し、publishedJstの新しい順に並べ替えて上限件数に絞る。"""
    ordered_for_dedupe = sorted(items, key=_priority_key)
    deduped = dedupe_news_items(ordered_for_dedupe)
    deduped.sort(key=lambda it: it.get("publishedJst") or "", reverse=True)
    return deduped[:max_items]


def _item_identity(item: dict) -> str:
    """記事の同一性判定キー(dedupe_news_itemsと同じ考え方: URL正規化優先、無ければタイトル)。"""
    url_key = normalize_url(item.get("link", ""))
    title_key = (item.get("title") or "").strip()
    return url_key or title_key


def accumulate_items(
    existing_items: list[dict],
    new_items: list[dict],
    max_items: int = MAX_ITEMS_PER_TEAM,
    cutoff_iso: str | None = None,
) -> tuple[list[dict], int]:
    """
    既存の記事一覧(前回実行までの蓄積)に、今回新規取得分をマージする。
    publishedJstがcutoff_isoより古い記事は捨てる(日付が取れない記事は捨てない)。
    戻り値は (マージ後の記事一覧, 今回新たに増えた件数)。
    """
    existing_keys = {_item_identity(it) for it in existing_items}
    combined = existing_items + new_items
    if cutoff_iso:
        combined = [
            it for it in combined
            if not it.get("publishedJst") or it["publishedJst"] >= cutoff_iso
        ]
    merged = merge_and_dedupe(combined, max_items=max_items)
    new_count = sum(1 for it in merged if _item_identity(it) not in existing_keys)
    return merged, new_count


def parse_pubdate_to_jst(pubdate: str) -> str | None:
    """RFC822形式(例: 'Mon, 10 Aug 2026 12:00:00 GMT')をJSTのISO8601文字列に変換する。"""
    try:
        dt = parsedate_to_datetime(pubdate)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        # 稀にnaiveで返る実装もあるため、RFC822はUTC/GMT前提として扱う
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).isoformat()


def _parse_rss_items(xml_bytes: bytes) -> list[dict]:
    """RSS 2.0のitem一覧を共通形式(title/link/publishedJst/description/source)でパースする。"""
    root = ET.fromstring(xml_bytes)
    items: list[dict] = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pubdate_el = item.find("pubDate")
        desc_el = item.find("description")
        source_el = item.find("source")

        title = title_el.text if title_el is not None else None
        link = link_el.text if link_el is not None else None
        pubdate_raw = pubdate_el.text if pubdate_el is not None else None
        desc = desc_el.text if desc_el is not None else None
        source = source_el.text if source_el is not None else None

        if not title or not link:
            continue

        items.append({
            "title": title,
            "link": link,
            "publishedJst": parse_pubdate_to_jst(pubdate_raw) if pubdate_raw else None,
            "description": desc or "",
            "source": source,
        })
    return items


def fetch_rss(query: str) -> list[dict]:
    """
    Google News RSSを1クエリぶん取得する(最大MAX_ITEMS件、pubDateの新しい順)。
    失敗したら例外を投げる(呼び出し元でキャッチしてそのクエリだけスキップする設計)。
    """
    import requests

    url = RSS_URL_TMPL.format(q=quote(query))
    resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    items = _parse_rss_items(resp.content)
    items.sort(key=lambda x: x["publishedJst"] or "", reverse=True)
    return items[:MAX_ITEMS]


def fetch_feed(url: str) -> list[dict]:
    """
    ゲキサカ/サッカーキングなど、リーグ全体を扱うRSSフィードを1回取得する。
    Google Newsと違いクエリ文字列は組み立てない(フィードURLをそのまま1回叩くだけ)。
    失敗したら例外を投げる(呼び出し元でキャッチしてそのフィードだけスキップする設計)。
    """
    import requests

    resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    return _parse_rss_items(resp.content)


def parse_soccerdigest_entries(page_html: str) -> list[dict]:
    """
    サッカーダイジェストWebのクラブ別記事一覧ページ(1ページ目)から、記事ブロック
    (<div class="entry">...</div>)を正規表現で抜き出して共通形式にする。
    サイト側はRSSを配信していないため、HTMLのマークアップに直接依存する(match_events_parser.py
    と同じ考え方)。マークアップが変わって0件になっても例外は投げない(呼び出し側で件数を見て
    判断できるよう、単に空リストを返す)。
    日付は「YYYY年MM月DD日」の日付のみで時刻は無いため、00:00:00 JSTとして扱う。
    """
    chunks = _SD_ENTRY_SPLIT_RE.split(page_html)[1:]  # 先頭要素は最初の.entryより前の部分なので捨てる
    items: list[dict] = []
    for chunk in chunks:
        m_title = _SD_TITLE_LINK_RE.search(chunk)
        if not m_title:
            continue
        link, raw_title = m_title.group(1), m_title.group(2)
        title = html_lib.unescape(raw_title).strip()
        if not title:
            continue

        published = None
        m_date = _SD_DATE_RE.search(chunk)
        if m_date:
            year, month, day = (int(g) for g in m_date.groups())
            try:
                published = datetime(year, month, day, tzinfo=JST).isoformat()
            except ValueError:
                published = None

        item = {
            "title": title,
            "link": link,
            "publishedJst": published,
            "source": SOCCERDIGEST_NAME,
            "sourceType": "soccerdigest",
        }
        m_image = _SD_IMAGE_RE.search(chunk)
        if m_image:
            item["imageUrl"] = html_lib.unescape(m_image.group(1))
        items.append(item)
    return items


def fetch_soccerdigest_tag(tag_id: str) -> list[dict]:
    """
    サッカーダイジェストWebの、指定tag_id(=クラブ)の記事一覧ページ1ページ目を取得してパースする。
    失敗したら例外を投げる(呼び出し元でキャッチしてそのクラブだけスキップする設計、他ソースと同じ)。
    """
    import requests

    url = SOCCERDIGEST_TAG_URL_TMPL.format(tag_id=tag_id)
    resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    return parse_soccerdigest_entries(resp.text)


def load_soccerdigest_tags(log=print) -> dict[str, str]:
    """data/config/soccerdigest_tags.json (idTeam -> tag_id) を読む。無ければ空dict。"""
    if not SOCCERDIGEST_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(SOCCERDIGEST_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log(f"[warn] {SOCCERDIGEST_CONFIG_PATH}の読み込みに失敗、空として扱う: {e}", file=sys.stderr)
        return {}
    return data.get("tags", {})


def classify_feed_items(
    items: list[dict], all_teams: list[dict], source_name: str
) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    リーグ全体フィードの記事を、タイトル+概要にクラブ名(ja/aliases)が含まれるかでクラブへ振り分ける。
    1本の記事が複数クラブにヒットしてもよい(移籍記事など)。

    戻り値は (クラブ別, どのクラブにもヒットしなかった記事) の組。

    第33弾まで、ヒットしなかった記事はその場で捨てていた。ところが調べてみると
    サッカーキングのフィードは直近8件が8件とも海外サッカーで(2026-08-31に実物で確認)、
    菅原由勢のカリアリ移籍や高井幸大のシント=トロイデン移籍といった、まさに
    「Jリーグから海外へ出た選手の動向」がそこに入っていた。Jクラブ名が本文に無いという
    理由だけで全部落ちていたことになる。捨てずに返し、マイニュースの検索対象に加える。
    """
    by_team: dict[str, list[dict]] = {}
    unmatched: list[dict] = []
    for item in items:
        text = (item.get("title") or "") + " " + (item.get("description") or "")
        entry = {
            "title": item["title"],
            "link": item["link"],
            "publishedJst": item["publishedJst"],
            "source": source_name,
            "sourceType": "feed",
        }
        teams = match_teams_in_text(text, all_teams)
        if not teams:
            unmatched.append(entry)
            continue
        for team in teams:
            by_team.setdefault(team["idTeam"], []).append(dict(entry))
    return by_team, unmatched


def build_news(
    watchlist: dict,
    all_teams: list[dict],
    soccerdigest_tags: dict[str, str] | None = None,
    fetch_query_fn=fetch_rss,
    fetch_feed_fn=fetch_feed,
    fetch_soccerdigest_fn=fetch_soccerdigest_tag,
    sleep_fn=time.sleep,
    log=print,
) -> dict:
    """
    4つの経路(ゲキサカ/サッカーキングの振り分け、サッカーダイジェストWeb、watchlist記載クラブの
    Google News、OB選手)からニュースを集める。ファイルI/Oはしない(テスト用に分離)。
    fetch_query_fn/fetch_feed_fn/fetch_soccerdigest_fnを差し替えれば実際のHTTPアクセス無しで
    テストできる。
    """
    soccerdigest_tags = soccerdigest_tags or {}
    team_lookup_by_id = {t["idTeam"]: t for t in all_teams}
    per_team: dict[str, list[dict]] = {t["idTeam"]: [] for t in all_teams}
    failed: list[str] = []
    query_count = 0

    # 1. リーグ全体フィード(全クラブが対象。watchlistは関係ない)
    world_out: list[dict] = []
    for feed in FEED_SOURCES:
        try:
            items = fetch_feed_fn(feed["url"])
            classified, unmatched = classify_feed_items(items, all_teams, feed["name"])
            hit_count = sum(len(v) for v in classified.values())
            for tid, arts in classified.items():
                per_team[tid].extend(arts)
            world_out.extend(unmatched)   # 第33弾: クラブに紐づかない記事(海外など)
            log(f"[info] {feed['name']}: {len(items)}件取得、{hit_count}件をクラブに割り当て、"
                f"{len(unmatched)}件をworldへ")
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {feed['name']}の取得に失敗: {e}", file=sys.stderr)
            failed.append(f"feed:{feed['name']}")
        sleep_fn(FEED_CRAWL_DELAY.get(feed["name"], SLEEP_BETWEEN_QUERIES))

    # 2. サッカーダイジェストWeb(tag_idが分かっているクラブだけ。全60クラブ分は無い)
    for id_team, tag_id in soccerdigest_tags.items():
        team = team_lookup_by_id.get(id_team)
        team_label = team["ja"] if team else id_team
        try:
            items = fetch_soccerdigest_fn(tag_id)
            per_team.setdefault(id_team, []).extend(items)
            log(f"[info] {SOCCERDIGEST_NAME}「{team_label}」(tag_id={tag_id}): {len(items)}件")
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {SOCCERDIGEST_NAME}「{team_label}」(tag_id={tag_id})の取得に失敗: {e}", file=sys.stderr)
            failed.append(f"soccerdigest:{id_team}({team_label})")
        sleep_fn(SLEEP_BETWEEN_QUERIES)

    # 3. Google Newsで個別クエリするクラブ。teamsが空なら全クラブを対象にする
    #    (記載したクラブに絞りたい場合だけwatchlist.jsonにidTeamを書く)
    target_team_ids = watchlist.get("teams") or [t["idTeam"] for t in all_teams]
    for id_team in target_team_ids:
        team = team_lookup_by_id.get(id_team)
        if team is None:
            log(f"[warn] idTeam={id_team} はどのマスタにも見つからない。スキップ", file=sys.stderr)
            continue
        queries = build_news_queries(team)
        for q in queries:
            query_count += 1
            try:
                items = fetch_query_fn(q)
                for it in items:
                    per_team[id_team].append({
                        "title": it["title"],
                        "link": it["link"],
                        "publishedJst": it["publishedJst"],
                        "source": it.get("source") or "Google News",
                        "sourceType": "google",
                    })
                log(f"[info] クラブ「{team['ja']}」クエリ「{q}」: {len(items)}件")
            except Exception as e:  # noqa: BLE001
                log(f"[warn] クラブ「{team['ja']}」クエリ「{q}」の取得に失敗: {e}", file=sys.stderr)
                failed.append(f"team:{id_team}({team['ja']}):{q}")
            sleep_fn(SLEEP_BETWEEN_QUERIES)

    teams_out: dict[str, list[dict]] = {
        tid: merge_and_dedupe(arts) for tid, arts in per_team.items() if arts
    }

    # 4. OB選手(従来どおりGoogle Newsのみ)
    ob_players_out: dict[str, list[dict]] = {}
    for player in watchlist.get("obPlayers", []):
        if not player.get("enabled", True):
            continue
        name = player.get("name")
        if not name:
            log("[warn] name の無いobPlayersエントリをスキップ", file=sys.stderr)
            continue
        extra = player.get("extraQuery")
        query = f"{name} {extra}" if extra else name
        query_count += 1
        try:
            ob_players_out[name] = fetch_query_fn(query)
            log(f"[info] OB選手「{name}」: {len(ob_players_out[name])}件")
        except Exception as e:  # noqa: BLE001
            log(f"[warn] OB選手「{name}」の取得に失敗: {e}", file=sys.stderr)
            failed.append(f"obPlayer:{name}")
        sleep_fn(SLEEP_BETWEEN_QUERIES)

    return {
        "meta": {
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "queryCount": query_count,
            "failed": failed,
        },
        "teams": teams_out,
        "obPlayers": ob_players_out,
        # 第33弾: どのクラブにも紐づかなかったフィード記事(海外サッカーなど)。
        # クラブ別と違って1本のリストで持つ(アプリ側はキーワード検索にだけ使う)。
        "world": dedupe_news_items(world_out),
    }


def load_existing_news(out_path: Path, log=print) -> dict:
    """前回実行分のnews.jsonを読む。無い/壊れている場合は空として扱う(バッチは止めない)。"""
    if not out_path.exists():
        return {}
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log(f"[warn] 既存の{out_path}の読み込みに失敗、新規として扱う: {e}", file=sys.stderr)
        return {}


def merge_with_existing(existing: dict, fresh: dict, cutoff_iso: str) -> dict:
    """
    前回までの蓄積(existing)と今回の新規取得(fresh)をマージする。
    公開から NEWS_MAX_AGE_DAYS 日を超えた記事は捨て、クラブ/OB選手ごとに件数上限で切る。
    meta.totalItems(最終件数)とmeta.newItems(今回増えた件数)を付与する。
    """
    existing_teams = existing.get("teams", {})
    existing_ob = existing.get("obPlayers", {})

    merged_teams: dict[str, list[dict]] = {}
    new_items_total = 0

    all_team_ids = set(existing_teams) | set(fresh["teams"])
    for tid in all_team_ids:
        merged, new_count = accumulate_items(
            existing_teams.get(tid, []), fresh["teams"].get(tid, []), MAX_ITEMS_PER_TEAM, cutoff_iso
        )
        if merged:
            merged_teams[tid] = merged
        new_items_total += new_count

    merged_ob: dict[str, list[dict]] = {}
    all_ob_names = set(existing_ob) | set(fresh["obPlayers"])
    for name in all_ob_names:
        merged, new_count = accumulate_items(
            existing_ob.get(name, []), fresh["obPlayers"].get(name, []), MAX_ITEMS_PER_TEAM, cutoff_iso
        )
        if merged:
            merged_ob[name] = merged
        new_items_total += new_count

    merged_world, world_new = accumulate_items(
        existing.get("world", []), fresh.get("world", []), MAX_ITEMS_WORLD, cutoff_iso
    )
    new_items_total += world_new

    total_items = (sum(len(v) for v in merged_teams.values())
                   + sum(len(v) for v in merged_ob.values())
                   + len(merged_world))

    return {
        "meta": {
            **fresh["meta"],
            "totalItems": total_items,
            "newItems": new_items_total,
        },
        "teams": merged_teams,
        "obPlayers": merged_ob,
        "world": merged_world,
    }


def main() -> None:
    if not CONFIG_PATH.exists():
        print(f"[error] {CONFIG_PATH} が無い", file=sys.stderr)
        sys.exit(1)

    watchlist = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    all_teams = load_all_teams()
    soccerdigest_tags = load_soccerdigest_tags()

    out_path = PROCESSED_DIR / "news.json"
    existing = load_existing_news(out_path)

    fresh = build_news(watchlist, all_teams, soccerdigest_tags=soccerdigest_tags)

    cutoff_iso = (datetime.now(JST) - timedelta(days=NEWS_MAX_AGE_DAYS)).isoformat()
    out = merge_with_existing(existing, fresh, cutoff_iso)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[info] {out_path} に書き出し "
        f"(queryCount={out['meta']['queryCount']}, failed={len(out['meta']['failed'])}, "
        f"クラブ別ニュース={len(out['teams'])}クラブ, "
        f"totalItems={out['meta']['totalItems']}, newItems={out['meta']['newItems']})"
    )


if __name__ == "__main__":
    main()
