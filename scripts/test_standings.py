"""
standings.py の純粋関数(build_records / rank_teams / compute_played_diff)の単体テスト。
ネットワーク不要、固定データのみ。

実行方法:
    python scripts/test_standings.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from standings import build_records, compute_played_diff, rank_teams  # noqa: E402


def M(home: str, hs: int, away: str, as_: int, kickoff: str = "2026-01-01T00:00:00+09:00") -> dict:
    """テスト用の最小の試合dictを作る。"""
    return {
        "finished": True,
        "kickoffJst": kickoff,
        "home": {"idTeam": home, "score": hs},
        "away": {"idTeam": away, "score": as_},
    }


def _rank_index_of(groups: list[list[str]], team: str) -> int:
    for i, g in enumerate(groups):
        if team in g:
            return i
    raise AssertionError(f"{team} が groups に見つからない: {groups}")


def test_points_gd_gf_alone_decide_order() -> None:
    """勝点・得失点差・総得点だけで一意に決まるケース。"""
    matches = [
        M("A", 3, "X", 0),
        M("A", 2, "Y", 0),
        M("B", 1, "X", 0),
        M("B", 1, "Y", 1),
        M("C", 0, "X", 0),
        M("C", 0, "Y", 0),
    ]
    records = build_records(matches)
    groups = rank_teams(records, matches)

    ia, ib, ic = _rank_index_of(groups, "A"), _rank_index_of(groups, "B"), _rank_index_of(groups, "C")
    assert ia < ib < ic, f"A > B > C の順のはず: groups={groups}"
    assert groups[ia] == ["A"] and groups[ib] == ["B"] and groups[ic] == ["C"], (
        f"A/B/Cはいずれも単独順位のはず: groups={groups}"
    )
    print("OK: 勝点・得失点差・総得点だけで順序が決まるケース")


def test_two_team_tie_resolved_by_head_to_head() -> None:
    """2クラブが完全に並び、直接対決2試合で決着するケース。"""
    matches = [
        M("A", 2, "B", 1),   # 直接対決 第1戦: Aが勝つ
        M("B", 0, "A", 0),   # 直接対決 第2戦: 引き分け
        M("A", 0, "Z1", 1),  # フィラー: Aは負けて全体成績を揃える
        M("B", 1, "Z2", 0),  # フィラー: Bは勝って全体成績を揃える
    ]
    records = build_records(matches)
    # 全体成績が完全に一致していることを前提の確認(テストの設計意図)
    assert (records["A"].points, records["A"].gd, records["A"].gf) == (
        records["B"].points, records["B"].gd, records["B"].gf
    ), "テストの前提: 全体成績はA/Bで完全に並んでいるはず"

    groups = rank_teams(records, matches)
    ia, ib = _rank_index_of(groups, "A"), _rank_index_of(groups, "B")
    assert ia < ib, f"直接対決でA(4pt)がB(1pt)より上位になるはず: groups={groups}"
    assert groups[ia] == ["A"] and groups[ib] == ["B"], f"AとBは同順位で並ばないはず: groups={groups}"
    print("OK: 2クラブ完全タイが直接対決2試合で決着するケース")


def test_three_team_tie_resolved_by_mini_league() -> None:
    """3クラブが並び、クラスタ内の全ペア(A-B, A-C, B-C)が消化済みでミニリーグが決着するケース。"""
    matches = [
        # 当該チーム間(A>B>C の推移的な結果にする。円環だと決着しないので注意)
        M("A", 1, "B", 0),
        M("A", 1, "C", 0),
        M("B", 1, "C", 0),
        # フィラー: 全体成績をA=B=C(勝点6・得失点+1・総得点3)に揃える
        M("A", 1, "ZA", 2),   # A: 負け(勝点0,得0失2)
        M("B", 2, "ZB", 1),   # B: 勝ち(勝点3,得2失1)
        M("C", 2, "ZC1", 0),  # C: 勝ち(勝点3,得2失0)
        M("C", 1, "ZC2", 0),  # C: 勝ち(勝点3,得1失0)
    ]
    records = build_records(matches)
    key_a = (records["A"].points, records["A"].gd, records["A"].gf)
    key_b = (records["B"].points, records["B"].gd, records["B"].gf)
    key_c = (records["C"].points, records["C"].gd, records["C"].gf)
    assert key_a == key_b == key_c, f"テストの前提: A/B/Cの全体成績は完全に並んでいるはず: {key_a},{key_b},{key_c}"

    groups = rank_teams(records, matches)
    ia, ib, ic = _rank_index_of(groups, "A"), _rank_index_of(groups, "B"), _rank_index_of(groups, "C")
    assert ia < ib < ic, f"ミニリーグでA>B>Cと決着するはず: groups={groups}"
    assert groups[ia] == ["A"] and groups[ib] == ["B"] and groups[ic] == ["C"], (
        f"ミニリーグで完全に分離されるはず(同順位が残ってはいけない): groups={groups}"
    )
    print("OK: 3クラブタイが(全ペア消化済みの)ミニリーグで決着するケース")


def test_three_team_tie_partial_head_to_head_stays_tied() -> None:
    """
    3クラブが並ぶが、クラスタ内の一部ペアだけ直接対決があり(A-Bのみ)、Cは0試合のケース。
    レビュー指摘の再現ケース: 「対戦していないチームに(0,0,0)を割り当てて部分順序をつける」実装だと
    未対戦のCが、A-B戦で負けたBより上位に来る捏造が起きていた。
    全ペアが消化されていなければミニリーグは成立しない -> 3クラブとも同順位が正しい。
    """
    matches = [
        M("A", 1, "B", 0),   # A-Bのみ直接対決あり(Cとは無関係)
        # フィラー: 全体成績をA=B=C(勝点3・得失点0・総得点1)に揃える
        M("A", 0, "ZA", 1),    # A: 負け(勝点0,得0失1) -> 累計 win1,loss1,pts3,gf1,ga1,gd0
        M("B", 1, "ZB", 0),    # B: 勝ち(勝点3,得1失0) -> 累計 loss1,win1,pts3,gf1,ga1,gd0
        M("C", 1, "ZC1", 0),   # C: 勝ち(勝点3,得1失0)
        M("C", 0, "ZC2", 1),   # C: 負け(勝点0,得0失1) -> 累計 win1,loss1,pts3,gf1,ga1,gd0(A-B戦とは無関係)
    ]
    records = build_records(matches)
    key_a = (records["A"].points, records["A"].gd, records["A"].gf)
    key_b = (records["B"].points, records["B"].gd, records["B"].gf)
    key_c = (records["C"].points, records["C"].gd, records["C"].gf)
    assert key_a == key_b == key_c, f"テストの前提: A/B/Cの全体成績は完全に並んでいるはず: {key_a},{key_b},{key_c}"

    groups = rank_teams(records, matches)
    ia, ib, ic = _rank_index_of(groups, "A"), _rank_index_of(groups, "B"), _rank_index_of(groups, "C")
    assert ia == ib == ic, (
        f"Cが1試合も対戦していない以上ミニリーグは成立せず、3クラブとも同順位のはず: groups={groups}"
    )
    tied_group = set(groups[ia])
    assert tied_group == {"A", "B", "C"}, f"tiedWith相当のグループはA/B/C全員のはず: {tied_group}"
    print("OK: 3クラブタイ・一部ペアのみ直接対決(Cは0試合)は同順位のまま(ミニリーグ未完扱い)")


def test_three_team_tie_no_head_to_head_stays_tied() -> None:
    """3クラブが並ぶが直接対決が0試合 -> 3クラブとも同順位(tiedWithに互いが入る)。"""
    matches = [
        M("A", 2, "X", 0),
        M("B", 2, "Y", 0),
        M("C", 2, "Z", 0),
        # A・B・C同士の対戦は無い
    ]
    records = build_records(matches)
    groups = rank_teams(records, matches)

    ia, ib, ic = _rank_index_of(groups, "A"), _rank_index_of(groups, "B"), _rank_index_of(groups, "C")
    assert ia == ib == ic, f"直接対決0試合なので3クラブとも同順位のはず: groups={groups}"
    tied_group = set(groups[ia])
    assert tied_group == {"A", "B", "C"}, f"tiedWith相当のグループはA/B/C全員のはず: {tied_group}"
    print("OK: 直接対決0試合の3クラブタイは同順位のまま(tiedWithに互いが入る)")


def test_played_diff() -> None:
    """playedDiffが正しく出るケース(1クラブだけ1試合少ない)。"""
    matches = [
        M("A", 1, "D1", 0),
        M("A", 0, "D2", 1),
        M("B", 2, "D1", 0),
        M("B", 1, "D2", 1),
        M("B", 0, "D3", 2),
        M("C", 1, "D1", 0),
        M("C", 2, "D2", 2),
        M("C", 0, "D3", 0),
    ]
    records = build_records(matches)
    assert records["A"].played == 2
    assert records["B"].played == 3
    assert records["C"].played == 3

    diff = compute_played_diff(records)
    assert diff["A"] == 1, f"Aは最多消化(3)より1試合少ないはず: {diff}"
    assert diff["B"] == 0
    assert diff["C"] == 0
    print("OK: playedDiffが正しく出るケース(Aだけ1試合少ない)")


def main() -> None:
    tests = [
        test_points_gd_gf_alone_decide_order,
        test_two_team_tie_resolved_by_head_to_head,
        test_three_team_tie_resolved_by_mini_league,
        test_three_team_tie_partial_head_to_head_stays_tied,
        test_three_team_tie_no_head_to_head_stays_tied,
        test_played_diff,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
