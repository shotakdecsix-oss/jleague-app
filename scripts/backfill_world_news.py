"""
ゲキサカ「海外サッカー」ニュース一覧をページ送りで遡り、news.json の world を過去ぶんで埋める。
1回だけ手で走らせる想定の補助スクリプト(定期実行には入れない)。

なぜ要るか:
    第33弾で world(Jクラブに紐づかないフィード記事)を貯め始めたが、RSSは最新20件しか
    返さないので、マイニュースで過去を検索しても何も当たらない。
    Google Newsの期間指定検索(when:30d)は news.google.com の robots.txt が /rss/ を
    拒否しているため使わない。代わりにゲキサカの海外ニュース一覧を使う。
    robots.txt(2026-09-01確認)は User-agent:* に対して /search* のみ Disallow で、
    /article/foreign は対象外。Crawl-delayの指定も無いが、こちらから2秒空ける。

一覧ページの読み方(2026-09-01に実物のHTMLで確認):
    <div class="article-block" id="n457979">
      <a href="//web.gekisaka.jp/news/world/detail/?457979-457979-fl">
        <div class="thumbnail news"><img src="...news_457979_1.webp?time=20260901110510"></div>
        <div class="article-info"><div class="title">堂安律がサウジ移籍を拒否か…</div></div>

    公開日時そのものは一覧に出ていない。代わりにサムネイル画像URLの ?time= が
    YYYYMMDDHHMMSS で入っており、これを公開日時として使う。
    厳密には画像の更新時刻なので、記事IDの順とわずかに前後することがある(実測で数十分程度)。
    並び順が数十分ずれるだけなので、検索用途には十分と判断した。

    そして都合の良いことに、?time= の有無が「読み物記事かどうか」の判定になっている。
    サンプル36件のうち ?time= を持つのはちょうど18件で、それが
    「〇〇vs〇〇 試合記録」「〇〇vs〇〇 スタメン発表」を除いた18件と完全に一致した。
    定型記事は見出しに選手名が入らずマイニュースでは当たらないので、拾わない方が良い。
    タイトルの文言で弾くルールを別に持たずに済むので、?time= の有無だけで判定する。

CLI:
    python scripts/backfill_world_news.py                 直近30日ぶん
    python scripts/backfill_world_news.py --days 60       日数を指定
    python scripts/backfill_world_news.py --dry-run       取得して件数を出すだけ(書き込まない)
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time as time_mod
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402
from fetch_news import accumulate_items, dedupe_news_items  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
NEWS_PATH = BASE_DIR / "data" / "processed" / "news.json"

LIST_URL = "https://web.gekisaka.jp/article/foreign?news_type=news"
SOURCE_NAME = "ゲキサカ(海外)"   # fetch_news.py の FEED_SOURCES と同じ名前にして重複除去を効かせる
HEADERS = {"User-Agent": "jleague-app news fetcher (personal use)"}
TIMEOUT = 20.0
SLEEP_BETWEEN_PAGES = 2.0
DEFAULT_DAYS = 30
DEFAULT_MAX_PAGES = 60      # 安全弁。1ページ18件・1日あたり約2ページなので30日で30〜35ページの想定
MAX_WORLD_ITEMS = 600       # fetch_news.MAX_ITEMS_WORLD と揃えること

_BLOCK_RE = re.compile(r'<div class="article-block"\s+id="n(\d+)">(.*?)</div>\s*</a>\s*</div>', re.S)
_TITLE_RE = re.compile(r'<div class="title">\s*(.*?)\s*</div>', re.S)
_TIME_RE = re.compile(r'\?time=(\d{14})')
_HREF_RE = re.compile(r'href="([^"]+)"')


def parse_list_page(html: str) -> list[dict]:
    """一覧ページのHTMLから記事を抜き出す。?time= を持たない記事(定型記事)は捨てる。

    戻り値は news.json の world と同じ形。source/sourceType もフィード側と揃える。
    """
    items: list[dict] = []
    for m in _BLOCK_RE.finditer(html):
        body = m.group(2)
        tm = _TIME_RE.search(body)
        if not tm:
            continue  # 「〇〇vs〇〇 試合記録」等。見出しに選手名が入らないので拾わない
        title_m = _TITLE_RE.search(body)
        href_m = _HREF_RE.search(body)
        if not title_m or not href_m:
            continue
        try:
            dt = datetime.strptime(tm.group(1), "%Y%m%d%H%M%S").replace(tzinfo=JST)
        except ValueError:
            continue
        href = href_m.group(1)
        if href.startswith("//"):
            href = "https:" + href
        items.append({
            "title": html_lib.unescape(re.sub(r"\s+", " ", title_m.group(1)).strip()),
            "link": href,
            "publishedJst": dt.isoformat(timespec="seconds"),
            "source": SOURCE_NAME,
            "sourceType": "feed",
        })
    return items


def fetch_page(page: int) -> str:
    url = LIST_URL if page == 1 else f"{LIST_URL}&page={page}"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def collect(days: int, max_pages: int, fetch_fn=fetch_page, sleep_fn=time_mod.sleep, log=print) -> list[dict]:
    """cutoffより古い記事に届くまでページを送る。取得できたものを全部返す(重複はあとで除く)。"""
    cutoff = (datetime.now(JST) - timedelta(days=days)).isoformat(timespec="seconds")
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            items = parse_list_page(fetch_fn(page))
        except Exception as e:  # noqa: BLE001
            log(f"[warn] page={page} の取得に失敗、ここで打ち切る: {e}", file=sys.stderr)
            break
        if not items:
            log(f"[info] page={page}: 記事0件。ここで打ち切る")
            break
        out.extend(items)
        oldest = min(i["publishedJst"] for i in items)
        log(f"[info] page={page}: {len(items)}件 (最古 {oldest[:16]})")
        if oldest < cutoff:
            log(f"[info] {days}日前({cutoff[:16]})に到達。取得を終える")
            break
        if page < max_pages:
            sleep_fn(SLEEP_BETWEEN_PAGES)
    else:
        log(f"[warn] max_pages={max_pages} に達した。もっと遡るなら --max-pages を増やすこと",
            file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    fresh = dedupe_news_items(collect(args.days, args.max_pages))
    print(f"\n[info] 取得: {len(fresh)}件(重複除去後)")
    if not fresh:
        print("[warn] 1件も取れなかった。一覧ページの作りが変わった可能性がある", file=sys.stderr)
        sys.exit(1)

    news = json.loads(NEWS_PATH.read_text(encoding="utf-8"))
    before = len(news.get("world") or [])
    cutoff = (datetime.now(JST) - timedelta(days=args.days)).isoformat(timespec="seconds")
    merged, new_count = accumulate_items(news.get("world") or [], fresh, MAX_WORLD_ITEMS, cutoff)
    print(f"[info] world: {before}件 -> {len(merged)}件 (新規 {new_count}件)")

    if args.dry_run:
        print("[info] --dry-run なので書き込まない")
        return
    news["world"] = merged
    NEWS_PATH.write_text(json.dumps(news, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[info] {NEWS_PATH} を更新した。このあと build_dist.py を実行すること")


if __name__ == "__main__":
    main()
