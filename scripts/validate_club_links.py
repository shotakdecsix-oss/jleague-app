"""
マスタの jleagueSlug が本当にそのクラブのページを指しているかを検証する。

Jリーグ公式の全60クラブページを1件ずつ取得し、ページ内にマスタの日本語クラブ名が
出てくるかを確認する。全角/半角の揺れ(ＦＣ東京 vs FC東京)はNFKC正規化で吸収する。

    python scripts/validate_club_links.py

栃木SC(tochigi) と 栃木シティ(tochigic) のような紛らわしい組み合わせが実在するので、
マスタを触ったら必ずこれを通すこと。
"""

from __future__ import annotations

import json
import sys
import time
import unicodedata
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
MASTER_FILES = [
    MASTERS_DIR / "j1_teams_2026-27.json",
    MASTERS_DIR / "j2_master_2026-27.json",
    MASTERS_DIR / "j3_teams_2026-27.json",
]

SLEEP = 1.0  # 公式サイトに負荷をかけない
TIMEOUT = 15.0
HEADERS = {"User-Agent": "jleague-app club link validator (personal use)"}


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("　", "")


def main() -> None:
    ng: list[str] = []
    total = 0

    for path in MASTER_FILES:
        data = json.loads(path.read_text(encoding="utf-8"))
        league = path.stem
        for team in data["teams"]:
            total += 1
            ja = team["ja"]
            url = team.get("playersUrl")
            if not url:
                ng.append(f"{league} {ja}: playersUrl が無い")
                continue

            page_url = url.split("#")[0]
            try:
                resp = requests.get(page_url, timeout=TIMEOUT, headers=HEADERS)
            except requests.RequestException as e:  # noqa: BLE001
                ng.append(f"{league} {ja}: 取得失敗 {e}")
                time.sleep(SLEEP)
                continue

            if resp.status_code != 200:
                ng.append(f"{league} {ja}: HTTP {resp.status_code} {page_url}")
            elif norm(ja) not in norm(resp.text):
                ng.append(f"{league} {ja}: ページ内にクラブ名が見つからない {page_url}")
            else:
                print(f"[ok] {league} {ja} -> {page_url}")

            time.sleep(SLEEP)

    print(f"\n検証{total}件 / NG {len(ng)}件")
    if ng:
        for line in ng:
            print(f"[NG] {line}", file=sys.stderr)
        sys.exit(1)
    print("全クラブのリンクがマスタと一致しました。")


if __name__ == "__main__":
    main()
