"""
fetch_emperors_cup_events.py の検証。ネットワーク不要。

JFAの個別試合ページを模した合成HTMLを使う(実物の構造は
data/tmp/sample_ec_*.html で確認済み。あちらは.gitignore対象なのでテストからは参照しない)。

実行方法:
    python scripts/test_fetch_emperors_cup_events.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402
from fetch_emperors_cup_events import (  # noqa: E402
    MAX_ATTEMPTS,
    reclassify_cards,
    migrate_fetch_state,
    fetch_state,
    build_entry,
    normalize_minute,
    page_url,
    pair_subs,
    parse_match_page,
    pick_targets,
    should_fetch,
    split_name,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=JST)

CARD_SPAN = '<span class="card"><img src="/common/img/{icon}.gif" alt="{icon}" height="17" width="14"></span>'


def starter_row(pos_h, no_h, name_h, pos_a, no_a, name_a):
    return (
        "<tr>"
        f'<td class="position"><span class="gk">{pos_h}</span></td><td class="number">{no_h}</td><td>{name_h}</td>'
        f'<td class="position"><span class="gk">{pos_a}</span></td><td class="number">{no_a}</td><td>{name_a}</td>'
        "</tr>"
    )


def change_row(no_h, body_h, no_a, body_a):
    return (
        "<tr>"
        f'<td class="number">{no_h}</td><td colspan="2">{body_h}</td>'
        f'<td class="number">{no_a}</td><td colspan="2">{body_a}</td>'
        "</tr>"
    )


def make_page(with_video: bool = True, card_icon: str = "tim_mem_ico_02") -> str:
    rows = [
        '<tr><td class="header" colspan="3">湘南ベルマーレ</td><td class="header" colspan="3">ラインメール青森</td></tr>',
        starter_row("GK", 99, "上福元 直人", "GK", 17, "廣末 陸"),
        starter_row("DF", 4, "舘 幸希", "DF", 3, "遠藤 元一 (Cap.)"),
        starter_row("MF", 18, "池田 昌生 (Cap.)", "FW", 9, "今村 優介"),
        '<tr><td class="separate" colspan="6"></td></tr>',
        '<tr><td class="header" colspan="6">控え選手</td></tr>',
        starter_row("GK", 41, "岩瀬 陽", "GK", 21, "パク・グァンギュ"),
        starter_row("FW", 9, "ファビアン・ゴンザレス", "MF", 20, "平尾 泰雅"),
        '<tr><td class="separate" colspan="6"></td></tr>',
        '<tr><td class="number" colspan="2">監督</td><td>長澤 徹</td>'
        '<td class="number" colspan="2">監督</td><td>原崎 政人</td></tr>',
        '<tr><td class="separate" colspan="6"></td></tr>',
        '<tr><td class="header" colspan="6">選手交代</td></tr>',
        # ホームは73分の1件だけ。アウェイはＨＴと80分の2件(左右で本数が違う=片側のセルが空になる)
        change_row(38, "上月 壮一郎<span>▼</span>73分 OUT", 11, "藤原 拓海<span>▼</span>ＨＴ OUT"),
        change_row(30, "山口 豪太<span>▲</span>73分 IN", 33, "妹尾 直哉<span>▲</span>ＨＴ IN"),
        change_row("", "", 29, "田頭 亮太<span>▼</span>80分 OUT"),
        change_row("", "", 18, "梅澤 魁翔<span>▲</span>80分 IN"),
        '<tr><td class="separate" colspan="6"></td></tr>',
        '<tr><td class="header" colspan="6">警告・退場</td></tr>',
        change_row(5, "松本 大弥" + CARD_SPAN.format(icon="tim_mem_ico_02") + "45+1分",
                   8, "橋本 陸" + CARD_SPAN.format(icon=card_icon) + "49分"),
    ]
    video = (
        '<h4 id="jfa-tv">JFATV</h4><h5>【ハイライト】湘南ベルマーレvsラインメール青森</h5>'
        '<div class="movie_area"><iframe src="//www.youtube.com/embed/pFTxVOysHI4?rel=0"></iframe></div>'
        if with_video else ""
    )
    return (
        '<html><body>'
        '<div class="official-record"><a href="../schedule_result/pdf/m56.pdf">公式記録</a></div>'
        '<table class="match-result"><tbody>' + "".join(rows) + "</tbody></table>"
        + video +
        "</body></html>"
    )


def test_lineups_and_coach():
    d = parse_match_page(make_page())
    home, away = d["lineups"]["home"], d["lineups"]["away"]
    assert home["teamName"] == "湘南ベルマーレ" and away["teamName"] == "ラインメール青森"
    assert [p["name"] for p in home["starters"]] == ["上福元 直人", "舘 幸希", "池田 昌生"]
    assert [p["name"] for p in away["starters"]] == ["廣末 陸", "遠藤 元一", "今村 優介"]
    assert home["starters"][0] == {"no": "99", "pos": "GK", "name": "上福元 直人"}
    assert [p["name"] for p in home["subs"]] == ["岩瀬 陽", "ファビアン・ゴンザレス"]
    assert home["coach"] == "長澤 徹" and away["coach"] == "原崎 政人"
    # 監督行を控えメンバーに混ぜ込まないこと
    assert all("監督" not in p["name"] for p in home["subs"] + away["subs"])
    print("OK: スタメン・控え・監督の抽出")


def test_captain_flag():
    d = parse_match_page(make_page())
    caps = [p["name"] for p in d["lineups"]["home"]["starters"] + d["lineups"]["away"]["starters"] if p.get("captain")]
    assert caps == ["池田 昌生", "遠藤 元一"], caps
    # 氏名から (Cap.) を取り除くこと
    assert all("Cap" not in p["name"] for p in d["lineups"]["home"]["starters"])
    assert split_name("池田 昌生 (Cap.)") == ("池田 昌生", True)
    assert split_name("上福元 直人") == ("上福元 直人", False)
    print("OK: キャプテン表記の分離")


def test_subs_pairing_including_uneven_sides():
    d = parse_match_page(make_page())
    home = [s for s in d["subs"] if s["side"] == "home"]
    away = [s for s in d["subs"] if s["side"] == "away"]
    assert len(home) == 1, home
    assert home[0]["minute"] == "73"
    assert home[0]["out"]["name"] == "上月 壮一郎" and home[0]["in"]["name"] == "山口 豪太"
    assert home[0]["out"]["no"] == "38" and home[0]["in"]["no"] == "30"
    assert len(away) == 2, away
    assert away[0]["minute"] == "HT", "ＨＴ(全角)はHTに正規化する"
    assert away[1]["minute"] == "80"
    assert away[1]["out"]["name"] == "田頭 亮太" and away[1]["in"]["name"] == "梅澤 魁翔"
    print("OK: 交代のOUT/IN組み合わせ(左右で本数が違うケース含む)")


def test_pair_subs_keeps_unpaired_entries():
    """OUTの次がINでない壊れた並びでも情報を捨てない。"""
    entries = [
        {"no": "7", "name": "A", "variant": "out", "minute": "60"},
        {"no": "8", "name": "B", "variant": "out", "minute": "61"},
    ]
    got = pair_subs(entries, "home")
    assert len(got) == 2
    assert got[0]["out"]["name"] == "A" and got[0]["in"] is None
    assert got[1]["out"]["name"] == "B" and got[1]["in"] is None
    print("OK: 組にならない交代も落とさない")


def test_cards():
    d = parse_match_page(make_page())
    assert len(d["cards"]) == 2, d["cards"]
    h = [c for c in d["cards"] if c["side"] == "home"][0]
    a = [c for c in d["cards"] if c["side"] == "away"][0]
    assert h == {"no": "5", "name": "松本 大弥", "type": "yellow", "icon": "tim_mem_ico_02",
                 "minute": "45+1", "side": "home"}, h
    assert a["name"] == "橋本 陸" and a["minute"] == "49"
    print("OK: 警告の抽出(アディショナルタイム表記込み)")


def test_unknown_card_icon_is_not_guessed():
    """知らないアイコンを黄や赤と決めつけない(未確認のものを嘘の種別で出さない)。"""
    d = parse_match_page(make_page(card_icon="tim_mem_ico_99"))
    a = [c for c in d["cards"] if c["side"] == "away"][0]
    assert a["type"] == "unknown", a
    assert a["icon"] == "tim_mem_ico_99", "後で正しく分類できるよう元のアイコン名を残す"
    print("OK: 未知のカードアイコンはunknownのまま保存する")


def test_red_card_icon():
    """tim_mem_ico_01 は退場(赤)。2026年大会の実データで確定した(CARD_ICON_TYPESのコメント参照)。"""
    d = parse_match_page(make_page(card_icon="tim_mem_ico_01"))
    a = [c for c in d["cards"] if c["side"] == "away"][0]
    assert a["type"] == "red", a
    print("OK: 退場(赤)アイコンの分類")


def test_reclassify_cards_without_refetching():
    """アイコン対応表を更新したら、ページを取り直さずに保存済みの種別だけ直せること。"""
    events = {
        "7": {"cards": [
            {"name": "A", "icon": "tim_mem_ico_01", "type": "unknown"},
            {"name": "B", "icon": "tim_mem_ico_02", "type": "yellow"},
            {"name": "C", "icon": "tim_mem_ico_99", "type": "yellow"},
            {"name": "D", "type": "yellow"},
        ]},
        "8": {"cards": []},
        "9": {},
    }
    changed = reclassify_cards(events)
    types = [c["type"] for c in events["7"]["cards"]]
    assert types == ["red", "yellow", "unknown", "yellow"], types
    assert changed == 2, "直したのは01(unknown->red)と99(yellow->unknown)の2件のはず"
    assert reclassify_cards(events) == 0, "2回目は変更なし(冪等)"
    print("OK: 取り直さずにカード種別を分類し直す")


def test_video_and_official_report():
    d = parse_match_page(make_page())
    assert d["videoId"] == "pFTxVOysHI4"
    assert d["videoTitle"] == "【ハイライト】湘南ベルマーレvsラインメール青森"
    assert d["officialReportPath"] == "../schedule_result/pdf/m56.pdf"
    d2 = parse_match_page(make_page(with_video=False))
    assert d2["videoId"] is None and d2["videoTitle"] is None, "動画が無い試合でも落ちない"
    assert d2["lineups"]["home"]["starters"], "動画が無くてもメンバーは取れる"
    print("OK: JFATVの動画IDとタイトル・公式記録PDF")


def test_broken_page_raises():
    for bad in ("", "<html><body>お探しのページはありません</body></html>"):
        try:
            parse_match_page(bad)
        except ValueError:
            continue
        raise AssertionError("表が無いページはValueErrorにするはず")
    print("OK: 構造が変わったページは例外にする")


def test_normalize_minute():
    assert normalize_minute("73分 OUT") == "73"
    assert normalize_minute("90+2分") == "90+2"
    assert normalize_minute("ＨＴ OUT") == "HT"
    assert normalize_minute("") == ""
    print("OK: 分表記の正規化")


def _entry(attempts=1, last=None, video="x", starters=True):
    return {
        "fetchState": {"attempts": attempts, "lastFetchedAtJst": (last or NOW).isoformat()},
        "lineups": {"home": {"starters": [{"name": "A"}] if starters else []}},
        "videoId": video,
    }


def test_should_fetch_rules():
    assert should_fetch(None, NOW) is True, "未取得なら取る"
    assert should_fetch(_entry(), NOW) is False, "メンバーも動画も揃っていれば取らない"
    assert should_fetch(_entry(video=None), NOW) is False, "動画待ちでもクールダウン中は取らない"
    assert should_fetch(_entry(video=None, last=NOW - timedelta(hours=13)), NOW) is True, \
        "クールダウンが明けたら動画のために取り直す"
    assert should_fetch(_entry(video=None, attempts=MAX_ATTEMPTS), NOW) is False, "試行上限で打ち切る"
    assert should_fetch(_entry(starters=False), NOW) is True, "メンバーが取れていなければ取り直す"
    assert should_fetch(_entry(), NOW, force=True) is True, "--forceは全部取り直す"
    # 古い形式(トップレベルにlastFetchedAtJst/attempts)でも同じ判断になること
    legacy = {"attempts": MAX_ATTEMPTS, "lineups": {"home": {"starters": [{"name": "A"}]}}, "videoId": None,
              "lastFetchedAtJst": (NOW - timedelta(hours=99)).isoformat()}
    assert should_fetch(legacy, NOW) is False, "古い形式のattemptsも読めること"
    print("OK: 再取得の判定(未取得/動画待ち/クールダウン/試行上限)")


def test_fetch_state_is_grouped_to_avoid_pointless_commits():
    """
    取得記録は fetchState にまとめる。中身が変わらなくても毎回動く値なので、
    git_diff_ignoring_timestamps.py の VOLATILE_KEYS で丸ごと無視させるため
    (バラバラのトップレベルキーだと「実質変更なし」の判定をすり抜けてしまう)。
    """
    events = {"1": {"lastFetchedAtJst": "2026-08-27T12:00:00+09:00", "attempts": 3, "cards": []}}
    moved = migrate_fetch_state(events)
    assert moved == 1
    assert events["1"]["fetchState"] == {"lastFetchedAtJst": "2026-08-27T12:00:00+09:00", "attempts": 3}
    assert "lastFetchedAtJst" not in events["1"] and "attempts" not in events["1"], "古いキーは残さない"
    assert events["1"]["cards"] == [], "他のフィールドは触らない"
    assert migrate_fetch_state(events) == 0, "2回目は変更なし(冪等)"
    assert fetch_state(events["1"])["attempts"] == 3
    assert fetch_state(None) == {}
    print("OK: 取得記録をfetchStateにまとめる(無駄なコミット防止)")


def test_pick_targets_only_finished():
    cup = {"matches": [
        {"matchNumber": "1", "finished": True, "round": "1回戦"},
        {"matchNumber": "2", "finished": False, "round": "3回戦"},
        {"matchNumber": "3", "finished": True, "round": "2回戦"},
    ]}
    got = pick_targets(cup, {}, NOW)
    assert [m["matchNumber"] for m in got] == ["1", "3"], "未消化の試合は取りに行かない"
    got2 = pick_targets(cup, {}, NOW, limit=1)
    assert len(got2) == 1, "--limitで打ち切る"
    got3 = pick_targets(cup, {}, NOW, only=["2"])
    assert [m["matchNumber"] for m in got3] == ["2"], "--onlyは未消化でも指定どおり取りに行く"
    print("OK: 取得対象の選別")


def test_build_entry_keeps_previous_lineups_on_empty_parse():
    """今回メンバーが取れなくても、前回取れていた分を消さない(部分失敗で良いデータを壊さない)。"""
    match = {"matchNumber": "56", "round": "2回戦"}
    prev = {
        "matchNumber": "56", "fetchState": {"attempts": 1, "lastFetchedAtJst": NOW.isoformat()},
        "lineups": {"home": {"teamName": "湘南", "starters": [{"name": "A"}], "subs": [], "coach": None},
                    "away": {"teamName": "青森", "starters": [{"name": "B"}], "subs": [], "coach": None}},
        "subs": [{"side": "home"}], "cards": [], "videoId": None, "videoTitle": None,
    }
    empty = {"lineups": {"home": {"teamName": "", "starters": [], "subs": [], "coach": None},
                         "away": {"teamName": "", "starters": [], "subs": [], "coach": None}},
             "subs": [], "cards": [], "videoId": "NEWVID", "videoTitle": "【ハイライト】",
             "officialReportPath": None}
    got = build_entry(match, empty, 2026, NOW, prev)
    assert got["lineups"]["home"]["starters"] == [{"name": "A"}], "前回のメンバーを維持する"
    assert got["subs"] == [{"side": "home"}], "前回の交代も維持する"
    assert got["videoId"] == "NEWVID", "後から公開された動画は取り込む"
    assert got["fetchState"]["attempts"] == 2

    # 正常に取れた場合は素直に今回の内容で作る
    parsed = parse_match_page(make_page())
    parsed["officialReportPath"] = "../schedule_result/pdf/m56.pdf"
    fresh = build_entry(match, parsed, 2026, NOW, None)
    assert len(fresh["lineups"]["home"]["starters"]) == 3
    assert fresh["fetchState"]["attempts"] == 1
    assert fresh["url"] == page_url(2026, "56")
    assert fresh["officialReportUrl"] == "https://www.jfa.jp/match/emperorscup_2026/schedule_result/pdf/m56.pdf"
    print("OK: 既存データのマージ(部分失敗で上書きしない)")


def main() -> None:
    tests = [
        test_lineups_and_coach,
        test_captain_flag,
        test_subs_pairing_including_uneven_sides,
        test_pair_subs_keeps_unpaired_entries,
        test_cards,
        test_unknown_card_icon_is_not_guessed,
        test_red_card_icon,
        test_reclassify_cards_without_refetching,
        test_video_and_official_report,
        test_broken_page_raises,
        test_normalize_minute,
        test_should_fetch_rules,
        test_fetch_state_is_grouped_to_avoid_pointless_commits,
        test_pick_targets_only_finished,
        test_build_entry_keeps_previous_lineups_on_empty_parse,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
