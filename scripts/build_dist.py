"""
配信用ディレクトリ dist/ を作る。

フィルタは一切かけない(個人利用のダッシュボードで、URLを共有せず検索にも載せない前提のため)。
必要なファイルをそのままコピーするだけ。

コピーするもの:
    index.html
    manifest.webmanifest
    robots.txt
    icons/*                    (manifest.webmanifestが参照するアイコン)
    data/masters/*.json
    data/processed/*.json      (club_extra.json / live.jsonも含む、全部)

生成するもの(コピーではなく、ビルドの都度ここで新しく書く):
    deploy-time.txt            (デプロイ時刻の鮮度表示用。index.htmlのフッターに小さく出す)

コピーしないもの(配信に不要なだけで、規約上の理由ではない):
    scripts/ docs/ data/config/ data/fixtures/ data/tmp/

CLI:
    python scripts/build_dist.py
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = BASE_DIR / "dist"

TOP_LEVEL_FILES = ["index.html", "manifest.webmanifest", "robots.txt"]
COPY_DIRS = ["icons", "data/masters", "data/processed"]


def clean_dist() -> None:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)


def copy_top_level_files() -> None:
    for name in TOP_LEVEL_FILES:
        src = BASE_DIR / name
        if not src.exists():
            print(f"[warn] {src} が無い。スキップ", file=sys.stderr)
            continue
        shutil.copy2(src, DIST_DIR / name)
        print(f"[info] コピー: {name}")


def copy_dirs() -> None:
    for rel in COPY_DIRS:
        src = BASE_DIR / rel
        if not src.exists():
            print(f"[warn] {src} が無い。スキップ", file=sys.stderr)
            continue
        dst = DIST_DIR / rel
        dst.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
                count += 1
        print(f"[info] コピー: {rel}/ ({count}ファイル)")


def write_deploy_time() -> None:
    """
    dist/deploy-time.txt に、いまこの瞬間のJST時刻を書く(デバッグ用の小さな鮮度表示)。
    ソースには存在しない、distをビルドする直前にフレッシュな値をここで生成するファイル。
    index.htmlがこれを取得してフッターに小さく出す(表示できなくてもアプリは落ちない設計)。
    """
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    (DIST_DIR / "deploy-time.txt").write_text(now, encoding="utf-8")
    print(f"[info] デプロイ時刻を記録: {now}")


def report_size() -> None:
    files = [f for f in DIST_DIR.rglob("*") if f.is_file()]
    total_bytes = sum(f.stat().st_size for f in files)
    total_mb = total_bytes / (1024 * 1024)
    print(f"\n[info] dist/ 合計サイズ: {total_mb:.2f} MB ({len(files)}ファイル)")

    # 大きいファイルは通信量の目安として個別に出しておく(削るかどうかの判断材料)
    big_files = sorted(files, key=lambda f: f.stat().st_size, reverse=True)[:5]
    print("[info] サイズの大きいファイル上位5件:")
    for f in big_files:
        size_kb = f.stat().st_size / 1024
        print(f"    {f.relative_to(DIST_DIR)}: {size_kb:,.0f} KB")


def main() -> None:
    clean_dist()
    copy_top_level_files()
    copy_dirs()
    write_deploy_time()
    report_size()
    print("\n[info] dist/ のビルド完了")


if __name__ == "__main__":
    main()
