"""
全バッチをまとめて実行するオーケストレーター。
fetch_batch(全リーグ) -> standings x3 -> simulate x3 -> fetch_news -> ... -> build_dist の順に実行する。

設計方針:
- どこか1ステップが失敗しても、残りのステップは実行を続ける
  (例: ネットワーク不調でfetch_newsだけ落ちても、順位表とシミュレーションは更新したい)
- 最後に成功/失敗の一覧をまとめて表示する
- 将来的にGitHub Actions等から呼ぶ単一エントリポイントとして使う想定

CLI:
    python scripts/update_all.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PY = sys.executable

# (表示名, コマンド引数リスト)
STEPS: list[tuple[str, list[str]]] = [
    ("fetch_batch(全リーグ)", [PY, str(SCRIPTS_DIR / "fetch_batch.py"), "--league", "all"]),
    # calendar.json(全体ビューの日程タブ用、日付軸の軽量集約ファイル)はfetch_batchの直後に作る
    # (standings/simulateの結果には依存しないため、ここより後ろでも問題ないが早めに作っておく)
    ("build_calendar(全体ビュー用日程集約)", [PY, str(SCRIPTS_DIR / "build_calendar.py")]),
    ("standings(J1)", [PY, str(SCRIPTS_DIR / "standings.py"), "--league", "j1"]),
    ("standings(J2)", [PY, str(SCRIPTS_DIR / "standings.py"), "--league", "j2"]),
    ("standings(J3)", [PY, str(SCRIPTS_DIR / "standings.py"), "--league", "j3"]),
    ("simulate(J1)", [PY, str(SCRIPTS_DIR / "simulate.py"), "--league", "j1", "--quiet"]),
    ("simulate(J2)", [PY, str(SCRIPTS_DIR / "simulate.py"), "--league", "j2", "--quiet"]),
    ("simulate(J3)", [PY, str(SCRIPTS_DIR / "simulate.py"), "--league", "j3", "--quiet"]),
    ("fetch_news", [PY, str(SCRIPTS_DIR / "fetch_news.py")]),
    ("fetch_official(公式サイト)", [PY, str(SCRIPTS_DIR / "fetch_official.py")]),
    # stats.pyはclub_extra.json(直前のfetch_official)の公式スタッツを取り込むため、必ずその後に置く
    ("stats(J1)", [PY, str(SCRIPTS_DIR / "stats.py"), "--league", "j1"]),
    ("stats(J2)", [PY, str(SCRIPTS_DIR / "stats.py"), "--league", "j2"]),
    ("stats(J3)", [PY, str(SCRIPTS_DIR / "stats.py"), "--league", "j3"]),
    # dist/のビルドは最後に置く(全データの更新が終わった状態でコピーするため)。
    # build_ics.py(カレンダー生成・SEQUENCE永続化)はbuild_dist.pyの内部から呼ばれる
    # (dist/ics/の60ファイルをソースツリーにコミットしたくないため、distの一部として生成する)。
    ("build_dist(配信用ディレクトリ+ics生成)", [PY, str(SCRIPTS_DIR / "build_dist.py")]),
]


def run_step(label: str, cmd: list[str]) -> tuple[bool, float, str]:
    """1ステップを実行する。失敗しても例外を投げず、成否と所要時間、末尾ログを返す。"""
    start = time.time()
    print(f"\n=== {label} ===", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=str(SCRIPTS_DIR.parent), capture_output=True, text=True)
    except Exception as e:  # noqa: BLE001
        elapsed = time.time() - start
        print(f"[error] {label}: 起動に失敗: {e}", file=sys.stderr)
        return False, elapsed, str(e)

    elapsed = time.time() - start
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)

    ok = proc.returncode == 0
    if not ok:
        print(f"[error] {label}: 終了コード {proc.returncode}", file=sys.stderr)

    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail_msg = tail[-1] if tail else ""
    return ok, elapsed, tail_msg


def main() -> None:
    results: list[tuple[str, bool, float, str]] = []

    for label, cmd in STEPS:
        ok, elapsed, tail_msg = run_step(label, cmd)
        results.append((label, ok, elapsed, tail_msg))

    print("\n" + "=" * 50)
    print("実行結果まとめ")
    print("=" * 50)
    n_ok = sum(1 for _, ok, _, _ in results if ok)
    for label, ok, elapsed, tail_msg in results:
        mark = "OK  " if ok else "FAIL"
        line = f"[{mark}] {label} ({elapsed:.1f}秒)"
        if not ok and tail_msg:
            line += f" - {tail_msg}"
        print(line)
    print(f"\n{n_ok}/{len(results)} 件成功")

    if n_ok < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
