"""fetch_leaguecup のパーサ・マージのテスト。

サンプルは 2026-09-01 に実物の https://www.jleague.jp/match/leaguecup/ から採取した
Next.jsペイロードの並びを、必要な部分だけ縮めて再現したもの。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_leaguecup import merge, parse_index  # noqa: E402

MASTER = {
    "ヴァンラーレ八戸": {"idTeam": "141245", "league": "j2", "ja": "ヴァンラーレ八戸"},
    "八戸": {"idTeam": "141245", "league": "j2", "ja": "ヴァンラーレ八戸"},
    "栃木シティ": {"idTeam": "150222", "league": "j2", "ja": "栃木シティ"},
}


def _match_block(code: str, home: str, away: str, info: str) -> str:
    """1試合ぶんのペイロード断片。実物と同じ並び(pc名 -> match-info -> pc名 -> 会場)。"""
    return (
        f'"href":"/match/leaguecup/2026/{code}","locale":"$undefined","localeCookie":false,'
        f'"className":"m-schedule__link m-schedule__link--clickable","children":['
        f'"$","div",null,{{"className":"m-schedule__match","data-media":"pc","children":"{home}"}},'
        f'{{"className":"m-schedule__team-name","data-media":"sp","children":"略{home[:2]}"}},'
        f'{{"className":"m-schedule__match-info","children":"{info}"}},'
        f'{{"className":"m-schedule__team-name","data-media":"pc","children":"{away}"}},'
        f'{{"className":"m-schedule__team-name","data-media":"sp","children":"略{away[:2]}"}},'
        f'{{"className":"m-schedule__info-stadium","data-media":"pc","children":"プライフーズスタジアム"}}]'
    )


HEADER = '{"className":"h","children":"１回戦"}{"className":"d","children":"2026/9/2 (水)"}'


def test_parses_upcoming_match() -> None:
    text = HEADER + _match_block("090201", "ヴァンラーレ八戸", "栃木シティ", "18:30")
    out = parse_index(text, MASTER)
    assert len(out) == 1, out
    m = out[0]
    assert m["code"] == "090201" and m["round"] == "１回戦" and m["date"] == "2026-09-02", m
    assert m["kickoffJst"] == "2026-09-02T18:30:00+09:00", m
    assert m["venue"] == "プライフーズスタジアム", m
    assert m["home"]["idTeam"] == "141245" and m["away"]["idTeam"] == "150222", m
    assert m["finished"] is False and m["score"] is None, m
    assert m["matchPageUrl"] == "https://www.jleague.jp/match/leaguecup/2026/090201/", m
    print("OK: 未消化の試合からコード/ラウンド/日付/キックオフ/会場/クラブ照合が取れる")


def test_parses_finished_match_score() -> None:
    """消化後は m-schedule__match-info がスコア表示に変わる想定。

    ルヴァンには消化済みの試合がまだ無く実物で確認できていないため、
    「時刻でなければスコアとして読む」という解釈が効くことだけを固定しておく。
    """
    text = HEADER + _match_block("090201", "ヴァンラーレ八戸", "栃木シティ", "2-1")
    m = parse_index(text, MASTER)[0]
    assert m["finished"] is True, m
    assert m["score"] == {"home": 2, "away": 1}, m
    assert m["kickoffJst"] is None, m
    print("OK: スコア表記の試合は finished=True とスコアになる")


def test_keeps_unknown_time_text_as_is() -> None:
    """時刻でもスコアでもない表記が来ても落とさず、生の文字列を残す(作り変わりの検知用)。"""
    text = HEADER + _match_block("090201", "ヴァンラーレ八戸", "栃木シティ", "中止")
    m = parse_index(text, MASTER)[0]
    assert m["timeText"] == "中止" and m["finished"] is False and m["score"] is None, m
    print("OK: 想定外の表記は timeText にそのまま残り、例外にならない")


def test_unknown_club_is_not_an_error() -> None:
    text = HEADER + _match_block("090299", "どこかのクラブ", "栃木シティ", "19:00")
    m = parse_index(text, MASTER)[0]
    assert m["home"]["idTeam"] is None and m["home"]["name"] == "どこかのクラブ", m
    assert m["away"]["idTeam"] == "150222", m
    print("OK: マスタに無いクラブは idTeam=null で残す(異常終了しない)")


def test_merge_keeps_past_rounds() -> None:
    """日程ページは現在のラウンドしか出さないので、過去ラウンドを消してはいけない。"""
    old = [{"code": "080101", "date": "2026-08-01", "round": "予選"},
           {"code": "090201", "date": "2026-09-02", "round": "１回戦", "finished": False}]
    fresh = [{"code": "090201", "date": "2026-09-02", "round": "１回戦", "finished": True},
             {"code": "090901", "date": "2026-09-09", "round": "２回戦"}]
    out = merge(old, fresh)
    assert [m["code"] for m in out] == ["080101", "090201", "090901"], out
    assert out[1]["finished"] is True, "同じ試合は今回見えた方で上書きするはず"
    print("OK: 過去ラウンドを残しつつ、同じ試合は最新で上書きし、日付順に並ぶ")


def main() -> None:
    tests = [
        test_parses_upcoming_match,
        test_parses_finished_match_score,
        test_keeps_unknown_time_text_as_is,
        test_unknown_club_is_not_an_error,
        test_merge_keeps_past_rounds,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
