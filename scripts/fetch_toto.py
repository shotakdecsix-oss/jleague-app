"""
スポーツくじの「くじ指定試合表」PDFを取得して data/history/toto/<回号>.json に保存する。

  python scripts/fetch_toto.py --kai 1653          # 1回だけ
  python scripts/fetch_toto.py --range 1600-1653   # まとめて
  python scripts/fetch_toto.py --latest 4          # 最新回の周辺を探して取る

出典と規約: scripts/toto_pdf.py の docstring を参照(robots.txt確認済み)。

PDF本体は data/tmp/toto/ に置く(gitignore済み)。リポジトリに入れるのは
パース結果のJSONだけ。PDFは1件70〜95KBあり、1シーズン分で数MBになるため。

未公示の回はHTMLの404ページが200で返ってくることがある(第1654回で実測:
16,051バイトのHTML)。先頭が %PDF かどうかで弾いている。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from toto_pdf import parse_shiteijiai  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "data" / "history" / "toto"
CACHE_DIR = BASE_DIR / "data" / "tmp" / "toto"
PDF_URL = "https://www.jpnsport.go.jp/sinko/Portals/0/sinko/toto/pdf/shiteijiai_{kai}.pdf"
HEADERS = {"User-Agent": "jleague-app toto fetcher (personal use)"}
TIMEOUT = 30
SLEEP_BETWEEN = 1.5  # 公示PDFは静的ファイルだが、連続取得の礼儀として1.5秒空ける
JST = timezone(timedelta(hours=9))


def download(kai: int) -> tuple[Path | None, str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"shiteijiai_{kai}.pdf"
    if path.exists() and path.read_bytes()[:4] == b"%PDF":
        return path, "cached"
    try:
        resp = requests.get(PDF_URL.format(kai=kai), headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return None, f"error {exc.__class__.__name__}"
    if resp.status_code != 200:
        return None, f"http {resp.status_code}"
    if not resp.content.startswith(b"%PDF"):
        return None, "not a pdf (未公示か)"
    path.write_bytes(resp.content)
    return path, "downloaded"


def save(kai: int, path: Path) -> dict:
    parsed = parse_shiteijiai(path)
    if parsed["kai"] != kai:
        raise ValueError(f"回号が一致しない: URL={kai} PDF={parsed['kai']}")
    parsed["source"] = PDF_URL.format(kai=kai)
    parsed["fetchedAtJst"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{kai}.json"
    out.write_text(json.dumps(parsed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return parsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kai", type=int)
    ap.add_argument("--range", dest="rng")
    ap.add_argument("--latest", type=int, metavar="N",
                    help="保存済みの最大回号の次からN回ぶん試す(定期実行向け)")
    ap.add_argument("--force", action="store_true", help="既にJSONがあっても取り直す")
    args = ap.parse_args()

    if args.kai:
        kais = [args.kai]
    elif args.rng:
        lo, _, hi = args.rng.partition("-")
        kais = list(range(int(lo), int(hi) + 1))
    elif args.latest:
        known = [int(f.stem) for f in OUT_DIR.glob("*.json") if f.stem.isdigit()] if OUT_DIR.exists() else []
        if not known:
            print("保存済みの回がありません。最初は --range で範囲を指定してください。")
            return 1
        # 未公示の回はHTMLが返るだけで即スキップされるので、毎回まわしても負荷は小さい
        kais = list(range(max(known) + 1, max(known) + 1 + args.latest))
    else:
        ap.error("--kai / --range / --latest のどれかを指定してください")

    ok = skipped = failed = 0
    for kai in kais:
        if not args.force and (OUT_DIR / f"{kai}.json").exists():
            skipped += 1
            continue
        path, note = download(kai)
        if path is None:
            print(f"  {kai}: {note}")
            failed += 1
            if note != "not a pdf (未公示か)":
                time.sleep(SLEEP_BETWEEN)
            continue
        try:
            parsed = save(kai, path)
        except Exception as exc:
            print(f"  {kai}: パース失敗 {exc}")
            failed += 1
            continue
        counts = []
        for key in ("toto", "minitotoA", "minitotoB", "totoGOAL3"):
            n = sum(1 for m in parsed["matches"] if key in m["marks"])
            if n:
                counts.append(f"{key}={n}")
        dates = sorted({m["date"] for m in parsed["matches"]})
        print(f"  {kai}: {note} 全{len(parsed['matches'])}試合 {'/'.join(dates)} {' '.join(counts)}")
        ok += 1
        if note == "downloaded":
            time.sleep(SLEEP_BETWEEN)

    print(f"完了: 保存{ok} スキップ{skipped} 失敗{failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
