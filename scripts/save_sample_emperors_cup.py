"""
天皇杯フェーズ2(カード/交代/出場メンバー/ハイライト動画)の実装準備。
1回だけ手元で実行して、JFAの個別試合ページの生HTMLをサンプル保存する
(Claude側は jfa.jp に直接アクセスできないため。scripts/save_sample_html.py と同じ役割)。

天皇杯の個別試合ページは schedule.json のようなJSON APIを持たず、HTMLに直接
出場メンバー・カード・交代が入っている(WebFetchで内容が見えることを確認済み)。
そのためHTMLの構造をこちらで確認してからパーサを書く必要がある。

実行方法:
    python scripts/save_sample_emperors_cup.py

data/tmp/ に以下を保存する(.gitignore対象なのでコミット不要):
    sample_ec_2026_m56.html  2026年2回戦 湘南ベルマーレ vs ラインメール青森。
                             カード(49分の警告)と交代が入っている基本形。ハイライト動画は無さそう。
    sample_ec_2026_m1.html   2026年1回戦 アトレチコ鈴鹿 vs FC BASARA HYOGO。
                             延長+PK決着の試合。PKや延長の表記がどう出るかの確認用。
    sample_ec_2025_m66.html  2025年3回戦 湘南ベルマーレ vs 清水エスパルス。
                             第14弾の調査で「ハイライト動画(YouTube)が載っている」と確認済みの試合。
                             動画IDがHTMLのどこに入るかを見るための本命サンプル。
    sample_ec_2025_m87.html  2025年大会の最終試合(=決勝と思われる)。ラウンドが進んだ試合の
                             ページ構成が1〜3回戦と同じか確認する用。存在しなければスキップされる。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "data" / "tmp"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jleague-app personal use"}


def page_url(year: int, number: int) -> str:
    return f"https://www.jfa.jp/match/emperorscup_{year}/match_page/m{number}.html"


TARGETS = {
    "sample_ec_2026_m56.html": page_url(2026, 56),
    "sample_ec_2026_m1.html": page_url(2026, 1),
    "sample_ec_2025_m66.html": page_url(2025, 66),
    "sample_ec_2025_m87.html": page_url(2025, 87),
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in TARGETS.items():
        print(f"[info] 取得中: {url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {url} の取得に失敗(スキップ): {e}", file=sys.stderr)
            continue
        out_path = OUT_DIR / filename
        out_path.write_text(resp.text, encoding="utf-8")
        print(f"[info] 保存: {out_path} ({len(resp.text)} 文字)")
        time.sleep(2)

    print("\n完了。data/tmp/ に保存しました(コミット不要、.gitignoreで除外されています)。")


if __name__ == "__main__":
    main()
