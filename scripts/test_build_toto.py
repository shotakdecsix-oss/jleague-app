"""build_toto の、外部データに依存しない部分のテスト。

いちばん大事なのは assign_years。指定試合表のPDFには年が書かれておらず
「9/12」としか読めないので、月日だけで試合を照合すると別シーズンの同月日を拾う。
実際、2026年4月29日の指定試合が2027年4月29日の試合(今季の日程)に誤マッチし、
関係の無い回が「予想できた」ことになっていた。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_toto import assign_years, picks_of  # noqa: E402


def _r(kai: int, *dates: str) -> dict:
    return {"kai": kai, "matches": [{"date": d} for d in dates]}


def test_assign_years_walks_back_over_new_year() -> None:
    today = date(2026, 9, 6)
    parsed = [
        _r(1653, "9/12", "9/13"),
        _r(1607, "2/6"),
        _r(1606, "1/31", "2/1"),
        _r(1600, "12/20"),
        _r(1595, "11/30"),
    ]
    years = assign_years(parsed, today)
    assert years[1653] == 2026
    assert years[1607] == 2026
    assert years[1606] == 2026
    # 1/31 より後ろの 12/20 が出てきたら、そこから前年
    assert years[1600] == 2025
    assert years[1595] == 2025


def test_assign_years_anchor_picks_the_nearest_year() -> None:
    """年明け直後に、去年の12月開催の回を見ているケース。"""
    years = assign_years([_r(1600, "12/28")], date(2026, 1, 5))
    assert years[1600] == 2025
    # 年末に、年明けの回を先に見ているケース
    years = assign_years([_r(1601, "1/3")], date(2025, 12, 28))
    assert years[1601] == 2026


def test_assign_years_uses_the_earliest_date_in_a_round() -> None:
    """1回のなかで日付が2日にまたがるので、判定は最も早い日で行う。"""
    years = assign_years([_r(1613, "3/1", "2/28")], date(2026, 3, 5))
    assert years[1613] == 2026


def test_assign_years_empty() -> None:
    assert assign_years([], date(2026, 9, 6)) == {}


def test_picks_of_sorts_by_mark_number() -> None:
    rnd = {"matches": [
        {"no": 3, "marks": {"toto": 3}},
        {"no": 1, "marks": {"toto": 1, "totoGOAL3": [1, 2]}},
        {"no": 2, "marks": {"toto": 2}},
        {"no": 9, "marks": {"minitotoB": 1}},   # totoには入っていない
    ]}
    assert [m["no"] for m in picks_of(rnd, "toto")] == [1, 2, 3]
    assert [m["no"] for m in picks_of(rnd, "minitotoB")] == [9]
    # GOAL3はマークが2つ組。先頭の番号で並ぶ
    assert [m["no"] for m in picks_of(rnd, "totoGOAL3")] == [1]


def main() -> None:
    tests = [
        test_assign_years_walks_back_over_new_year,
        test_assign_years_anchor_picks_the_nearest_year,
        test_assign_years_uses_the_earliest_date_in_a_round,
        test_assign_years_empty,
        test_picks_of_sorts_by_mark_number,
    ]
    for t in tests:
        t()
        print(f"  ok {t.__name__}")
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
