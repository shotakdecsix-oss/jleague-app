"""
サッカーダイジェストWebのクラブ別タグ(tag_id)を総当たりで発見し、対応表の候補を作る。
1回だけ手で走らせる補助スクリプト(定期実行には入れない)。

なぜ要るか:
    fetch_news.py は data/config/soccerdigest_tags.json に書かれたクラブだけを取りに行くが、
    2026-08-22時点で湘南(142)の1件しか判明しておらず、この経路の記事は全体で1件しかなかった。
    サイト側に全クラブの一覧ページが無いため、番号を総当たりして見出しから club を同定する。

    ナビゲーションにJ1の20クラブぶんのリンクは並んでいるが、それを読んで書き写すのは危険。
    tag_id を1つ間違えると、別クラブの記事がそのクラブのニュースに混ざり込む
    (soccerdigest_tags.json の note も同じ警告をしている)。実物のページの見出しで確かめる。

見出しの形(2026-08-22採取のサンプルで確認):
    <h2 class="title">
        湘南 新着記事</h2>
    クラブ名はマスタの short(湘南/鹿島/町田…)と同じ表記。

robots.txt(2026-08時点でfetch_news.pyが確認済み): /tag_list/ への Disallow は無い。
こちらから2秒空ける。

CLI:
    python scripts/discover_soccerdigest_tags.py                  tag_id 130〜200 を探す
    python scripts/discover_soccerdigest_tags.py --from 100 --to 260
結果は data/tmp/soccerdigest_tags_found.json に書く(data/tmp はgit管理外)。
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_news import (  # noqa: E402
    MASTER_FILES, SOCCERDIGEST_TAG_URL_TMPL, parse_soccerdigest_entries,
)

OUT_PATH = BASE_DIR / "data" / "tmp" / "soccerdigest_tags_found.json"
HEADERS = {"User-Agent": "jleague-app news fetcher (personal use)"}
TIMEOUT = 20.0
SLEEP = 2.0

_HEADING_RE = re.compile(r'<h2 class="title">\s*(.*?)\s*新着記事\s*</h2>', re.S)


def heading_club_name(html: str) -> str | None:
    m = _HEADING_RE.search(html)
    if not m:
        return None
    return html_lib.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) or None


def load_teams_with_short() -> list[dict]:
    """生のマスタを読む。

    fetch_news.load_all_teams() は team_matching 用に整形された形を返し、short を落としている
    (本文からクラブを拾うときに「鹿島」のような2文字語で誤爆させないための設計)。
    こちらが照合したいのはページの見出し=クラブ名そのものなので、short が要る。
    """
    out: list[dict] = []
    for league, path in MASTER_FILES.items():
        raw = json.loads(path.read_text(encoding="utf-8"))
        teams = raw["teams"] if isinstance(raw, dict) and "teams" in raw else raw
        for t in teams:
            out.append({"league": league, "idTeam": t["idTeam"],
                        "ja": t.get("ja"), "short": t.get("short")})
    return out


def match_team(name: str, all_teams: list[dict]) -> dict | None:
    """見出しの表記(short相当)からクラブを1つに決める。曖昧なものは採らない。

    完全一致だけを見る。部分一致にすると「大阪」が G大阪/C大阪/FC大阪 に当たってしまい、
    間違ったクラブに他所の記事を混ぜ込む事故(soccerdigest_tags.json の note が警告している事故)に
    そのままつながる。1つに決まらないものは人が見る。
    """
    hits = [t for t in all_teams if name == t.get("short") or name == t.get("ja")]
    return hits[0] if len(hits) == 1 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=130)
    ap.add_argument("--to", dest="end", type=int, default=200)
    args = ap.parse_args()

    all_teams = load_teams_with_short()
    found: dict[str, dict] = {}
    for tag_id in range(args.start, args.end + 1):
        url = SOCCERDIGEST_TAG_URL_TMPL.format(tag_id=tag_id)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            html = resp.text
        except Exception as e:  # noqa: BLE001
            print(f"[warn] tag_id={tag_id}: 取得失敗 {e}", file=sys.stderr)
            time.sleep(SLEEP)
            continue

        name = heading_club_name(html)
        if not name:
            print(f"[  --] tag_id={tag_id}: 見出しなし")
            time.sleep(SLEEP)
            continue
        entries = parse_soccerdigest_entries(html)
        team = match_team(name, all_teams)
        mark = "OK" if team else "??"
        label = f"{team['league']} {team['ja']}" if team else "(Jクラブとして同定できず)"
        print(f"[{mark}] tag_id={tag_id}: 「{name}」 記事{len(entries)}件 -> {label}")
        found[str(tag_id)] = {
            "heading": name,
            "entries": len(entries),
            "idTeam": team["idTeam"] if team else None,
            "ja": team["ja"] if team else None,
            "league": team["league"] if team else None,
        }
        time.sleep(SLEEP)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(found, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = sum(1 for v in found.values() if v["idTeam"])
    print(f"\n[info] 見出しの取れたtag_id {len(found)}件、うちJクラブに同定できたもの {ok}件")
    print(f"[info] {OUT_PATH} に書き出した")


if __name__ == "__main__":
    main()
