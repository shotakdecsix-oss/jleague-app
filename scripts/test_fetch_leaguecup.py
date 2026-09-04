"""fetch_leaguecup のパーサ・マージのテスト。

サンプルは 2026-09-01 に実物の https://www.jleague.jp/match/leaguecup/ から採取した
Next.jsペイロードの並びを、必要な部分だけ縮めて再現したもの。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_leaguecup import (  # noqa: E402
    _date_from_code,
    _final_score_from_goals,
    fill_scores_from_events,
    merge,
    parse_index,
)

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


def test_date_comes_from_code_not_heading() -> None:
    """日付見出しを取りこぼしても、試合コードから正しい日付になる。

    2026-09-04 に実際に起きた事故の再現: 9/2 に終わった15試合の見出しが拾えず、
    _last_before() が次のグループの 9/9 を引き当てて、全部「6日後にキックオフ」に見えていた。
    """
    assert _date_from_code("2026", "090201") == "2026-09-02"
    assert _date_from_code("2026", "090907") == "2026-09-09"
    assert _date_from_code("2026", "123101") == "2026-12-31"
    # MMDDとして読めないコードは None を返し、見出しへのフォールバックに任せる
    assert _date_from_code("2026", "133101") is None, "13月は日付にならない"
    assert _date_from_code("2026", "023001") is None, "2月30日は日付にならない"
    assert _date_from_code("2026", "9021") is None, "桁が足りない"
    assert _date_from_code("2026", "") is None
    print("OK: 日付は日付見出しではなく試合コードから決まる")


def test_final_score_from_goals() -> None:
    """scoreAfter の最大値が最終スコアになる(得点は単調に増えるため)。"""
    goals = [
        {"minute": "88", "scoreAfter": "0-2"},
        {"minute": "11", "scoreAfter": "0-1"},
    ]
    assert _final_score_from_goals(goals) == {"home": 0, "away": 2}, "並び順に依存しないはず"
    # 90+3 のような表記でも minute を解釈しないので影響を受けない
    assert _final_score_from_goals([
        {"minute": "90+3", "scoreAfter": "2-1"},
        {"minute": "45+1", "scoreAfter": "1-0"},
        {"minute": "70", "scoreAfter": "1-1"},
    ]) == {"home": 2, "away": 1}
    assert _final_score_from_goals([]) is None, "得点なしはNone(呼び出し側で0-0を判断する)"
    assert _final_score_from_goals([{"minute": "10"}]) is None, "scoreAfterが無ければNone"


def test_fill_scores_from_events(tmp_events) -> None:
    """日程ページでスコアを取れなかった消化済み試合に、得点イベントから補う。"""
    matches = [
        # 得点あり
        {"code": "090201", "date": "2026-09-02", "score": None, "finished": False},
        # 得点なし(イベント取得済み) -> 0-0
        {"code": "090206", "date": "2026-09-02", "score": None, "finished": False},
        # 未来の試合。触らない
        {"code": "090901", "date": "2999-01-01", "score": None, "finished": False},
        # 日程ページから正規に取れている。触らない
        {"code": "090202", "date": "2026-09-02", "score": {"home": 9, "away": 9},
         "finished": True},
        # イベント自体が無い。触らない
        {"code": "090299", "date": "2026-09-02", "score": None, "finished": False},
    ]
    filled = fill_scores_from_events(matches)
    assert filled == 2, filled
    assert matches[0]["score"] == {"home": 0, "away": 2}
    assert matches[0]["finished"] is True
    assert matches[0]["scoreSource"] == "events"
    assert matches[1]["score"] == {"home": 0, "away": 0}, "得点0件の消化済みは0-0"
    assert matches[2]["score"] is None, "未来の試合に触ってはいけない"
    assert matches[3]["score"] == {"home": 9, "away": 9}, "日程ページ由来を上書きしてはいけない"
    assert "scoreSource" not in matches[3]
    assert matches[4]["score"] is None, "イベントが無ければ触らない"
    print("OK: スコアを取れなかった消化済み試合を、得点イベントから補える")


def _with_temp_events(fn):
    """fill_scores_from_events が読む JSON を、テスト用に差し替えて実行する。"""
    import json
    import tempfile
    from pathlib import Path as _P

    import fetch_leaguecup as mod

    data = {"events": {
        "090201": {"goals": [{"minute": "88", "scoreAfter": "0-2"},
                             {"minute": "11", "scoreAfter": "0-1"}]},
        "090206": {"goals": []},
        "090202": {"goals": [{"minute": "5", "scoreAfter": "1-0"}]},
        "090901": {"goals": []},
    }}
    with tempfile.TemporaryDirectory() as d:
        path = _P(d) / "leaguecup_match_events.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        original = mod.EVENTS_PATH
        mod.EVENTS_PATH = path
        try:
            fn(None)
        finally:
            mod.EVENTS_PATH = original


def main() -> None:
    tests = [
        test_parses_upcoming_match,
        test_parses_finished_match_score,
        test_keeps_unknown_time_text_as_is,
        test_unknown_club_is_not_an_error,
        test_merge_keeps_past_rounds,
        test_date_comes_from_code_not_heading,
        test_final_score_from_goals,
        lambda: _with_temp_events(test_fill_scores_from_events),
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
