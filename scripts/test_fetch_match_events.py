"""
scripts/match_events_parser.py と scripts/fetch_match_events.py のオフラインテスト。
python scripts/test_fetch_match_events.py で実行する(pytest不使用、標準ライブラリのみ)。

data/tmp/sample_match_*.html は scripts/save_sample_html.py の実行結果で、.gitignore対象
(リポジトリにコミットされない)。無ければサンプル依存のテストはスキップし、他のテストだけ実行する。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_match_events import (  # noqa: E402
    EVENTS_WINDOW_HOURS,
    build_lineups,
    load_all_teams,
    parse_and_merge,
    pick_candidates,
)
from match_events_parser import (  # noqa: E402
    extract_schedule_index,
    find_cards,
    find_formations,
    find_goals,
    find_highlight_video_id,
    find_lineup_members,
    find_subs,
)
from team_matching import load_master_teams  # noqa: E402
from time_utils import JST  # noqa: E402
import youtube_highlights  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = BASE_DIR / "data" / "tmp"


def _match(id_event="E1", kickoff_jst="2026-08-22T14:00:00+09:00", home_id="T_A", away_id="T_B"):
    return {
        "idEvent": id_event,
        "kickoffJst": kickoff_jst,
        "home": {"idTeam": home_id, "ja": "ホーム"},
        "away": {"idTeam": away_id, "ja": "アウェイ"},
    }


# ---------- pick_candidates: 時間窓の判定 ----------

def test_pick_candidates_window():
    now = datetime.fromisoformat("2026-08-22T18:00:00+09:00")
    matches = [
        _match("in_window", "2026-08-22T14:00:00+09:00"),   # 4時間前。窓内
        _match("just_now", "2026-08-22T17:59:00+09:00"),    # 直前。窓内
        _match("too_old", "2026-08-20T18:00:00+09:00"),     # 48時間前。窓外
        _match("future", "2026-08-22T20:00:00+09:00"),      # まだキックオフ前。対象外
        _match("no_kickoff", None),                          # 日時未定。対象外
    ]
    matches[4]["kickoffJst"] = None
    picked = {m["idEvent"] for m in pick_candidates(matches, now)}
    assert picked == {"in_window", "just_now"}, picked
    print(f"OK: pick_candidates() が窓({EVENTS_WINDOW_HOURS}時間)内の試合だけを選ぶことを確認")


# ---------- parse_and_merge: 退行(0件上書き)防止の安全弁 ----------

def test_parse_and_merge_regression_guard(monkeypatch=None):
    fake_master = {
        "teams": [
            {"idTeam": "T_A", "en": "a", "aliases": [], "aliasesJa": [], "ja": "ホーム"},
            {"idTeam": "T_B", "en": "b", "aliases": [], "aliasesJa": [], "ja": "アウェイ"},
        ]
    }
    all_teams = load_master_teams("j2", fake_master)
    existing = {"E1": {"goals": [{"minute": "10"}], "cards": [], "subs": []}}

    import fetch_match_events as fme

    # HTMLが取れたが目印文字列が無い(=0件になる)ケースを模擬する
    original_fetch_html = fme.fetch_html
    fme.fetch_html = lambda url: "<html>マークアップが変わって何も拾えないページ</html>"
    try:
        failed = []
        result = parse_and_merge("E1", {"code": "000000", "url": "dummy"}, all_teams, existing, failed)
        assert result is None, "既存データがあるのに0件で上書きしてしまっている"
        assert failed and failed[0]["reason"] == "regression_zero_events", failed
        print("OK: 0件への退行時は既存データを上書きしないことを確認")

        # 初回(既存データが無い)は0件でも正当な結果として上書きしてよい
        failed2 = []
        result2 = parse_and_merge("E2", {"code": "000000", "url": "dummy"}, all_teams, {}, failed2)
        assert result2 is not None and result2["goals"] == []
        assert not failed2
        print("OK: 既存データが無い試合の0件は正当な結果として書き込まれることを確認")
    finally:
        fme.fetch_html = original_fetch_html


# ---------- lineups: 抽出できなかった回は前回分を維持する ----------

def test_lineups_carry_forward_when_missing():
    fake_master = {
        "teams": [
            {"idTeam": "T_A", "en": "a", "aliases": [], "aliasesJa": [], "ja": "ホーム"},
            {"idTeam": "T_B", "en": "b", "aliases": [], "aliasesJa": [], "ja": "アウェイ"},
        ]
    }
    all_teams = load_master_teams("j2", fake_master)
    old_lineups = {"home": {"idTeam": "T_A", "formation": "4-4-2", "players": [{"id": 1, "name": "選手A", "number": "9"}]},
                   "away": {"idTeam": "T_B", "formation": "4-3-3", "players": []}}
    existing = {"E1": {"goals": [], "cards": [], "subs": [], "lineups": old_lineups}}

    import fetch_match_events as fme

    original_fetch_html = fme.fetch_html
    # ゴール等の目印もformationsも無い、マークアップが変わったページを模擬する
    fme.fetch_html = lambda url: "<html>何も拾えないページ</html>"
    try:
        failed = []
        result = parse_and_merge("E1", {"code": "000000", "url": "dummy"}, all_teams, existing, failed)
        assert result is not None, "得点等が0件でも既存データありのケースはregression guardに掛からないはず(lineups確認用の別データなので)"
        assert result["lineups"] == old_lineups, "formationsが取れなかった回は前回のlineupsを維持するはず"
        print("OK: 出場メンバーが今回取れなくても前回分を維持することを確認")
    finally:
        fme.fetch_html = original_fetch_html


# ---------- youtube_highlights.search_dazn_highlight: タイトル絞り込み・APIキー未設定時の挙動 ----------

def test_search_dazn_highlight_title_filter():
    import requests

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    # 1件目は別カード(タイトルに両チーム名が揃わない)、2件目が本命というケースを模擬する
    payload = {
        "items": [
            {"id": {"videoId": "WRONG"}, "snippet": {"title": "【湘南ベルマーレ×他チーム｜ハイライト】J2リーグ第29節｜2026シーズン｜Jリーグ"}},
            {"id": {"videoId": "RIGHT123"}, "snippet": {"title": "【湘南ベルマーレ×FC東京｜ハイライト】J2リーグ第30節｜2026シーズン｜Jリーグ"}},
        ]
    }
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return FakeResponse(payload)

    original_get = requests.get
    requests.get = fake_get
    try:
        video_id = youtube_highlights.search_dazn_highlight("湘南ベルマーレ", "FC東京", api_key="dummy-key")
        assert video_id == "RIGHT123", video_id
        assert captured["params"]["channelId"] == youtube_highlights.DAZN_JAPAN_CHANNEL_ID
        print("OK: search_dazn_highlight()がタイトルに両チーム名を含む動画だけを採用することを確認")
    finally:
        requests.get = original_get


def test_search_dazn_highlight_no_api_key_skips_silently():
    import requests

    def fail_if_called(*args, **kwargs):
        raise AssertionError("APIキーが無いのにHTTPリクエストしてしまっている")

    original_load_key = youtube_highlights.load_api_key
    original_get = requests.get
    youtube_highlights.load_api_key = lambda: None
    requests.get = fail_if_called
    try:
        assert youtube_highlights.search_dazn_highlight("ホーム", "アウェイ") is None
        print("OK: APIキー未設定時はリクエストせずNoneを返すことを確認")
    finally:
        youtube_highlights.load_api_key = original_load_key
        requests.get = original_get


# ---------- parse_and_merge: DAZNハイライト検索のクールダウン・試行回数上限 ----------

def test_dazn_search_cooldown_and_attempts():
    fake_master = {
        "teams": [
            {"idTeam": "T_A", "en": "a", "aliases": [], "aliasesJa": [], "ja": "ホーム"},
            {"idTeam": "T_B", "en": "b", "aliases": [], "aliasesJa": [], "ja": "アウェイ"},
        ]
    }
    all_teams = load_master_teams("j2", fake_master)

    import fetch_match_events as fme

    original_fetch_html = fme.fetch_html
    original_search = fme.search_dazn_highlight
    original_load_key = fme.load_youtube_api_key
    # 得点等の目印が無いページを模擬する(このテストではDAZN検索まわりの挙動だけを見たいため)
    fme.fetch_html = lambda url: "<html>マークアップが変わって何も拾えないページ</html>"
    # このテストはAPIキーが設定済みの前提で検索ロジックを検証する(未設定時の挙動は別テストで確認)
    fme.load_youtube_api_key = lambda: "dummy-key"
    resolved_finished = {"code": "000000", "url": "dummy", "finished": True, "homeJa": "ホーム", "awayJa": "アウェイ"}
    resolved_unfinished = {"code": "000000", "url": "dummy", "finished": False, "homeJa": "ホーム", "awayJa": "アウェイ"}
    try:
        # ケース1: 試合終了済み・未検索 -> 検索が呼ばれ、見つかった動画IDが保存される
        calls = []

        def fake_search_found(home_ja, away_ja):
            calls.append((home_ja, away_ja))
            return "VIDEO123"

        fme.search_dazn_highlight = fake_search_found
        result = parse_and_merge("D1", resolved_finished, all_teams, {}, [])
        assert result is not None
        assert result["daznVideoId"] == "VIDEO123", result
        assert result["daznSearchAttempts"] == 1
        assert calls == [("ホーム", "アウェイ")]
        print("OK: 試合終了済み・未検索の試合ではDAZN検索が呼ばれ、見つかった動画IDが保存されることを確認")

        # ケース2: 既にdaznVideoIdがある -> 再検索しない
        def fail_if_called(home_ja, away_ja):
            raise AssertionError("既にdaznVideoIdがあるのに再検索してしまっている")

        fme.search_dazn_highlight = fail_if_called
        existing2 = {"D2": {"goals": [], "cards": [], "subs": [], "daznVideoId": "OLD_ID",
                             "daznSearchAttempts": 1, "daznLastSearchedAtJst": "2026-08-01T00:00:00+09:00"}}
        result2 = parse_and_merge("D2", resolved_finished, all_teams, existing2, [])
        assert result2["daznVideoId"] == "OLD_ID"
        assert result2["daznSearchAttempts"] == 1
        print("OK: 既にDAZN動画が見つかっている試合では再検索しないことを確認")

        # ケース3: 試行回数が上限に達している -> 再検索しない
        existing3 = {"D3": {"goals": [], "cards": [], "subs": [], "daznVideoId": None,
                             "daznSearchAttempts": 3, "daznLastSearchedAtJst": "2020-01-01T00:00:00+09:00"}}
        result3 = parse_and_merge("D3", resolved_finished, all_teams, existing3, [])
        assert result3["daznVideoId"] is None
        assert result3["daznSearchAttempts"] == 3
        print("OK: 試行回数が上限(DAZN_SEARCH_MAX_ATTEMPTS)に達した試合では再検索しないことを確認")

        # ケース4: クールダウン中(直近に検索済み) -> 再検索しない
        recent = datetime.now(JST).isoformat(timespec="seconds")
        existing4 = {"D4": {"goals": [], "cards": [], "subs": [], "daznVideoId": None,
                             "daznSearchAttempts": 1, "daznLastSearchedAtJst": recent}}
        result4 = parse_and_merge("D4", resolved_finished, all_teams, existing4, [])
        assert result4["daznVideoId"] is None
        assert result4["daznSearchAttempts"] == 1, "クールダウン中は試行回数を増やさないはず"
        print("OK: クールダウン中の試合では再検索しないことを確認")

        # ケース5: 試合が終わっていない -> 検索しない
        fme.search_dazn_highlight = fail_if_called
        result5 = parse_and_merge("D5", resolved_unfinished, all_teams, {}, [])
        assert result5["daznVideoId"] is None
        assert result5["daznSearchAttempts"] == 0
        print("OK: 試合終了前はDAZN検索をしないことを確認")

        # ケース6: 検索したが見つからなかった -> 試行回数だけ増える
        fme.search_dazn_highlight = lambda home_ja, away_ja: None
        result6 = parse_and_merge("D6", resolved_finished, all_teams, {}, [])
        assert result6["daznVideoId"] is None
        assert result6["daznSearchAttempts"] == 1
        assert result6["daznLastSearchedAtJst"] is not None
        print("OK: 検索して見つからなかった場合は試行回数だけ増えて次回また試せることを確認")

        # ケース7: APIキー未設定 -> 検索しない・試行回数もクールダウンも消費しない
        # (キー設定前に無駄撃ちした「なし」判定のせいで、キー設定後もクールダウン待ちに
        #  なってしまう問題を防ぐための挙動)
        fme.load_youtube_api_key = lambda: None
        fme.search_dazn_highlight = fail_if_called
        result7 = parse_and_merge("D7", resolved_finished, all_teams, {}, [])
        assert result7["daznVideoId"] is None
        assert result7["daznSearchAttempts"] == 0, "APIキー未設定時は試行回数を消費しないはず"
        assert result7["daznLastSearchedAtJst"] is None
        print("OK: APIキー未設定の間は試行回数・クールダウンを消費しないことを確認")
    finally:
        fme.fetch_html = original_fetch_html
        fme.search_dazn_highlight = original_search
        fme.load_youtube_api_key = original_load_key


def test_parsers_against_samples():
    livetxt = SAMPLE_DIR / "sample_match_livetxt.html"
    review = SAMPLE_DIR / "sample_match_review.html"
    schedule = SAMPLE_DIR / "sample_match_schedule.html"
    if not (livetxt.exists() and review.exists() and schedule.exists()):
        print("SKIP: data/tmp/sample_match_*.html が無いためサンプル依存テストをスキップ"
              "(python scripts/save_sample_html.py で生成できます)")
        return

    from fetch_official import extract_next_chunks

    chunks = extract_next_chunks(livetxt.read_text(encoding="utf-8"))
    goals, cards, subs = find_goals(chunks), find_cards(chunks), find_subs(chunks)
    assert len(goals) == 5, f"livetxt: 得点5件のはずが{len(goals)}件"
    assert len(cards) == 3, f"livetxt: カード3件のはずが{len(cards)}件"
    assert len(subs) == 10, f"livetxt: 交代10件のはずが{len(subs)}件"
    print("OK: sample_match_livetxt.html の得点/カード/交代の件数を確認")

    chunks_r = extract_next_chunks(review.read_text(encoding="utf-8"))
    goals_r = find_goals(chunks_r)
    assert len(goals_r) == 5, f"review: 得点5件のはずが{len(goals_r)}件"
    print("OK: sample_match_review.html の得点件数を確認")

    video_id = find_highlight_video_id(chunks_r)
    assert video_id == "FsBrYXiQ7dU", f"review: ハイライト動画IDが想定と違う: {video_id}"
    assert find_highlight_video_id(chunks) is None, "livetxt: ハイライト動画は無いはずなのに見つかった"
    print("OK: sample_match_review.html からハイライト動画ID(YouTube)を確認")

    for label, chunks_x in [("livetxt", chunks), ("review", chunks_r)]:
        formations = find_formations(chunks_x)
        assert formations and len(formations) == 1, f"{label}: formationsが1件取れるはず"
        home, away = formations[0]["homeTeam"], formations[0]["awayTeam"]
        assert len(home["players"]) == 11 and len(away["players"]) == 11, f"{label}: 出場メンバーは11人ずつのはず"
        assert home.get("formation") and away.get("formation"), f"{label}: フォーメーション文字列が空"
    print("OK: 出場メンバー(スタメン11人・フォーメーション)をlivetxt/review両方で確認")

    chunks_s = extract_next_chunks(schedule.read_text(encoding="utf-8"))
    entries = extract_schedule_index(chunks_s)
    assert len(entries) == 10, f"schedule: 10試合のはずが{len(entries)}件"
    codes = {e["code"]: (e["home"], e["away"]) for e in entries}
    assert codes.get("082217") == ("テゲバジャーロ宮崎", "湘南ベルマーレ"), codes.get("082217")
    print("OK: sample_match_schedule.html の対戦カード<->コード対応を確認")

    # 第14弾: 控えメンバー(基点ページ側のみに埋め込まれている)
    base = SAMPLE_DIR / "sample_match_base.html"
    if not base.exists():
        print("SKIP: data/tmp/sample_match_base.html が無いため控えメンバーのテストをスキップ"
              "(python scripts/save_sample_html.py で生成できます)")
        return
    chunks_b = extract_next_chunks(base.read_text(encoding="utf-8"))
    members = find_lineup_members(chunks_b)
    assert set(members.keys()) == {"sapporo", "omiya"}, f"teamNameKeyが想定外: {members.keys()}"
    for slug in ("sapporo", "omiya"):
        players = members[slug]
        assert len(players) == 20, f"{slug}: スタメン11+控え9=20人のはずが{len(players)}人"
        assert all(p["id"] and p["name"] and p["position"] and p["number"] for p in players), \
            f"{slug}: id/name/position/numberが揃っていない選手がいる"
    print("OK: sample_match_base.html からスタメン+控えメンバー(各20人)を確認")

    # build_lineups()でformations(スタメン)とbench_by_slug(控え)を統合できることを確認する
    # (実マスタが必要なので、data/masters/が揃っている実環境でだけ意味のあるテスト)
    formations = find_formations(chunks)
    all_teams = load_all_teams()
    lineups = build_lineups(formations, members, all_teams)
    assert lineups is not None, "build_lineups()がNoneを返した"
    for side in ("home", "away"):
        s = lineups[side]
        assert s["idTeam"], f"{side}: idTeamが解決できていない(masters側のjaと一致しない?)"
        assert len(s["players"]) == 11, f"{side}: スタメン11人のはずが{len(s['players'])}人"
        assert len(s["bench"]) == 9, f"{side}: 控え9人のはずが{len(s['bench'])}人"
        starter_ids = {p["id"] for p in s["players"]}
        bench_ids = {p["id"] for p in s["bench"]}
        assert not (starter_ids & bench_ids), f"{side}: スタメンと控えでidが重複している"
    print("OK: build_lineups()でスタメン+控えメンバーを統合できることを確認")


if __name__ == "__main__":
    test_pick_candidates_window()
    test_parse_and_merge_regression_guard()
    test_lineups_carry_forward_when_missing()
    test_search_dazn_highlight_title_filter()
    test_search_dazn_highlight_no_api_key_skips_silently()
    test_dazn_search_cooldown_and_attempts()
    test_parsers_against_samples()
    print("\n全テスト完了")
