"""
ルヴァンカップの生HTMLをサンプル保存する(1回だけ手元で実行する)。

なぜ要るか:
    第36弾で天皇杯と同じようにルヴァンカップを入れることにした。
    調べたところ、個別試合ページのURLはJ1〜J3とまったく同じ形
    (https://www.jleague.jp/match/leaguecup/2026/090201/)で、日程一覧も
    https://www.jleague.jp/match/leaguecup/ にある。得点者・カード・交代・出場メンバー・
    ハイライト動画のパーサ(match_events_parser.py)はそのまま流用できる見込み。

    新しく要るのは「日程・結果一覧」の方。天皇杯はJFAのJSON APIから日付・会場・ラウンド名・
    スコアまで構造化データで取れたが、ルヴァンはJリーグ主催でそのAPIが無く、
    日程ページ(Next.jsのRSCペイロード)から抜くことになる。
    Claude側からは jleague.jp に直接アクセスできないため、パーサを書く前に実物を保存する
    (save_sample_html.py と同じ流儀)。

実行方法:
    python scripts/save_sample_leaguecup.py

data/tmp/ に以下を保存する:
    sample_leaguecup_index.html   (日程一覧。日付・会場・ラウンド名がどう入っているかの確認用)
    sample_leaguecup_match.html   (個別試合のテキスト速報ページ。既存パーサが効くかの確認用。
                                     一覧から拾った最初の試合コードを使う)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_official import extract_next_chunks  # noqa: E402
from match_events_parser import extract_schedule_index  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "tmp"
INDEX_URL = "https://www.jleague.jp/match/leaguecup/"
HEADERS = {"User-Agent": "jleague-app news fetcher (personal use)"}


def get(url: str) -> str:
    print(f"[info] 取得中: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_html = get(INDEX_URL)
    (OUT_DIR / "sample_leaguecup_index.html").write_text(index_html, encoding="utf-8")
    print(f"[info] 保存: sample_leaguecup_index.html ({len(index_html):,}文字)")

    entries = extract_schedule_index(extract_next_chunks(index_html))
    print(f"[info] 一覧から拾えた試合: {len(entries)}件")
    for e in entries[:5]:
        print(f"    {e['league']}/{e['year']}/{e['code']}  {e.get('home')} vs {e.get('away')}")
    if not entries:
        print("[warn] 試合が1件も拾えなかった。個別試合ページの保存はスキップする", file=sys.stderr)
        return

    time.sleep(2.0)
    e = entries[0]
    url = f"https://www.jleague.jp/match/{e['league']}/{e['year']}/{e['code']}/livetxt/"
    try:
        html = get(url)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 個別試合ページの取得に失敗: {exc}", file=sys.stderr)
        return
    (OUT_DIR / "sample_leaguecup_match.html").write_text(html, encoding="utf-8")
    print(f"[info] 保存: sample_leaguecup_match.html ({len(html):,}文字)")


if __name__ == "__main__":
    main()
