"""
scripts/fetch_batch.py のオフラインテスト(第11弾: 増分取得ロジック)。
ネットワーク不要。python scripts/test_fetch_batch.py で実行する。
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_batch as fetch_batch_module  # noqa: E402
from fetch_batch import determine_incremental_rounds, load_existing_matches  # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=JST)


def M(round_no, kickoff, finished):
    return {"round": round_no, "kickoffJst": kickoff, "finished": finished}


def test_no_existing_data_falls_back_to_full_fetch() -> None:
    """既存データが無ければ(初回実行)、全節取得(None)にフォールバックすること。"""
    assert determine_incremental_rounds(None, NOW) is None
    assert determine_incremental_rounds([], NOW) is None
    print("OK: 既存データが無ければNone(全節取得)にフォールバックする")


def test_unfinished_within_window_is_included() -> None:
    """未消化試合のうち、kickoffJstが+14日以内の節が対象に入ること。"""
    matches = [M(5, "2026-08-25T14:00:00+09:00", False)]  # 4日後
    assert determine_incremental_rounds(matches, NOW) == [5]
    print("OK: 14日以内の未消化試合の節が対象に入る")


def test_unfinished_beyond_window_is_excluded() -> None:
    """kickoffJstが+14日を超える未消化試合の節は対象外であること。"""
    matches = [
        M(5, "2026-08-25T14:00:00+09:00", False),   # 4日後 -> 対象
        M(6, "2026-09-20T14:00:00+09:00", False),   # 30日後 -> 対象外
    ]
    assert determine_incremental_rounds(matches, NOW) == [5]
    print("OK: 14日を超える未消化試合の節は対象外")


def test_most_recently_finished_round_is_included() -> None:
    """直近で消化された試合の節が(スコア訂正回収用に)対象に入ること。"""
    matches = [
        M(3, "2026-08-08T14:00:00+09:00", True),
        M(4, "2026-08-15T14:00:00+09:00", True),  # こちらが直近(より新しい)
    ]
    assert determine_incremental_rounds(matches, NOW) == [4]
    print("OK: 直近で消化された試合の節が対象に入る")


def test_union_of_pending_window_and_latest_finished() -> None:
    """未消化(窓内)の節と直近消化節の和集合になること。"""
    matches = [
        M(3, "2026-08-08T14:00:00+09:00", True),
        M(4, "2026-08-15T14:00:00+09:00", True),   # 直近消化
        M(5, "2026-08-25T14:00:00+09:00", False),  # 窓内の未消化
        M(6, "2026-09-30T14:00:00+09:00", False),  # 窓外
    ]
    assert determine_incremental_rounds(matches, NOW) == [4, 5]
    print("OK: 直近消化節と窓内未消化節の和集合になる(重複無く昇順)")


def test_empty_union_falls_back_to_earliest_pending_round() -> None:
    """
    和集合が空(未消化が全部窓外、消化済み試合が無い=開幕前)なら、
    最小1節(未消化の中でキックオフが最も早い節)を取ること。
    """
    matches = [
        M(2, "2026-10-01T14:00:00+09:00", False),
        M(1, "2026-09-25T14:00:00+09:00", False),  # こちらが最も早い
    ]
    assert determine_incremental_rounds(matches, NOW) == [1]
    print("OK: 和集合が空なら、未消化の中で最も早い節にフォールバックする")


def test_all_finished_and_far_outside_window_uses_latest_finished() -> None:
    """全試合消化済み(シーズン終了)なら、直近消化節(最終節)が対象になること。"""
    matches = [
        M(37, "2027-05-30T14:00:00+09:00", True),
        M(38, "2027-06-06T14:00:00+09:00", True),
    ]
    assert determine_incremental_rounds(matches, NOW) == [38]
    print("OK: 全消化済みなら直近消化節(最終節)が対象になる")


def test_no_kickoff_and_no_finished_does_not_crash() -> None:
    """kickoffJstが無く消化試合も無い場合でも例外にならないこと(未消化の先頭節にフォールバックする)。"""
    matches = [M(1, None, False), M(2, None, False)]
    result = determine_incremental_rounds(matches, NOW)
    assert result == [1], result
    print("OK: kickoffJst無し・消化試合無しでも例外にならない")


def test_no_pending_and_no_finished_falls_back_to_max_round() -> None:
    """
    未消化試合が1件も無く(pendingが空)、消化済み試合も無い(finishedが空)という
    保険的な最終フォールバックのケースでも例外にならず、存在する節の最大値を返すこと。
    実データでは起こらない想定(finished=trueならkickoffJstは必ず入る)だが、防御的にテストしておく。
    """
    matches = [{"round": 1, "kickoffJst": None, "finished": True}, {"round": 3, "kickoffJst": None, "finished": True}]
    result = determine_incremental_rounds(matches, NOW)
    assert result == [3], result
    print("OK: pending/finishedとも空という保険的ケースでも最大の節にフォールバックする")


def test_load_existing_matches_missing_file_returns_none() -> None:
    with tempfile.TemporaryDirectory() as td:
        orig = fetch_batch_module.PROCESSED_DIR
        fetch_batch_module.PROCESSED_DIR = Path(td)
        try:
            assert load_existing_matches("j2") is None
        finally:
            fetch_batch_module.PROCESSED_DIR = orig
    print("OK: {league}_matches.jsonが無ければload_existing_matchesはNoneを返す")


def test_load_existing_matches_reads_matches_array() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "j2_matches.json").write_text(
            json.dumps({"meta": {}, "matches": [{"round": 1}]}), encoding="utf-8"
        )
        orig = fetch_batch_module.PROCESSED_DIR
        fetch_batch_module.PROCESSED_DIR = tmp
        try:
            result = load_existing_matches("j2")
            assert result == [{"round": 1}], result
        finally:
            fetch_batch_module.PROCESSED_DIR = orig
    print("OK: load_existing_matchesは既存ファイルのmatches配列を読む")


def main() -> None:
    tests = [
        test_no_existing_data_falls_back_to_full_fetch,
        test_unfinished_within_window_is_included,
        test_unfinished_beyond_window_is_excluded,
        test_most_recently_finished_round_is_included,
        test_union_of_pending_window_and_latest_finished,
        test_empty_union_falls_back_to_earliest_pending_round,
        test_all_finished_and_far_outside_window_uses_latest_finished,
        test_no_kickoff_and_no_finished_does_not_crash,
        test_no_pending_and_no_finished_falls_back_to_max_round,
        test_load_existing_matches_missing_file_returns_none,
        test_load_existing_matches_reads_matches_array,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
