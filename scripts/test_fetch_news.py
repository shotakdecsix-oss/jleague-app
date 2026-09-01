"""
fetch_news.py の検証。ネットワーク不要(fetch_query_fn/fetch_feed_fnを差し替えてオフラインでテストする)。

実行方法:
    python scripts/test_fetch_news.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_news import (  # noqa: E402
    accumulate_items,
    build_news,
    build_news_queries,
    classify_feed_items,
    dedupe_news_items,
    merge_and_dedupe,
    merge_with_existing,
    normalize_url,
    parse_pubdate_to_jst,
    parse_soccerdigest_entries,
)
from team_matching import match_teams_in_text  # noqa: E402

# team_matching.load_master_teams() が返す共通形式(league/idTeam/en/aliases/aliasesJa/ja)を模した
# 小さなマスタ。fix-news-volume.md の必須テストケース(鹿島の短縮名、ベルマーレ+山形の複数一致、
# C大阪/FC大阪の部分一致衝突)をカバーできるクラブを含めてある。
ALL_TEAMS = [
    {"league": "j1", "idTeam": "137714", "en": "Kashima Antlers", "aliases": ["Antlers"], "aliasesJa": ["アントラーズ", "鹿島"], "ja": "鹿島アントラーズ"},
    {"league": "j2", "idTeam": "137715", "en": "Shonan Bellmare", "aliases": ["Shonan"], "aliasesJa": ["ベルマーレ", "湘南"], "ja": "湘南ベルマーレ"},
    {"league": "j2", "idTeam": "137720", "en": "Montedio Yamagata", "aliases": ["Yamagata"], "aliasesJa": ["モンテディオ", "山形"], "ja": "モンテディオ山形"},
    {"league": "j1", "idTeam": "137706", "en": "Consadole Sapporo", "aliases": ["Sapporo", "Consadole"], "aliasesJa": ["コンサドーレ"], "ja": "北海道コンサドーレ札幌"},
    {"league": "j1", "idTeam": "999901", "en": "Kashiwa Reysol", "aliases": ["Reysol"], "aliasesJa": ["レイソル"], "ja": "柏レイソル"},
    {"league": "j1", "idTeam": "137701", "en": "Cerezo Osaka", "aliases": ["Cerezo"], "aliasesJa": ["セレッソ大阪", "C大阪"], "ja": "セレッソ大阪"},
    {"league": "j3", "idTeam": "999902", "en": "FC Osaka", "aliases": [], "aliasesJa": [], "ja": "FC大阪"},
]


def fake_fetch_ok(query: str) -> list[dict]:
    return [{"title": f"{query}のニュース", "link": "https://example.com/1", "publishedJst": "2026-08-10T21:00:00+09:00", "source": "テスト新聞"}]


def fake_fetch_empty(_url: str) -> list[dict]:
    return []


def test_empty_ob_players_does_not_error() -> None:
    """watchlist.jsonのobPlayersが空でもエラーにならないこと。"""
    watchlist = {"teams": ["137715"], "obPlayers": []}
    out = build_news(watchlist, ALL_TEAMS, fetch_query_fn=fake_fetch_ok, fetch_feed_fn=fake_fetch_empty, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    assert out["obPlayers"] == {}
    # 湘南: ja「湘南ベルマーレ」+ aliasesJaのうちカタカナ愛称「ベルマーレ」の2クエリ
    # (「湘南」は2文字の非カタカナ語なのでクエリ対象から除外される)
    assert out["meta"]["queryCount"] == 2
    assert out["meta"]["failed"] == []
    assert "137715" in out["teams"]
    print("OK: obPlayersが空配列でもエラーにならない")


def test_build_news_queries_filters_short_kanji_aliases() -> None:
    """
    クエリ語はja(常に採用)+aliasesJaのうち、カタカナのみの愛称か3文字以上の語だけに絞ること。
    「鹿島」のような2文字の漢字語は無関係な検索結果を拾いやすいので除外する。
    """
    team = {"ja": "鹿島アントラーズ", "aliasesJa": ["アントラーズ", "鹿島"]}
    queries = build_news_queries(team)
    assert queries == ["鹿島アントラーズ", "アントラーズ"], queries
    print("OK: 2文字の漢字語(鹿島)はクエリから除外され、カタカナ愛称(アントラーズ)は採用される")


def test_google_query_uses_ja_and_aliasesja() -> None:
    """watchlist記載クラブは、ja名+aliasesJa(フィルタ後)を個別クエリとして投げること。"""
    seen_queries: list[str] = []

    def capturing_fetch(query: str) -> list[dict]:
        seen_queries.append(query)
        return [{"title": f"{query}記事", "link": f"https://example.com/{query}", "publishedJst": "2026-08-10T21:00:00+09:00", "source": None}]

    watchlist = {"teams": ["137706"], "obPlayers": []}  # 札幌: aliasesJa=["コンサドーレ"]
    out = build_news(watchlist, ALL_TEAMS, fetch_query_fn=capturing_fetch, fetch_feed_fn=fake_fetch_empty, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    assert seen_queries == ["北海道コンサドーレ札幌", "コンサドーレ"], seen_queries
    assert out["meta"]["queryCount"] == 2
    assert len(out["teams"]["137706"]) == 2
    assert all(it["sourceType"] == "google" for it in out["teams"]["137706"])
    print("OK: watchlist記載クラブはja名+aliasesJa(フィルタ後)の個別クエリになる")


def test_empty_watchlist_teams_queries_all_clubs() -> None:
    """teamsが空配列の場合、全クラブがGoogle Newsクエリの対象になること。"""
    seen_ids: list[str] = []

    def capturing_fetch(query: str) -> list[dict]:
        return [{"title": f"{query}記事", "link": f"https://example.com/{query}", "publishedJst": "2026-08-10T21:00:00+09:00", "source": None}]

    watchlist = {"teams": [], "obPlayers": []}
    out = build_news(watchlist, ALL_TEAMS, fetch_query_fn=capturing_fetch, fetch_feed_fn=fake_fetch_empty, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    assert set(out["teams"].keys()) == {t["idTeam"] for t in ALL_TEAMS}, out["teams"].keys()
    print("OK: watchlist.teamsが空なら全クラブがGoogle Newsクエリの対象になる")


def test_unknown_id_team_is_skipped_with_warning() -> None:
    """存在しないidTeamを書いたら、そのエントリを警告付きでスキップして続行すること。"""
    watchlist = {"teams": ["137715", "999999"], "obPlayers": []}
    logs: list[str] = []
    out = build_news(
        watchlist, ALL_TEAMS, fetch_query_fn=fake_fetch_ok, fetch_feed_fn=fake_fetch_empty, sleep_fn=lambda s: None,
        log=lambda *a, **k: logs.append(" ".join(str(x) for x in a)),
    )
    assert "137715" in out["teams"]
    assert "999999" not in out["teams"]
    assert any("999999" in line and "見つからない" in line for line in logs), "警告ログが出るはず"
    print("OK: 存在しないidTeamは警告付きでスキップし、後続は続行する")


def test_pubdate_gmt_is_converted_to_jst() -> None:
    """pubDateがJSTに変換されていること(GMT表記の実例)。"""
    jst_iso = parse_pubdate_to_jst("Mon, 10 Aug 2026 12:00:00 GMT")
    assert jst_iso == "2026-08-10T21:00:00+09:00", f"GMT12:00 は JST21:00 のはず: {jst_iso}"

    jst_iso2 = parse_pubdate_to_jst("Mon, 10 Aug 2026 12:00:00 +0000")
    assert jst_iso2 == "2026-08-10T21:00:00+09:00"

    assert parse_pubdate_to_jst("not a date") is None
    print("OK: pubDate(GMT表記含む)がJSTのISO8601に変換される。壊れた入力はNone")


def test_extra_query_is_appended_for_ob_players() -> None:
    """
    OB選手はname(+extraQuery)で検索クエリが組まれること。
    all_teamsを空にして、クラブ側のクエリ(teams=[]がデフォルトで全クラブ対象になる仕様)が
    OB選手側のクエリと混ざらないようにしてある。
    """
    seen_queries: list[str] = []

    def capturing_fetch(query: str) -> list[dict]:
        seen_queries.append(query)
        return []

    watchlist = {
        "teams": [],
        "obPlayers": [
            {"name": "山田太郎", "extraQuery": "サッカー", "enabled": True},
            {"name": "無効選手", "extraQuery": "サッカー", "enabled": False},
            {"name": "鈴木次郎", "enabled": True},
        ],
    }
    out = build_news(watchlist, [], fetch_query_fn=capturing_fetch, fetch_feed_fn=fake_fetch_empty, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    assert seen_queries == ["山田太郎 サッカー", "鈴木次郎"], f"クエリの組み方が違う: {seen_queries}"
    assert "山田太郎" in out["obPlayers"]
    assert "鈴木次郎" in out["obPlayers"]
    assert "無効選手" not in out["obPlayers"], "enabled=falseはフェッチされないはず"
    print("OK: OB選手のクエリはname(+extraQuery)、enabled=falseはスキップされる")


def test_fetch_failure_skips_only_that_query() -> None:
    """
    取得失敗(HTTPエラー・XMLパース失敗)は、そのクエリ/フィードだけスキップして続行すること。
    1クラブにつきja名+aliasesJaの複数クエリが飛ぶので、1つが失敗しても他のクエリの結果は残る。
    """
    def flaky_fetch(query: str) -> list[dict]:
        if "壊れる" in query:
            raise RuntimeError("simulated failure")
        return [{"title": "ok", "link": "https://example.com", "publishedJst": None, "source": None}]

    watchlist = {"teams": ["999901"], "obPlayers": []}
    broken_teams = [{**t, "ja": "壊れるクラブ"} if t["idTeam"] == "999901" else t for t in ALL_TEAMS]
    out = build_news(watchlist, broken_teams, fetch_query_fn=flaky_fetch, fetch_feed_fn=fake_fetch_empty, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    # ja名クエリ(「壊れるクラブ」)は失敗するが、aliasesJaクエリ(「レイソル」)は成功するので結果には残る
    assert "999901" in out["teams"], "一部のクエリが成功していれば結果に残るはず"
    assert any("999901" in f and "壊れるクラブ" in f for f in out["meta"]["failed"]), out["meta"]["failed"]
    print("OK: 一部クエリの失敗は他クエリの結果を巻き込まず、失敗理由もログに残る")


def test_feed_items_classified_by_team_name() -> None:
    """
    リーグ全体フィードの記事は、タイトル+概要にクラブ名(ja/aliasesJa)が含まれるかで振り分けられること。
    """
    items = [
        {"title": "湘南ベルマーレ、新加入選手を発表", "link": "https://feed.example.com/1", "publishedJst": "2026-08-10T10:00:00+09:00", "description": ""},
        {"title": "無関係な記事", "link": "https://feed.example.com/2", "publishedJst": "2026-08-10T09:00:00+09:00", "description": "サッカーとは関係ない話題"},
    ]
    classified, unmatched = classify_feed_items(items, ALL_TEAMS, "テスト媒体")
    assert "137715" in classified, "「湘南ベルマーレ」はjaと完全一致するので拾えるはず"
    assert len(classified["137715"]) == 1
    assert classified["137715"][0]["source"] == "テスト媒体"
    assert classified["137715"][0]["sourceType"] == "feed"
    assert sum(len(v) for v in classified.values()) == 1, "無関係な記事はどのクラブにもヒットしないはず"
    # 第33弾: 当たらなかった記事は捨てずに返す
    assert len(unmatched) == len(items) - 1, unmatched
    assert all(u["sourceType"] == "feed" and u["source"] == "テスト媒体" for u in unmatched)
    print("OK: フィード記事はタイトルのクラブ名(ja/aliasesJa)でクラブに振り分けられ、"
          "当たらなかった記事はunmatchedとして返る")


def test_feed_item_can_match_multiple_teams() -> None:
    """1本の記事が複数クラブにヒットする場合、両方に入れてよい(移籍記事など)。"""
    items = [
        {"title": "柏レイソルから北海道コンサドーレ札幌へ、FW〇〇が完全移籍", "link": "https://feed.example.com/3", "publishedJst": "2026-08-10T10:00:00+09:00", "description": ""},
    ]
    classified, unmatched = classify_feed_items(items, ALL_TEAMS, "テスト媒体")
    assert "999901" in classified and "137706" in classified, classified
    assert unmatched == [], "クラブに当たった記事はunmatchedに入らないはず"
    print("OK: 複数クラブがタイトルに含まれる記事は両方に振り分けられる")


def test_merge_with_existing_accumulates_world() -> None:
    """第33弾: world(クラブに紐づかない記事)も teams と同じように蓄積・重複除去・掃除される。"""
    cutoff = "2026-08-01T00:00:00+09:00"
    existing = {"teams": {}, "obPlayers": {}, "world": [
        {"title": "古い海外記事", "link": "https://x.example.com/old", "publishedJst": "2026-07-01T00:00:00+09:00"},
        {"title": "残る海外記事", "link": "https://x.example.com/keep", "publishedJst": "2026-08-20T00:00:00+09:00"},
    ]}
    fresh = {"meta": {}, "teams": {}, "obPlayers": {}, "world": [
        {"title": "残る海外記事", "link": "https://x.example.com/keep", "publishedJst": "2026-08-20T00:00:00+09:00"},
        {"title": "新しい海外記事", "link": "https://x.example.com/new", "publishedJst": "2026-08-31T00:00:00+09:00"},
    ]}
    out = merge_with_existing(existing, fresh, cutoff)
    titles = [w["title"] for w in out["world"]]
    assert titles == ["新しい海外記事", "残る海外記事"], titles   # 新しい順
    assert "古い海外記事" not in titles, "cutoffより古い記事は掃除されるはず"
    assert out["meta"]["newItems"] == 1, out["meta"]      # 重複した1件は新規に数えない
    assert out["meta"]["totalItems"] == 2, out["meta"]
    print("OK: worldもcutoff掃除・重複除去・新着カウントの対象になる")


def test_overseas_feed_items_go_to_world() -> None:
    """第33弾: Jクラブ名が入っていない海外記事は捨てずにunmatchedへ回す。

    これが無いと「クラブから海外移籍した選手の動向」が一切集まらない
    (サッカーキングのフィードは大半がこの形)。
    """
    items = [
        {"title": "菅原由勢、カリアリへレンタル移籍の可能性浮上　買取OP付きでセリエA挑戦か",
         "link": "https://feed.example.com/w1", "publishedJst": "2026-08-31T10:00:00+09:00", "description": ""},
        {"title": "湘南ベルマーレ、次節のメンバーを発表",
         "link": "https://feed.example.com/w2", "publishedJst": "2026-08-31T11:00:00+09:00", "description": ""},
    ]
    classified, unmatched = classify_feed_items(items, ALL_TEAMS, "サッカーキング")
    assert [u["title"] for u in unmatched] == [items[0]["title"]], unmatched
    assert list(classified) == ["137715"], classified
    print("OK: Jクラブ名の無い海外記事はunmatched(world行き)、クラブ名のある記事はクラブ別に入る")


# 第14弾: サッカーダイジェストWeb(soccerdigestweb.com)の記事一覧ページ(クラブ別タグページ)。
# 2026-08-22にブラウザで実際の湘南ベルマーレのページ(tag_id=142)を開いて確認した
# 実物のマークアップを基にした最小サンプル(2記事ぶん + タイトルにHTMLエンティティが
# 含まれるケース + 画像/日付が欠けているケースを1件足してある)。
_SOCCERDIGEST_SAMPLE_HTML = """
<div class="news_list">
<h2 class="title">
    湘南 新着記事</h2>

