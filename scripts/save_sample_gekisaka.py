"""
ゲキサカ「海外サッカー」ニュース一覧の生HTMLをサンプル保存する(1回だけ手元で実行する)。

なぜ要るか:
    第34弾で「過去1ヶ月ぶんの海外ニュースを遡って取り込みたい」という話になった。
    RSSフィードは最新20件しか返さず遡れない。Google News の期間指定検索(when:30d)は
    news.google.com の robots.txt が /rss/ を拒否しているため使わない方針にした。
    残るのが、この一覧ページの ?page= によるページ送り。
    robots.txt(2026-09-01確認)は User-agent:* に対して /search* のみ Disallow で、
    /article/foreign は対象外。Crawl-delay の指定も無い。

    Claude側からは web.gekisaka.jp に直接アクセスできないため、パーサを書く前に
    実物のマークアップを手元で保存してもらう(save_sample_html.py と同じ流儀)。

実行方法:
    python scripts/save_sample_gekisaka.py

data/tmp/ に以下を保存する:
    sample_gekisaka_foreign_p1.html   (1ページ目)
    sample_gekisaka_foreign_p2.html   (2ページ目: ページ送りが効くかの確認用)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "tmp"
BASE = "https://web.gekisaka.jp/article/foreign?news_type=news"
TARGETS = {
    "sample_gekisaka_foreign_p1.html": BASE,
    "sample_gekisaka_foreign_p2.html": BASE + "&page=2",
}
HEADERS = {"User-Agent": "jleague-app news fetcher (personal use)"}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in TARGETS.items():
        print(f"[info] 取得中: {url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"[error] {url} の取得に失敗: {e}", file=sys.stderr)
            continue
        resp.encoding = resp.apparent_encoding or resp.encoding
        path = OUT_DIR / filename
        path.write_text(resp.text, encoding="utf-8")
        print(f"[info] 保存: {path} ({len(resp.text):,}文字)")
        time.sleep(2.0)


if __name__ == "__main__":
    main()
