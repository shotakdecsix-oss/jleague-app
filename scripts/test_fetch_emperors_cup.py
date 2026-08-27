"""
fetch_emperors_cup.py の検証。ネットワーク不要(JFAのAPIレスポンスを模した合成JSONを使う)。

サンプルは実際に www.jfa.jp/match/emperorscup_2026/match/schedule.json が返す形をそのまま
写したもの(未消化・PK決着・延長・日程未定・アマチュア相手の各パターン)。

実行方法:
    python scripts/test_fetch_emperors_cup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_emperors_cup import (  # noqa: E402
    build_emperors_cup,
    guard_shrink,
    match_page_url,
    norm,
    parse_kickoff,
    parse_score,
    parse_scorer_line,
    parse_scorers,
    resolve_team,
)

# data/masters を読まずに済むよう、テスト内では索引を手で作る
INDEX = {
    norm("湘南ベルマーレ"): {"idTeam": "137712", "league": "j2", "ja": "湘南ベルマーレ"},
    norm("鹿島アントラーズ"): {"idTeam": "137707", "league": "j1", "ja": "鹿島アントラーズ"},
    norm("ヴァンフォーレ甲府"): {"idTeam": "137730", "league": "j2", "ja": "ヴァンフォーレ甲府"},
}

FINISHED_PK = {
    "matchTypeName": "１回戦",
    "matchNumber": "1",
    "matchDate": "2026/08/19",
    "matchDateJpn": "2026/08/19",
    "matchDateWeek": "水",
    "matchTime": "18:30",
    "matchTimeJpn": "18:30",
    "venue": "三交鈴鹿",
    "venueFullName": "三重交通Gスポーツの杜鈴鹿サッカー・ラグビー場メインG",
    "homeTeamName": "アトレチコ鈴鹿",
    "homeTeamQualificationDescription": "三重県代表",
    "awayTeamName": "FC BASARA HYOGO",
    "awayTeamQualificationDescription": "兵庫県代表",
    "score": {
        "homeWinFlag": True, "awayWinFlag": False,
        "homeScore": "1", "awayScore": "1",
        "homeTeamScore1st": "0", "awayTeamScore1st": "0",
        "homeTeamScore2nd": "1", "awayTeamScore2nd": "1",
        "exMatch": True,
        "homeTeamScore1ex": "0", "awayTeamScore1ex": "0",
        "homeTeamScore2ex": "0", "awayTeamScore2ex": "0",
        "homePKScore": "3", "awayPKScore": "1",
    },
    "scorer": {"homeScorer": ["90+2分 渡邊 星来"], "awayScorer": ["88分 小延 将大"]},
    "matchStatus": "試合終了",
    "officialReportURL": "../schedule_result/pdf/m01.pdf",
}

FINISHED_CLUB = {
    "matchTypeName": "２回戦",
    "matchNumber": "56",
    "matchDate": "2026/08/26",
    "matchDateJpn": "2026/08/26",
    "matchDateWeek": "水",
    "matchTime": "19:00",
    "matchTimeJpn": "19:00",
    "venue": "レモンＳ",
    "venueFullName": "レモンガススタジアム平塚",
    "homeTeamName": "湘南ベルマーレ",
    "homeTeamQualificationDescription": "J2",
    "awayTeamName": "ラインメール青森",
    "awayTeamQualificationDescription": "青森県代表",
    "score": {
        "homeWinFlag": True, "awayWinFlag": False,
        "homeScore": "1", "awayScore": "0",
        "homeTeamScore1st": "0", "awayTeamScore1st": "0",
        "homeTeamScore2nd": "1", "awayTeamScore2nd": "0",
        "exMatch": False,
    },
    "scorer": {"homeScorer": ["66分 松村 秀明"], "awayScorer": []},
    "matchStatus": "試合終了",
    "officialReportURL": "../schedule_result/pdf/m56.pdf",
}

UNPLAYED_TBD = {
    "matchTypeName": "３回戦",
    "matchNumber": "57",
    "matchDate": "未定",
    "matchDateJpn": "未定",
    "matchDateWeek": "",
    "matchTime": "未定",
    "matchTimeJpn": "未定",
    "venue": "未定",
    "venueFullName": "",
    "homeTeamName": "鹿島アントラーズ",
    "homeTeamQualificationDescription": "J1",
    "awayTeamName": "ヴァンフォーレ甲府",
    "awayTeamQualificationDescription": "J2",
    "score": {
        "homeWinFlag": False, "awayWinFlag": False,
        "homeScore": "", "awayScore": "",
        "homeTeamScore1st": "", "awayTeamScore1st": "",
        "homeTeamScore2nd": "", "awayTeamScore2nd": "",
        "exMatch": False,
    },
    "scorer": {"homeScorer": [], "awayScorer": []},
    "matchStatus": "",
    "officialReportURL": "",
}

PAYLOAD = {
    "matchScheduleList": {
        "competitionName": "天皇杯 JFA 第106回全日本サッカー選手権大会",
        "matchSchedule": [FINISHED_PK, FINISHED_CLUB, UNPLAYED_TBD],
    }
}


def test_parse_kickoff_and_tbd():
    assert parse_kickoff("2026/08/26", "19:00") == "2026-08-26T19:00:00+09:00"
    assert parse_kickoff("未定", "未定") is None, "日付・時刻が未定ならNone"
    assert parse_kickoff("2026/08/26", "未定") is None, "時刻だけ未定でもキックオフは確定しない"
    assert parse_kickoff("", "") is None
    assert parse_kickoff("2026/13/99", "19:00") is None, "ありえない日付はNoneに落とす"
    print("OK: 日時のパースと「未定」の扱い")


def test_parse_score_pk_and_unplayed():
    s = parse_score(FINISHED_PK["score"])
    assert s == {"home": 1, "away": 1, "extra": True, "pk": {"home": 3, "away": 1}}, s
    s2 = parse_score(FINISHED_CLUB["score"])
    assert s2 == {"home": 1, "away": 0, "extra": False}, s2
    assert "pk" not in s2, "PKが無い試合にpkキーを作らない"
    assert parse_score(UNPLAYED_TBD["score"]) is None, "スコアが空文字なら未消化としてNone"
    assert parse_score(None) is None
    assert parse_score({"homeScore": "0", "awayScore": "0"}) == {"home": 0, "away": 0, "extra": False}, \
        "0-0を「スコア無し」と誤判定しないこと"
    print("OK: スコア(延長・PK・未消化・0-0)の判定")


def test_parse_scorer_line():
    a = parse_scorer_line("90+2分 渡邊 星来")
    assert a["minute"] == "90+2" and a["name"] == "渡邊 星来", a
    b = parse_scorer_line("66分 松村 秀明")
    assert b["minute"] == "66" and b["name"] == "松村 秀明", b
    c = parse_scorer_line("フォーマット不明")
    assert c["minute"] is None and c["name"] == "フォーマット不明", c
    assert c["text"] == "フォーマット不明", "元の文字列は必ず残す"
    sc = parse_scorers(FINISHED_PK["scorer"])
    assert len(sc["home"]) == 1 and len(sc["away"]) == 1
    assert parse_scorers({"homeScorer": [], "awayScorer": []}) == {"home": [], "away": []}
    assert parse_scorers(None) == {"home": [], "away": []}
    print("OK: 得点者文字列のパース")


def test_resolve_team_matches_club_and_ignores_amateur():
    t = resolve_team("湘南ベルマーレ", "J2", INDEX)
    assert t["idTeam"] == "137712" and t["league"] == "j2", t
    a = resolve_team("ラインメール青森", "青森県代表", INDEX)
    assert a["idTeam"] is None and a["league"] is None, "アマチュアは未照合のままでよい"
    assert a["name"] == "ラインメール青森" and a["qualification"] == "青森県代表"
    print("OK: Jクラブの突き合わせとアマチュアの扱い")


def test_norm_absorbs_width_and_symbols():
    assert norm("横浜Ｆ・マリノス") == norm("横浜F・マリノス"), "全角/半角と中黒のゆれを吸収する"
    assert norm("ＦＣ東京") == norm("FC東京")
    print("OK: チーム名の正規化")


def test_build_emperors_cup_structure():
    out = build_emperors_cup(PAYLOAD, INDEX, 2026)

    assert out["meta"]["competitionName"] == "天皇杯 JFA 第106回全日本サッカー選手権大会"
    assert out["meta"]["matchCount"] == 3
    assert out["meta"]["finishedCount"] == 2
    assert out["meta"]["year"] == 2026
    assert out["meta"]["source"] == "JFA"

    # ラウンドは出現順のまま。全角数字はNFKCで半角に寄せる
    assert out["rounds"] == ["1回戦", "2回戦", "3回戦"], out["rounds"]

    m = out["matches"][1]
    assert m["round"] == "2回戦"
    assert m["matchNumber"] == "56"
    assert m["kickoffJst"] == "2026-08-26T19:00:00+09:00"
    assert m["kickoffTbd"] is False
    assert m["date"] == "2026-08-26"
    assert m["venue"] == "レモンＳ"
    assert m["home"]["idTeam"] == "137712"
    assert m["away"]["idTeam"] is None
    assert m["finished"] is True
    assert m["scorers"]["home"][0]["name"] == "松村 秀明"
    # matchNumberは個別試合ページのURLにそのまま対応する(フェーズ2の入口)
    assert m["matchPageUrl"] == "https://www.jfa.jp/match/emperorscup_2026/match_page/m56.html"
    assert m["matchPageUrl"] == match_page_url(2026, "56")

    tbd = out["matches"][2]
    assert tbd["kickoffTbd"] is True and tbd["kickoffJst"] is None
    assert tbd["date"] is None
    assert tbd["venue"] == "", "会場「未定」は空文字に正規化する"
    assert tbd["finished"] is False and tbd["score"] is None
    print("OK: 出力構造(メタ・ラウンド順・試合)")


def test_build_emperors_cup_rejects_broken_payload():
    for bad in ({}, {"matchScheduleList": None}, {"matchScheduleList": {"matchSchedule": []}}):
        try:
            build_emperors_cup(bad, INDEX, 2026)
        except ValueError:
            continue
        raise AssertionError(f"壊れたレスポンスはValueErrorで弾くはず: {bad}")
    print("OK: 壊れたレスポンスは例外にする")


def test_guard_shrink():
    assert guard_shrink(87, 87) is None
    assert guard_shrink(60, 87) is None, "1回戦が消化されて統合されるような自然な減りは止めない"
    assert guard_shrink(3, 87) is not None, "半分未満まで激減したら止める"
    assert guard_shrink(1, 0) is None, "既存ファイルが無い初回は止めない"
    assert guard_shrink(2, 4) is None, "件数が少ないうち(<8)は判定しない"
    print("OK: 件数激減ガード")


def main() -> None:
    tests = [
        test_parse_kickoff_and_tbd,
        test_parse_score_pk_and_unplayed,
        test_parse_scorer_line,
        test_resolve_team_matches_club_and_ignores_amateur,
        test_norm_absorbs_width_and_symbols,
        test_build_emperors_cup_structure,
        test_build_emperors_cup_rejects_broken_payload,
        test_guard_shrink,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
