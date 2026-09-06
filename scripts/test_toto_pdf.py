"""くじ指定試合表PDFのパーサのテスト。

data/samples/toto/ に実物のPDFを3件置いてある。3件とも別のケースを踏んでいる:
  1653 … BIG系+toto+mini A/B+GOAL3 が全部そろった標準形
  1650 … 第6試合だけ mini toto A/B が空欄。空白区切りで読むと列がズレる回
  1651 … BIG系がまるごと無く、totoも無い(mini toto A と GOAL3 だけ)。天皇杯が対象の回

とくに1650の第6試合は、pdftotext -layout の空白区切りだと mini toto B の値を
mini toto A の位置に読んでしまう。x座標で列に割り当てているのはこのため。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from toto_pdf import circled_to_int, parse_shiteijiai, picks_for  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples" / "toto"


def _load(kai: int) -> dict:
    return parse_shiteijiai(SAMPLES / f"shiteijiai_{kai}.pdf")


def test_circled_to_int() -> None:
    assert circled_to_int("①") == 1
    assert circled_to_int("⑬") == 13
    assert circled_to_int("x") is None


def test_standard_round() -> None:
    p = _load(1653)
    assert p["kai"] == 1653
    assert len(p["matches"]) == 14          # BIG系は14試合、totoは13試合
    assert len(picks_for(p, "toto")) == 13
    assert len(picks_for(p, "minitotoA")) == 5
    assert len(picks_for(p, "minitotoB")) == 5
    assert len(picks_for(p, "totoGOAL3")) == 3

    first = picks_for(p, "toto")[0]
    assert first["date"] == "9/12"
    assert first["home"] == "水戸ホーリーホック"
    assert first["away"] == "川崎フロンターレ"
    # マーク番号は1から連番で、並び順と一致していること
    assert [m["mark"] for m in picks_for(p, "toto")] == list(range(1, 14))
    # GOAL3はホーム・アウェイの2マークで1試合
    assert picks_for(p, "totoGOAL3")[0]["mark"] == [1, 2]


def test_blank_cell_does_not_shift_columns() -> None:
    """第1650回の第6試合(柏-横浜FM)は mini toto A/B がどちらも空欄。
    ここがズレていないことが、このパーサの肝心なところ。"""
    p = _load(1650)
    row6 = next(m for m in p["matches"] if m["no"] == 6)
    assert row6["home"] == "柏レイソル"
    assert row6["marks"].get("toto") == 6
    assert "minitotoA" not in row6["marks"]
    assert "minitotoB" not in row6["marks"]

    # mini toto A は第1〜5試合、Bは第7〜11試合(第6試合をまたぐ)
    assert [m["no"] for m in picks_for(p, "minitotoA")] == [1, 2, 3, 4, 5]
    assert [m["no"] for m in picks_for(p, "minitotoB")] == [7, 8, 9, 10, 11]


def test_round_without_toto() -> None:
    """BIG系もtotoも無い回。列構成が変わってもヘッダから読めていること。"""
    p = _load(1651)
    assert p["kai"] == 1651
    assert len(p["matches"]) == 5
    assert picks_for(p, "toto") == []
    assert len(picks_for(p, "minitotoA")) == 5
    assert len(picks_for(p, "totoGOAL3")) == 3
    assert picks_for(p, "minitotoA")[0]["home"] == "レイラック滋賀ＦＣ"


def test_all_rows_have_a_date_and_teams() -> None:
    for kai in (1650, 1651, 1653):
        p = _load(kai)
        assert p["matches"], f"第{kai}回の試合が0件"
        for m in p["matches"]:
            assert "/" in m["date"], m
            assert m["home"] and m["away"], m
            assert m["marks"], m       # どのくじにも入らない行は無いはず


def main() -> None:
    tests = [
        test_circled_to_int,
        test_standard_round,
        test_blank_cell_does_not_shift_columns,
        test_round_without_toto,
        test_all_rows_have_a_date_and_teams,
    ]
    for t in tests:
        t()
        print(f"  ok {t.__name__}")
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
