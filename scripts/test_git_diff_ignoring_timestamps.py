"""
scripts/git_diff_ignoring_timestamps.py のオフラインテスト(第11弾)。
ネットワーク不要。git自体は使う(一時リポジトリを作って検証する)。
python scripts/test_git_diff_ignoring_timestamps.py で実行する。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_diff_ignoring_timestamps import (  # noqa: E402
    strip_volatile,
    has_meaningful_change,
    changed_files,
)


def test_strip_volatile_removes_known_keys() -> None:
    obj = {
        "generatedAtJst": "2026-08-21T12:00:00+09:00",
        "meta": {"updatedAtJst": "2026-08-21T12:00:00+09:00", "league": "j1"},
        "runs": [{"at": "2026-08-21T12:00:00+09:00", "ok": True}],
        "matches": [{"round": 1, "fetchedAt": "x", "score": [1, 0]}],
    }
    stripped = strip_volatile(obj)
    assert stripped == {
        "meta": {"league": "j1"},
        "matches": [{"round": 1, "score": [1, 0]}],
    }, stripped
    print("OK: strip_volatileは既知のタイムスタンプ/ログ系キーを再帰的に取り除く")


def test_strip_volatile_keeps_unrelated_keys() -> None:
    obj = {"standings": [{"team": "A", "points": 10}]}
    assert strip_volatile(obj) == obj
    print("OK: strip_volatileは無関係なキーには影響しない")


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _init_repo(tmp: Path) -> None:
    _git("init", "-q")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "test")


def test_timestamp_only_change_is_not_meaningful() -> None:
    with tempfile.TemporaryDirectory() as td:
        cwd = os.getcwd()
        os.chdir(td)
        try:
            _init_repo(Path(td))
            path = Path("data.json")
            path.write_text(
                json.dumps({"generatedAtJst": "T1", "matches": [{"round": 1, "score": None}]}),
                encoding="utf-8",
            )
            _git("add", "-A")
            _git("commit", "-q", "-m", "init")

            # タイムスタンプ欄だけを書き換える(実質的な中身=matchesは同じ)
            path.write_text(
                json.dumps({"generatedAtJst": "T2", "matches": [{"round": 1, "score": None}]}),
                encoding="utf-8",
            )

            assert changed_files(["data.json"]) == ["data.json"]
            assert has_meaningful_change("data.json") is False
            print("OK: タイムスタンプ欄のみの変更はhas_meaningful_change=Falseになる")
        finally:
            os.chdir(cwd)


def test_real_field_change_is_meaningful() -> None:
    with tempfile.TemporaryDirectory() as td:
        cwd = os.getcwd()
        os.chdir(td)
        try:
            _init_repo(Path(td))
            path = Path("data.json")
            path.write_text(
                json.dumps({"generatedAtJst": "T1", "matches": [{"round": 1, "score": None}]}),
                encoding="utf-8",
            )
            _git("add", "-A")
            _git("commit", "-q", "-m", "init")

            # タイムスタンプに加えて実質的な中身(スコア)も変わる
            path.write_text(
                json.dumps({"generatedAtJst": "T2", "matches": [{"round": 1, "score": [2, 1]}]}),
                encoding="utf-8",
            )

            assert has_meaningful_change("data.json") is True
            print("OK: 実質的なフィールドの変更はhas_meaningful_change=Trueになる")
        finally:
            os.chdir(cwd)


def test_new_untracked_file_is_meaningful() -> None:
    with tempfile.TemporaryDirectory() as td:
        cwd = os.getcwd()
        os.chdir(td)
        try:
            _init_repo(Path(td))
            # HEADが存在する状態(実際のCIでは常にそう)にしてから、未トラックの新規ファイルを追加
            Path("README.md").write_text("init", encoding="utf-8")
            _git("add", "-A")
            _git("commit", "-q", "-m", "init")
            Path("new.json").write_text(json.dumps({"a": 1}), encoding="utf-8")

            assert changed_files(["new.json"]) == ["new.json"]
            assert has_meaningful_change("new.json") is True
            print("OK: 新規(未トラック)ファイルは無条件でhas_meaningful_change=Trueになる")
        finally:
            os.chdir(cwd)


def test_no_changes_returns_empty_list() -> None:
    with tempfile.TemporaryDirectory() as td:
        cwd = os.getcwd()
        os.chdir(td)
        try:
            _init_repo(Path(td))
            Path("data.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
            _git("add", "-A")
            _git("commit", "-q", "-m", "init")

            assert changed_files(["data.json"]) == []
            print("OK: 変更が無ければchanged_filesは空リストを返す")
        finally:
            os.chdir(cwd)


def main() -> None:
    tests = [
        test_strip_volatile_removes_known_keys,
        test_strip_volatile_keeps_unrelated_keys,
        test_timestamp_only_change_is_not_meaningful,
        test_real_field_change_is_meaningful,
        test_new_untracked_file_is_meaningful,
        test_no_changes_returns_empty_list,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
