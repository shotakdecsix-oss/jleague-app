"""
CI(第11弾)専用: data/processed・data/history の変更が「タイムスタンプ欄が書き直されただけ」か、
実質的な変更(試合結果・順位・確率等)を伴うものかを判定する。

なぜ必要か:
  fetch_batch.py / standings.py / simulate.py / build_calendar.py は、入力(試合結果)が実質的に
  変わっていなくても、実行するたびに generatedAtJst / updatedAtJst のようなタイムスタンプ欄と、
  meta.json の runs ログを書き直す。素朴に `git diff --cached --quiet` するだけだと、
  試合が無い深夜帯でも「差分あり」と誤判定され、第11弾6章の「変更が無ければコミットしない」が
  機能しなくなる(無駄なコミット・Renderへの無駄なデプロイが4時間おきに発生し続ける)。

  そこで、既知のタイムスタンプ/ログ系のキーを取り除いたJSON同士を比較し、実質的な差分だけを見る。

対象は「gitのHEAD時点の内容」対「現在の作業ツリーの内容」。HEADに存在しないファイル(新規追加)や、
JSONとしてパースできないファイルは安全側に倒して無条件で「変更あり」とみなす。

CLI:
    python scripts/git_diff_ignoring_timestamps.py data/processed data/history
    終了コード: 実質的な変更が1件でもあれば0、無ければ1
      (シェルの `if python scripts/git_diff_ignoring_timestamps.py ...; then ... fi` で使う想定)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 再帰的に取り除くキー名(値ではなくキー名だけで判定。どの階層のオブジェクト直下にあっても対象)。
# "runs" は meta.json の実行ログ配列そのもの(タイムスタンプ以外の実質的な情報を持たないため丸ごと除外)。
# fetchState: 天皇杯の試合詳細(fetch_emperors_cup_events.py)が持つ取得記録。
# 最終取得時刻と試行回数だけが入っており、中身が変わらなくても動くのでまとめて無視する。
VOLATILE_KEYS = {"generatedAtJst", "updatedAtJst", "at", "fetchedAt", "runs", "fetchState"}


def strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def git_show(path: str) -> str | None:
    """HEAD時点の内容を返す。HEADに存在しない(新規ファイル)場合はNone。"""
    result = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout


def changed_files(paths: list[str]) -> list[str]:
    """HEADとの差分があるファイル(トラック済みの変更+未トラックの新規ファイル)を列挙する。"""
    tracked = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", *paths],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    return sorted(set(tracked) | set(untracked))


def has_meaningful_change(path: str) -> bool:
    if not path.endswith(".json"):
        return True  # JSON以外(想定外のファイル種別)は素朴に「変更あり」とみなす(安全側)

    new_path = Path(path)
    if not new_path.exists():
        return True  # 削除された

    old_text = git_show(path)
    if old_text is None:
        return True  # 新規ファイル

    try:
        old_json = json.loads(old_text)
        new_json = json.loads(new_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True  # パースできなければ安全側に倒して「変更あり」

    return strip_volatile(old_json) != strip_volatile(new_json)


def main() -> None:
    paths = sys.argv[1:] or ["data/processed", "data/history"]
    files = changed_files(paths)
    if not files:
        print("[info] 変更されたファイルなし")
        sys.exit(1)

    meaningful = [f for f in files if has_meaningful_change(f)]
    if meaningful:
        print(f"[info] 実質的な変更あり({len(meaningful)}件): {meaningful}")
        sys.exit(0)

    print(f"[info] タイムスタンプ欄のみの差分({len(files)}件)。実質的な変更なし: {files}")
    sys.exit(1)


if __name__ == "__main__":
    main()
