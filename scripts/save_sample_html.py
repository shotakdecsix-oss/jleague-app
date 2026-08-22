"""
公式サイトスクレイピング実装の準備。1回だけ手元で実行して、
生HTMLをサンプル保存する(Claude側は jleague.jp に直接アクセスできないため)。

実行方法:
    python scripts/save_sample_html.py

data/tmp/ に以下を保存する:
    sample_club_top.html      (クラブ概要ページ: スタッツ/ニュース/シーズン別成績)
    sample_club_player.html   (選手一覧ページ)
    sample_match_schedule.html (その日のJ2試合一覧ページ: 個別試合ページのURL[6桁コード]が
                                 対戦カード名と一緒に採取できるか確認する用)
    sample_match_review.html  (終了済み試合の「試合結果・データ」ページ: 得点者・時間が
                                埋め込みJSONに入っているか確認する用。2026-02-14の宮崎vs湘南、消化済み)
    sample_match_livetxt.html (進行中/直近の試合の「テキスト速報」ページ: 得点・カード・交代が
                                リアルタイムで採取できるか確認する用。2026-08-22 15:00キックオフの
                                札幌vs大宮、既に複数得点が入っている状態で取得する)
    sample_match_base.html    (個別試合ページのトップ(#lineup等のアンカーがある基点URL)。
                                第14弾: ベンチメンバー(控え選手)のデータがlivetxt/review側の
                                formationsには含まれていない(スタメン11人×2のみ)ため、
                                こちらに埋め込まれているか確認する用)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "data" / "tmp"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jleague-app personal use"}

TARGETS = {
    "sample_club_top.html": "https://www.jleague.jp/club/shonan/",
    "sample_club_player.html": "https://www.jleague.jp/club/shonan/player/",
    "sample_match_schedule.html": "https://www.jleague.jp/match/j2/",
    "sample_match_review.html": "https://www.jleague.jp/match/j2j3/2026/021409/review/",
    "sample_match_livetxt.html": "https://www.jleague.jp/match/j2/2026/082208/livetxt/",
    "sample_match_base.html": "https://www.jleague.jp/match/j2/2026/082208/",
}


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
        out_path = OUT_DIR / filename
        out_path.write_text(resp.text, encoding="utf-8")
        print(f"[info] 保存: {out_path} ({len(resp.text)} 文字)")
        time.sleep(2)

    print("\n完了。data/tmp/ に保存しました(コミット不要、.gitignoreで除外されています)。")


if __name__ == "__main__":
    main()