<div class="entry">
\t<div class="pic">
\t\t<a href="https://www.soccerdigestweb.com/news/detail/id=196005"><img src="https://soccerdigestweb.thedigestweb.com/v=1787201947/files/topics/196005_ext_03_0.jpg" width="105" height="105" alt=""></a>
\t</div>
\t<div class="text">
\t\t\t<p class="entry_date">
\t\t\t\t\t<span class="author">金子 徹（サッカーダイジェスト編集部）</span>
\t\t\t\t\t\t\t<span class="date">2026年08月20日</span></p>
\t\t\t<p class="title"><a href="https://www.soccerdigestweb.com/news/detail/id=196005">新布陣導入の湘南でDF松村晟怜が存在感。４－４－２の左SBに手応え「やることも、やれることも増えて、幅が広がっている」</a></p>
\t\t\t<p class="read">
\t\t\t\t　４－４－２の新布陣で開幕２連勝を飾った湘南ベルマーレにおいて、存在感を高めているDFがいる。
\t\t\t\t<span><a href="https://www.soccerdigestweb.com/news/detail/id=196005">続きを読む</a></span></p>
\t</div>
</div>
<div class="entry">
\t<div class="pic">
\t\t<a href="https://www.soccerdigestweb.com/news/detail/id=195964"><img src="https://soccerdigestweb.thedigestweb.com/v=x/files/topics/195964.jpg" width="105" height="105" alt=""></a>
\t</div>
\t<div class="text">
\t\t\t<p class="entry_date">
\t\t\t\t\t<span class="author">金子 徹</span>
\t\t\t\t\t\t\t<span class="date">2026年08月19日</span></p>
\t\t\t<p class="title"><a href="https://www.soccerdigestweb.com/news/detail/id=195964">&quot;新スタイル&quot;で開幕２連勝の湘南。長澤徹監督が語った４バックの狙い</a></p>
\t\t\t<p class="read">summary</p>
\t</div>
</div>
<div class="entry">
\t<div class="text">
\t\t\t<p class="title"><a href="https://www.soccerdigestweb.com/news/detail/id=999999">日付・画像が無い記事</a></p>
\t</div>
</div>
"""


def test_parse_soccerdigest_entries_extracts_title_link_date_image() -> None:
    """実物のマークアップから title/link/publishedJst/imageUrl を正しく抜き出せること。"""
    items = parse_soccerdigest_entries(_SOCCERDIGEST_SAMPLE_HTML)
    assert len(items) == 3, items

    first = items[0]
    assert first["link"] == "https://www.soccerdigestweb.com/news/detail/id=196005"
    assert first["title"].startswith("新布陣導入の湘南でDF松村晟怜が存在感")
    assert first["publishedJst"] == "2026-08-20T00:00:00+09:00"
    assert first["source"] == "サッカーダイジェストWeb"
    assert first["sourceType"] == "soccerdigest"
    assert first["imageUrl"].endswith("196005_ext_03_0.jpg")

    second = items[1]
    assert second["title"] == '"新スタイル"で開幕２連勝の湘南。長澤徹監督が語った４バックの狙い', second["title"]
    print("OK: サッカーダイジェストWebの記事一覧からtitle/link/日付/画像URLを抽出できる(HTMLエンティティも復元)")


def test_parse_soccerdigest_entries_tolerates_missing_date_and_image() -> None:
    """日付・画像が無い記事でも、タイトルとリンクさえあれば例外を投げず拾うこと。"""
    items = parse_soccerdigest_entries(_SOCCERDIGEST_SAMPLE_HTML)
    third = items[2]
    assert third["title"] == "日付・画像が無い記事"
    assert third["publishedJst"] is None
    assert "imageUrl" not in third
    print("OK: 日付・画像が欠けた記事も例外を投げずpublishedJst=None/imageUrlキー無しで拾う")


def test_parse_soccerdigest_entries_returns_empty_on_unrecognized_markup() -> None:
    """マークアップが想定と全く違う(.entryが無い)場合は、例外を投げず空リストを返すこと。"""
    assert parse_soccerdigest_entries("<html><body>no entries here</body></html>") == []
    print("OK: 想定外のマークアップでは例外を投げず空リストを返す")


def test_build_news_includes_soccerdigest_source() -> None:
    """soccerdigest_tagsに書かれたクラブは、fetch_soccerdigest_fnで取得しsourceType='soccerdigest'で入ること。"""
    calls: list[str] = []

    def fake_soccerdigest(tag_id: str) -> list[dict]:
        calls.append(tag_id)
        return [{"title": "湘南の記事", "link": "https://soccerdigestweb.example.com/1",
                  "publishedJst": "2026-08-20T00:00:00+09:00", "source": "サッカーダイジェストWeb",
                  "sourceType": "soccerdigest"}]

    watchlist = {"teams": [], "obPlayers": []}
    out = build_news(
        watchlist, ALL_TEAMS, soccerdigest_tags={"137715": "142"},
        fetch_query_fn=fake_fetch_empty, fetch_feed_fn=fake_fetch_empty,
        fetch_soccerdigest_fn=fake_soccerdigest, sleep_fn=lambda s: None, log=lambda *a, **k: None,
    )
    assert calls == ["142"], calls
    items = out["teams"]["137715"]
    assert any(it["sourceType"] == "soccerdigest" for it in items), items
    print("OK: soccerdigest_tagsに書かれたクラブはfetch_soccerdigest_fnで取得されsourceType='soccerdigest'で入る")


def test_build_news_soccerdigest_failure_does_not_break_other_sources() -> None:
    """サッカーダイジェストWebの取得が失敗しても、他ソース(Google News等)の結果は無事なこと。"""
    def failing_soccerdigest(_tag_id: str) -> list[dict]:
        raise RuntimeError("simulated failure")

    watchlist = {"teams": ["137715"], "obPlayers": []}
    logs: list[str] = []
    out = build_news(
        watchlist, ALL_TEAMS, soccerdigest_tags={"137715": "142"},
        fetch_query_fn=fake_fetch_ok, fetch_feed_fn=fake_fetch_empty,
        fetch_soccerdigest_fn=failing_soccerdigest, sleep_fn=lambda s: None,
        log=lambda *a, **k: logs.append(" ".join(str(x) for x in a)),
    )
    assert "137715" in out["teams"], "Google News側の結果は残るはず"
    assert all(it["sourceType"] != "soccerdigest" for it in out["teams"]["137715"])
    assert any("soccerdigest:137715" in f for f in out["meta"]["failed"]), out["meta"]["failed"]
    assert any("サッカーダイジェストWeb" in line for line in logs), "失敗ログが出るはず"
    print("OK: サッカーダイジェストWebの取得失敗は他ソースを巻き込まず、失敗理由もログに残る")


def test_match_teams_in_text_ignores_terms_not_in_ja_or_aliasesja() -> None:
    """ja/aliasesJaのどちらにも登録していない短縮名では一致しないこと。"""
    matched = match_teams_in_text("札幌で開催されたイベント", ALL_TEAMS)
    assert matched == [], f"aliasesJaに無い短縮名では一致しないはず: {matched}"

    matched2 = match_teams_in_text("北海道コンサドーレ札幌が新監督を発表", ALL_TEAMS)
    assert len(matched2) == 1 and matched2[0]["idTeam"] == "137706"
    print("OK: aliasesJaに無い語では一致せず、ja(正式名称)なら一致する")


def test_match_teams_in_text_kashima_short_form() -> None:
    """fix-news-volume.md必須ケース: 【鹿島】のような短縮名(aliasesJa)でも一致すること。"""
    matched = match_teams_in_text("DF濃野がD.C.ユナイテッドへ完全移籍【鹿島】", ALL_TEAMS)
    assert [t["ja"] for t in matched] == ["鹿島アントラーズ"], matched
    print("OK: 「鹿島」のような短縮名でも鹿島アントラーズに一致する")


def test_match_teams_in_text_multiple_short_forms() -> None:
    """fix-news-volume.md必須ケース: 複数クラブの短縮名が1文に含まれる場合、両方に一致すること。"""
    matched = match_teams_in_text("ベルマーレが山形に1-0で勝利、第2節", ALL_TEAMS)
    names = {t["ja"] for t in matched}
    assert names == {"湘南ベルマーレ", "モンテディオ山形"}, matched
    print("OK: 「ベルマーレ」「山形」から湘南ベルマーレ・モンテディオ山形の両方に一致する")


def test_match_teams_in_text_fc_osaka_alone_does_not_match_cerezo() -> None:
    """fix-news-volume.md必須ケース: 「FC大阪が勝利」はFC大阪のみに一致し、セレッソ大阪には入らないこと。"""
    matched = match_teams_in_text("FC大阪が勝利", ALL_TEAMS)
    assert [t["ja"] for t in matched] == ["FC大阪"], matched
    print("OK: 「FC大阪が勝利」はFC大阪のみに一致する(セレッソ大阪に誤爆しない)")


def test_match_teams_in_text_c_osaka_and_fc_osaka_both_match() -> None:
    """fix-news-volume.md必須ケース: 「C大阪とFC大阪が対戦」は両方に一致すること(長い語から消費)。"""
    matched = match_teams_in_text("C大阪とFC大阪が対戦", ALL_TEAMS)
    names = {t["ja"] for t in matched}
    assert names == {"セレッソ大阪", "FC大阪"}, matched
    print("OK: 「C大阪とFC大阪が対戦」はセレッソ大阪・FC大阪の両方に一致する")


def test_normalize_url_strips_utm_params() -> None:
    """utm_*等のトラッキングパラメータを除いて正規化すること。"""
    a = normalize_url("https://example.com/news/1?utm_source=twitter&utm_medium=social")
    b = normalize_url("https://example.com/news/1")
    assert a == b, (a, b)
    print("OK: URL正規化でutm_*等のトラッキングパラメータが除かれる")


def test_dedupe_prefers_higher_priority_source() -> None:
    """同一記事(URL一致)が複数ソースにあれば、優先順位(ゲキサカ→サッカーキング→Google News)の高い方を残す。"""
    items = [
        {"title": "A", "link": "https://x.example.com/1", "publishedJst": "2026-08-10T10:00:00+09:00", "source": "Google News", "sourceType": "google"},
        {"title": "A", "link": "https://x.example.com/1?utm_source=rss", "publishedJst": "2026-08-10T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"},
        {"title": "A", "link": "https://x.example.com/1", "publishedJst": "2026-08-10T10:00:00+09:00", "source": "サッカーキング", "sourceType": "feed"},
    ]
    out = merge_and_dedupe(items)
    assert len(out) == 1
    assert out[0]["source"] == "ゲキサカ", out
    print("OK: 重複記事は優先順位(公式>ゲキサカ>サッカーキング>Google News)の高い方が残る")


def test_dedupe_by_exact_title_match_different_url() -> None:
    """URLが違ってもタイトルが完全一致するものは同一記事とみなすこと。"""
    items = [
        {"title": "同じ見出し", "link": "https://a.example.com/1", "publishedJst": "2026-08-10T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"},
        {"title": "同じ見出し", "link": "https://b.example.com/2", "publishedJst": "2026-08-10T10:00:00+09:00", "source": "Google News", "sourceType": "google"},
    ]
    out = dedupe_news_items(items)
    assert len(out) == 1
    print("OK: タイトル完全一致はURLが違っても重複とみなす")


def test_dedupe_collapses_same_headline_from_different_outlets() -> None:
    """第35弾: 「見出し - 媒体名」の媒体名違いを同一記事として畳む。

    実測(2026-09-01)ではGoogle News由来の34%がこの形の重複で、クラブごとの100件枠を
    食い潰していた。ただし見出し本体が違うものまで畳んではいけない。
    """
    items = [
        {"title": "守田、坂元が初出場 サッカー - 甲府経済新聞", "link": "https://a.example/1",
         "publishedJst": "2026-08-30T10:00:00+09:00", "sourceType": "google"},
        {"title": "守田、坂元が初出場 サッカー - 水戸経済新聞", "link": "https://b.example/2",
         "publishedJst": "2026-08-30T10:00:00+09:00", "sourceType": "google"},
        {"title": "Ｊ１第4節 湘南vs仙台 - Yahoo!ニュース", "link": "https://c.example/3",
         "publishedJst": "2026-08-30T10:00:00+09:00", "sourceType": "google"},
        {"title": "Ｊ１第5節 湘南vs仙台 - Yahoo!ニュース", "link": "https://d.example/4",
         "publishedJst": "2026-08-30T10:00:00+09:00", "sourceType": "google"},
    ]
    out = dedupe_news_items(items)
    titles = [i["title"] for i in out]
    assert len(out) == 3, titles
    assert "守田、坂元が初出場 サッカー - 甲府経済新聞" in titles, "先に来た方を残すはず"
    assert "守田、坂元が初出場 サッカー - 水戸経済新聞" not in titles
    assert "Ｊ１第4節 湘南vs仙台 - Yahoo!ニュース" in titles and "Ｊ１第5節 湘南vs仙台 - Yahoo!ニュース" in titles, \
        "見出し本体が違うものは畳んではいけない"
    print("OK: 媒体名違いの同一見出しは畳み、見出し本体が違うものは残す")


def test_merge_and_dedupe_reserves_slots_for_non_google() -> None:
    """第35弾: Google News以外は件数上限で押し出されない。

    Google Newsは量で圧倒するので、素直に「新しい順に上限件数」で切ると
    公式サイトやサッカーダイジェストの記事が枠から溢れる(実測で600件中17件しか残らなかった)。
    """
    google = [{"title": f"google記事{i}", "link": f"https://g.example/{i}",
               "publishedJst": f"2026-08-31T{i:02d}:00:00+09:00", "sourceType": "google"}
              for i in range(10)]
    curated = [
        {"title": "公式の古い記事", "link": "https://o.example/1",
         "publishedJst": "2026-08-01T10:00:00+09:00", "sourceType": "official"},
        {"title": "ダイジェストの古い記事", "link": "https://s.example/1",
         "publishedJst": "2026-08-02T10:00:00+09:00", "sourceType": "soccerdigest"},
    ]
    out = merge_and_dedupe(google + curated, max_items=5)
    assert len(out) == 5, out
    types = [i["sourceType"] for i in out]
    assert "official" in types and "soccerdigest" in types, f"google以外が残るはず: {types}"
    assert types.count("google") == 3, f"残り枠だけgoogleが入るはず: {types}"
    dates = [i.get("publishedJst") for i in out]
    assert dates == sorted(dates, reverse=True), f"最終的な並びは新しい順: {dates}"
    print("OK: Google News以外に枠を確保し、残りをGoogle Newsで埋め、新しい順に並べる")


def test_merge_and_dedupe_caps_and_sorts_by_recency() -> None:
    """件数上限に絞り、publishedJstの新しい順に並ぶこと。"""
    items = [
        {"title": f"記事{i}", "link": f"https://example.com/{i}", "publishedJst": f"2026-08-{i:02d}T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"}
        for i in range(1, 5)
    ]
    out = merge_and_dedupe(items, max_items=2)
    assert len(out) == 2
    assert out[0]["title"] == "記事4" and out[1]["title"] == "記事3", out
    print("OK: 上限件数に絞り、publishedJstの新しい順に並ぶ")


def test_accumulate_items_merges_new_into_existing() -> None:
    """既存記事に新規記事を足したとき、重複せず両方残り、新規件数が正しくカウントされること。"""
    existing = [
        {"title": "既存記事", "link": "https://example.com/old", "publishedJst": "2026-08-01T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"},
    ]
    new = [
        {"title": "新規記事", "link": "https://example.com/new", "publishedJst": "2026-08-15T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"},
        {"title": "既存記事", "link": "https://example.com/old", "publishedJst": "2026-08-01T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"},  # 重複
    ]
    merged, new_count = accumulate_items(existing, new, max_items=100, cutoff_iso=None)
    assert len(merged) == 2, merged
    assert new_count == 1, new_count
    print("OK: 既存+新規のマージで重複は除かれ、新規件数だけが正しくカウントされる")


def test_accumulate_items_drops_items_older_than_cutoff() -> None:
    """cutoff_isoより古いpublishedJstの記事は捨てること。日付不明の記事は捨てない。"""
    existing = [
        {"title": "古い記事", "link": "https://example.com/old", "publishedJst": "2026-01-01T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"},
        {"title": "日付不明記事", "link": "https://example.com/nodate", "publishedJst": None, "source": "ゲキサカ", "sourceType": "feed"},
    ]
    new = [
        {"title": "新しい記事", "link": "https://example.com/new", "publishedJst": "2026-08-15T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"},
    ]
    merged, _ = accumulate_items(existing, new, max_items=100, cutoff_iso="2026-06-01T00:00:00+09:00")
    titles = {it["title"] for it in merged}
    assert titles == {"日付不明記事", "新しい記事"}, titles
    print("OK: cutoffより古い記事は捨てられ、日付不明の記事は残る")


def test_merge_with_existing_computes_total_and_new_items() -> None:
    """meta.totalItems/newItemsが、既存+新規のマージ結果を正しく反映すること。"""
    existing = {
        "teams": {
            "137715": [{"title": "既存A", "link": "https://example.com/a", "publishedJst": "2026-08-01T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"}],
        },
        "obPlayers": {},
    }
    fresh = {
        "meta": {"generatedAtJst": "2026-08-19T00:00:00+09:00", "queryCount": 1, "failed": []},
        "teams": {
            "137715": [{"title": "新規B", "link": "https://example.com/b", "publishedJst": "2026-08-18T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"}],
        },
        "obPlayers": {},
    }
    out = merge_with_existing(existing, fresh, cutoff_iso="2026-06-01T00:00:00+09:00")
    assert len(out["teams"]["137715"]) == 2, out["teams"]
    assert out["meta"]["totalItems"] == 2, out["meta"]
    assert out["meta"]["newItems"] == 1, out["meta"]
    print("OK: merge_with_existingがtotalItems/newItemsを正しく計算する")


def test_merge_with_existing_keeps_teams_with_no_fresh_hits() -> None:
    """今回ヒット0件のクラブでも、既存記事(cutoff内)は消えずに残ること。"""
    existing = {
        "teams": {
            "999901": [{"title": "過去記事", "link": "https://example.com/c", "publishedJst": "2026-08-01T10:00:00+09:00", "source": "ゲキサカ", "sourceType": "feed"}],
        },
        "obPlayers": {},
    }
    fresh = {
        "meta": {"generatedAtJst": "2026-08-19T00:00:00+09:00", "queryCount": 0, "failed": []},
        "teams": {},
        "obPlayers": {},
    }
    out = merge_with_existing(existing, fresh, cutoff_iso="2026-06-01T00:00:00+09:00")
    assert "999901" in out["teams"] and len(out["teams"]["999901"]) == 1, out["teams"]
    assert out["meta"]["newItems"] == 0
    print("OK: 今回ヒット0件のクラブでも既存記事(cutoff内)は残る")


def main() -> None:
    tests = [
        test_empty_ob_players_does_not_error,
        test_build_news_queries_filters_short_kanji_aliases,
        test_google_query_uses_ja_and_aliasesja,
        test_empty_watchlist_teams_queries_all_clubs,
        test_unknown_id_team_is_skipped_with_warning,
        test_pubdate_gmt_is_converted_to_jst,
        test_extra_query_is_appended_for_ob_players,
        test_fetch_failure_skips_only_that_query,
        test_feed_items_classified_by_team_name,
        test_feed_item_can_match_multiple_teams,
        test_overseas_feed_items_go_to_world,
        test_merge_with_existing_accumulates_world,
        test_parse_soccerdigest_entries_extracts_title_link_date_image,
        test_parse_soccerdigest_entries_tolerates_missing_date_and_image,
        test_parse_soccerdigest_entries_returns_empty_on_unrecognized_markup,
        test_build_news_includes_soccerdigest_source,
        test_build_news_soccerdigest_failure_does_not_break_other_sources,
        test_match_teams_in_text_ignores_terms_not_in_ja_or_aliasesja,
        test_match_teams_in_text_kashima_short_form,
        test_match_teams_in_text_multiple_short_forms,
        test_match_teams_in_text_fc_osaka_alone_does_not_match_cerezo,
        test_match_teams_in_text_c_osaka_and_fc_osaka_both_match,
        test_normalize_url_strips_utm_params,
        test_dedupe_prefers_higher_priority_source,
        test_dedupe_by_exact_title_match_different_url,
        test_dedupe_collapses_same_headline_from_different_outlets,
        test_merge_and_dedupe_reserves_slots_for_non_google,
        test_merge_and_dedupe_caps_and_sorts_by_recency,
        test_accumulate_items_merges_new_into_existing,
        test_accumulate_items_drops_items_older_than_cutoff,
        test_merge_with_existing_computes_total_and_new_items,
        test_merge_with_existing_keeps_teams_with_no_fresh_hits,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
