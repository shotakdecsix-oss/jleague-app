"""
data/processed/{league}_match_events.json に「タイムスタンプ欄を除いた実質的な変更」があるかを
判定する(第13弾)。update.yml側のgit_diff_ignoring_timestamps.pyとは対象ファイルの構造が違う
(meta.generatedAtJstだけでなく、events内の各試合ごとにfetchedAtJstが付く)ため専用スクリプトにした。

5分おきに走る第13弾のワークフローで、得点/カード/交代が実際には増えていない回まで
コミット+push+Renderデプロイを発生させないためのガード
(第11弾以来の「タイムスタンプだけの差分でコミットしない」方針を踏襲)。

終了コード: 実質的な変更が1件でもあれば0、無ければ1
(シェルの `if python scripts/git_diff_match_events.py ...; then` でそのまま分岐できる)。

CLI:
    python scripts/git_diff_match_events.py data/processed/j1_match_events.json data/processed/j2_match_events.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_VOLATILE_KEYS = {"generatedAtJst", "fetchedAtJst"}


def _strip_volatile(obj):
    """generatedAtJst(meta) / fetchedAtJst(各イベント)を再帰的に取り除いたコピーを返す。"""
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in _VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _committed_version(path: str) -> dict | None:
    result = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True)
    if result.returncode != 0:
        return None  # HEADにまだ存在しない(初回コミット前)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def has_meaningful_change(path: str) -> bool:
    working_path = Path(path)
    if not working_path.exists():
        return False
    try:
        working = json.loads(working_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False

    committed = _committed_version(path)
    if committed is None:
        return True  # 初回コミット

    return _strip_volatile(working) != _strip_volatile(committed)


def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        print("[error] 対象ファイルを引数で指定してください", file=sys.stderr)
        sys.exit(2)

    changed = [p for p in paths if has_meaningful_change(p)]
    if changed:
        print(f"[info] 実質的な変更あり: {changed}")
        sys.exit(0)
    print("[info] 実質的な変更なし(タイムスタンプ欄のみの差分、または差分なし)")
    sys.exit(1)


if __name__ == "__main__":
    main()
