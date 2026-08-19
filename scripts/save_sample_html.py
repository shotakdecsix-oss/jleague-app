"""
公式サイトスクレイピング実装の準備。1回だけ手元で実行して、
生HTMLをサンプル保存する(Claude側は jleague.jp に直接アクセスできないため)。

実行方法:
    python scripts/save_sample_html.py

data/tmp/ に以下を保存する:
    sample_club_top.html    (クラブ概要ページ: スタッツ/ニュース/シーズン別成績)
    sample_club_player.html (選手一覧ページ)
    sample_match_detail.html (個別試合ページ: 第6弾Bの調査用。埋め込みJSONに経過時間・イベントが
                               入っているか確認する。できれば2026-08-21 19:00キックオフ後に取り直すこと。
                               キックオフ前に取ると試合開始前の状態しか見えない)
    sample_match_list.html   (今週の日程一覧ページ: 個別試合ページのURL[6桁コード]がhrefとして
                               採取できるか確認する用)
    sample_sp_game.html      (スマホ版「今日の試合速報」ページ: club/matchページと同じNext.js系統か
                               確認する用。試合が無い日は空表示になるので、試合がある日に取り直すこと)
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
    "sample_match_detail.html": "https://www.jleague.jp/match/j1/2026/082102/",
    "sample_match_list.html": "https://www.jleague.jp/j1/match/",
    "sample_sp_game.html": "https://www.jleague.jp/sp/game/",
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

    print("\n完了。data/tmp/ の2ファイルをそのまま次のメッセージで教えてください。")


if __name__ == "__main__":
    main()
