"""
scripts/fetch_batch.py のオフラインテスト(マージ処理と激減ガード)。
python scripts/test_fetch_batch.py で実行する(pytest不使用、標準ライブラリのみ)。

2026-08-21、増分取得が「取得した節だけ」でmatches.jsonを丸ごと上書きし、
380件→40件に激減させる事故が起きた。ここはそのリグレッションテスト。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_batch  # noqa: E402


def _m(id_event: str, round_no: int, kickoff: str = "2026-08-22T14:00:00+09:00", score=None) -> dict:
    return {
        "idEvent": id_event,
        "round": round_no,
        "kickoffJst": kickoff,
        "finished": score is not None,
        "home": {"idTeam": "100", "short": "湘南", "score": score},
        "away": {"idTeam": "200", "short": "千葉", "score": None},
    }


def _season(rounds: int = 38, per_round: int = 10) -> list[dict]:
    """38節x10試合=380件の擬似シーズン。"""
    out = []
    for r in range(1, rounds + 1):
        for i in range(per_round):
            out.append(_m(f"{r:02d}{i:02d}", r, f"2026-08-{(r % 28) + 1:02d}T14:00:00+09:00"))
    return out


# ---------- merge_matches ----------

def test_merge_keeps_untouched_rounds():
    """本命のリグレッション: 2〜5節だけ取得しても全380件が維持されること。"""
    existing = _season()
    assert len(existing) == 380
    fetched = [m for m in existing if 2 <= m["round"] <= 5]
    merged = fetch_batch.merge_matches(existing, fetched, [2, 3, 4, 5])
    assert len(merged) == 380, f"380件でなく{len(merged)}件になった"
    assert {m["round"] for m in merged} == set(range(1, 39))


def test_merge_updates_scores_in_fetched_rounds():
    existing = _season()
    target = next(m for m in existing if m["round"] == 3)
    fetched = [dict(m) for m in existing if m["round"] == 3]
    for m in fetched:
        m["home"] = {"idTeam": "100", "short": "湘南", "score": 2}
        m["finished"] = True
    merged = fetch_batch.merge_matches(existing, fetched, [3])
    updated = next(m for m in merged if m["idEvent"] == target["idEvent"])
    assert updated["finished"] is True
    assert updated["home"]["score"] == 2
    assert len(merged) == 380


def test_merge_deletes_match_removed_within_fetched_round():
    """取得できた節の中で消えた試合は、本物の日程削除として消える。"""
    existing = _season()
    fetched = [m for m in existing if m["round"] == 3][:-1]
    merged = fetch_batch.merge_matches(existing, fetched, [3])
    assert len(merged) == 379
    assert len([m for m in merged if m["round"] == 3]) == 9


def test_merge_does_not_delete_matches_outside_fetched_rounds():
    """取得していない節の試合は、fetchedに無くても消えない。"""
    existing = _season()
    merged = fetch_batch.merge_matches(existing, [], [])
    assert len(merged) == 380, "1節も取得できなかったときに既存を消してはいけない"


def test_merge_without_existing_returns_fetched():
    fetched = [_m("0101", 1)]
    assert fetch_batch.merge_matches(None, fetched, [1]) == fetched
    assert fetch_batch.merge_matches([], fetched, [1]) == fetched


def test_merge_handles_round_change_without_duplicating():
    """節が振り替えられた試合が、idEventキーで1件に収束すること。"""
    existing = [_m("0101", 3, "2026-08-22T14:00:00+09:00")]
    fetched = [_m("0101", 4, "2026-09-05T14:00:00+09:00")]
    merged = fetch_batch.merge_matches(existing, fetched, [4])
    assert len(merged) == 1
    assert merged[0]["round"] == 4


def test_merge_sorts_by_kickoff_with_tbd_last():
    fetched = [
        _m("b", 1, "2026-08-23T14:00:00+09:00"),
        dict(_m("c", 1), kickoffJst=None),
        _m("a", 1, "2026-08-22T14:00:00+09:00"),
    ]
    merged = fetch_batch.merge_matches([_m("z", 9)], fetched, [1])
    ids = [m["idEvent"] for m in merged]
    assert ids[-1] == "c", ids
    assert ids.index("a") < ids.index("b")


# ---------- 激減ガード ----------

def test_shrink_guard_blocks_large_drop():
    existing = _season()
    merged = [m for m in existing if m["round"] <= 4]
    assert fetch_batch.check_not_shrunk("j1", existing, merged, allow_shrink=False) is False


def test_shrink_guard_allows_small_drop():
    existing = _season()
    merged = existing[:-1]
    assert fetch_batch.check_not_shrunk("j1", existing, merged, allow_shrink=False) is True


def test_shrink_guard_can_be_overridden():
    existing = _season()
    merged = existing[:40]
    assert fetch_batch.check_not_shrunk("j1", existing, merged, allow_shrink=True) is True


def test_shrink_guard_skipped_on_first_run():
    merged = _season()
    assert fetch_batch.check_not_shrunk("j1", None, merged, allow_shrink=False) is True


# ---------- 期待試合数 ----------

def test_expected_match_count_matches_real_master():
    for league in ("j1", "j2", "j3"):
        assert fetch_batch.expected_match_count(league) == 380, league


def main() -> None:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"OK   {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"ERROR {name}: {type(e).__name__}: {e}")

    print()
    if failed:
        print(f"{len(failed)}/{len(tests)}件失敗: {failed}")
        sys.exit(1)
    print(f"全{len(tests)}件OK")


if __name__ == "__main__":
    main()
