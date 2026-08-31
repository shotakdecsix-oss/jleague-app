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
    data/history/*.json        (確率推移履歴。ics_state.jsonも含むが配信には使わない)

生成するもの(コピーではなく、ビルドの都度ここで新しく書く):
    deploy-time.txt            (デプロイ時刻の鮮度表示用。index.htmlのヘッダーに小さく出す)
    deploy-version.txt         (直近のgitコミットの短縮ハッシュ。同上。git管理下でなければ書かない)
    ics/{idTeam}.ics           (60クラブぶんのカレンダー。build_ics.pyがdist直下に直接生成する。
                                 ソースツリーに60ファイルをコミットしたくないのでCOPY_DIRSではなくここで呼ぶ)

コピーしないもの(配信に不要なだけで、規約上の理由ではない):
    scripts/ docs/ data/config/ data/fixtures/ data/tmp/

CLI:
    python scripts/build_dist.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402
import build_ics  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = BASE_DIR / "dist"

TOP_LEVEL_FILES = ["index.html", "manifest.webmanifest", "robots.txt"]
COPY_DIRS = ["icons", "data/masters", "data/processed", "data/history"]

# フロントが直接使わない内部状態ファイルは、data/history/をコピーする際にだけ除外する
# (ics_state.jsonはSEQUENCE永続化用の内部状態で、リポジトリにはコミットするがdist配信には不要なため)。
COPY_DIRS_EXCLUDE: dict[str, set[str]] = {"data/history": {"ics_state.json"}}

# 第30弾: index.html に埋めてあるビルド版数のプレースホルダ。
# 分割して書いてあるのは、この行自身が置換対象にならないようにするため。
PLACEHOLDER = "__DEPLOY" + "_VERSION__"


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
        exclude = COPY_DIRS_EXCLUDE.get(rel, set())
        count = 0
        for f in src.iterdir():
            if f.is_file() and f.name not in exclude:
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


def write_deploy_version() -> None:
    """
    dist/deploy-version.txt に、直近のgitコミットの短縮ハッシュを書く(デバッグ用のバージョン表示)。
    このビルド自体はまだコミットされていない(distの中身自体がこれからコミットされる側)ので、
    厳密には「1つ前のコミット」を指す点に注意。それでも「ブラウザが古いキャッシュを見ていないか」を
    確認する用途には十分。gitが無い/コミットが1つも無い環境では黙ってスキップする(アプリは落とさない)。
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=5,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] gitコミットハッシュの取得に失敗、デプロイバージョン表示はスキップ: {e}", file=sys.stderr)
        return
    if out.returncode != 0:
        print(f"[warn] git rev-parseが失敗(gitリポジトリでない?)、デプロイバージョン表示はスキップ", file=sys.stderr)
        return
    version = out.stdout.strip()
    (DIST_DIR / "deploy-version.txt").write_text(version, encoding="utf-8")
    print(f"[info] デプロイバージョンを記録: {version}")

    # 第30弾: 同じ値を dist/index.html にも焼き込む。
    # 書き換えるのはコピー後の dist/ の方だけで、ソースの index.html は触らない
    # (ソースを書き換えるとビルドのたびにgitの差分が出てしまう)。
    # アプリ側はこの定数と deploy-version.txt を比べ、食い違えば
    # 「ブラウザが古いHTMLを掴んでいる」と判断して再読み込みを促す。
    dist_index = DIST_DIR / "index.html"
    if not dist_index.exists():
        return
    html = dist_index.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        print(f"[warn] dist/index.html に {PLACEHOLDER} が無い。更新検知が働かなくなるので確認すること",
              file=sys.stderr)
        return
    dist_index.write_text(html.replace(PLACEHOLDER, version), encoding="utf-8")
    print(f"[info] dist/index.html にバージョンを焼き込み: {version}")


def check_calendar_generated() -> None:
    """
    calendar.json(全体ビューの日程タブ用)はdata/processed/配下なので既存のCOPY_DIRSで
    そのままコピーされる(追加設定は不要)。ただし生成し忘れたまま配信してしまう事故を防ぐため、
    このファイルが無ければwarnだけ出す(ビルド自体は止めない)。
    """
    path = BASE_DIR / "data" / "processed" / "calendar.json"
    if not path.exists():
        print(
            "[warn] data/processed/calendar.json が無い。先に python scripts/build_calendar.py を実行すること"
            "(全体ビューの日程タブが空になる)",
            file=sys.stderr,
        )


def build_ics_calendars() -> None:
    """
    dist/ics/{idTeam}.ics を60クラブぶん生成する。COPY_DIRSでのコピーではなく、
    build_ics.pyにdist直下へ直接書かせる(ソースツリーに60ファイルをコミットしたくないため)。
    SEQUENCE永続化用のdata/history/ics_state.jsonは、副作用としてこの呼び出し中にリポジトリ側で更新される。
    """
    try:
        club_count, cancelled_count = build_ics.build_all(dist_dir=DIST_DIR)
    except build_ics.MassCancellationError as e:
        # 大量キャンセルガード発動。ics_state.jsonは書き換わっていない。
        # ここで止めないと壊れたicsをdistに出してしまう。
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[info] カレンダー(.ics)を生成: {club_count}クラブ (今回新たにCANCELLEDにしたイベント: {cancelled_count})")


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
    check_calendar_generated()
    clean_dist()
    copy_top_level_files()
    copy_dirs()
    write_deploy_time()
    write_deploy_version()
    build_ics_calendars()
    report_size()
    print("\n[info] dist/ のビルド完了")


if __name__ == "__main__":
    main()
