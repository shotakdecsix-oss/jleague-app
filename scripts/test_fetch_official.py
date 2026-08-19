"""
fetch_official.py の検証。ネットワーク不要(fetch_fnを差し替えてオフラインでテストする)。
実際のクラブページHTMLを模した最小限の合成HTMLで、Next.jsストリーミングペイロードの
抽出ロジックを検証する。

実行方法:
    python scripts/test_fetch_official.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_official import (  # noqa: E402
    build_club_extra,
    build_media_url,
    extract_next_chunks,
    parse_club_page,
    parse_publish_date,
    search_chunks,
)


def make_next_html(chunk_lines: list[str]) -> str:
    """
    実際のjleague.jpと同じ形の <script>self.__next_f.push([1,"..."])</script> を作る。
    複数の<script>タグに分割してpushすることで、結合処理も検証する。
    """
    body = "\n".join(chunk_lines) + "\n"
    mid = len(body) // 2
    part1, part2 = body[:mid], body[mid:]
    parts = [json.dumps(part1), json.dumps(part2)]
    return "".join(f'<script>self.__next_f.push([1,{p}])</script>' for p in parts)


def test_extract_and_search_chunks_across_split_scripts() -> None:
    """複数<script>タグに分割されたペイロードでも正しく結合・キー探索できること。"""
    html = make_next_html(
        [
            '10:["$","div",null,{"foo":"bar"}]',
            '11:{"newsList":[{"id":1,"title":"A"},{"id":2,"title":"B"}]}',
        ]
    )
    chunks = extract_next_chunks(html)
    assert chunks["10"] == '["$","div",null,{"foo":"bar"}]'
    assert "newsList" in chunks["11"]

    found = search_chunks(chunks, "newsList")
    assert len(found) == 1
    assert len(found[0]) == 2
    print("OK: 分割されたペイロードでも結合・チャンク分解・キー探索ができる")


def test_parse_publish_date() -> None:
    """公式サイトのpublishDisplayDate形式(UTC, '+00')がJSTに変換されること。"""
    jst_iso = parse_publish_date("2026-08-07 09:00:00+00")
    assert jst_iso == "2026-08-07T18:00:00+09:00", jst_iso
    assert parse_publish_date("not a date") is None
    assert parse_publish_date(None) is None
    print("OK: publishDisplayDate(UTC)がJSTのISO8601に変換される。壊れた入力はNone")


def test_build_media_url_uses_filename_not_api_path() -> None:
    """
    keyVisual.url は "/api/media/file/{filename}" というAPIパスだが、実際にページに描画される
    <img src> は "/images/media/{filename}" (data/tmp/sample_club_top.html の実物のimgタグで確認済み)。
    APIパスをそのまま使うと画像が出ないので、filenameから組み立てること。
    """
    kv = {"filename": "3-25.webp", "url": "/api/media/file/3-25.webp"}
    assert build_media_url(kv) == "https://www.jleague.jp/images/media/3-25.webp"

    # filenameが欠けていてもurlの末尾から補える
    kv2 = {"url": "/api/media/file/34402.jpg"}
    assert build_media_url(kv2) == "https://www.jleague.jp/images/media/34402.jpg"

    assert build_media_url(None) is None
    assert build_media_url({}) is None
    print("OK: keyVisualの絶対URLはfilenameから/images/media/で組み立てる(APIパスは使わない)")


def test_parse_club_page_picks_longest_news_and_valid_players() -> None:
    """
    newsListが複数箇所にヒットしても最長のものを採用し、
    playersは playerId を持つ本物の選手一覧(ランキングの3人だけの配列ではない方)を選ぶこと。
    """
    payload = {
        "121": json.dumps(
            {
                "shortNewsList": {"newsList": [{"id": 1, "title": "短い方"}]},
                "fullNewsList": {
                    "newsList": [
                        {"id": 1, "title": "N1", "publishDisplayDate": "2026-01-01 00:00:00+00"},
                        {"id": 2, "title": "N2", "publishDisplayDate": "2026-01-02 00:00:00+00"},
                    ]
                },
                "rankingPlayers": {"players": [{"playerId": "999", "rank": 1}]},  # playerIdはあるが3人未満のダミー
                "clubStatsInLeague": {
                    "2026-6": {
                        "scorePg": {"label": "1試合平均得点数", "rawValue": 1, "rank": 7},
                    }
                },
                "playerStatsRanking": {
                    "2026-6": {
                        "clubName": "テストFC",
                        "score": {"label": "ゴール", "players": [{"rank": 1, "playerId": "1", "name": "A", "statsValue": 5}]},
                    }
                },
                "seasonalPerformances_group1": {
                    "gameKindIds": [2],
                    "seasonalPerformances": [
                        {"year": "2025", "result": {"leagueName": "Ｊ２", "resultLabel": "3位"}},
                    ],
                },
                "seasonalPerformances_group2": {
                    "gameKindIds": [4],
                    "seasonalPerformances": [
                        {"year": "2025", "result": {"leagueName": None, "resultLabel": "ベスト8"}},
                    ],
                },
            }
        ),
        "126": json.dumps(
            {
                "players": [
                    {"playerId": "1", "playerName": "選手A", "uniformNo": "10", "position": "FW"},
                    {"playerId": "2", "playerName": "選手B", "uniformNo": "11", "position": "MF"},
                ]
            }
        ),
    }
    html = make_next_html([f'{k}:{v}' for k, v in payload.items()])
    result = parse_club_page(html)

    assert len(result["news"]) == 2, "3人ランキングではなく、より長いnewsListを採用するはず"
    assert result["news"][0]["title"] == "N1"
    assert result["news"][0]["publishedJst"] == "2026-01-01T09:00:00+09:00"

    assert len(result["players"]) == 2, "playerIdを持つダミー1件ではなく、本物の選手一覧(2件)を採用するはず"
    assert result["players"][0]["name"] == "選手A"

    assert result["clubStats"]["seasonKey"] == "2026-6"
    assert result["clubStats"]["items"][0]["value"] == 1

    assert result["leaders"]["score"]["players"][0]["name"] == "A"

    # 「n位」表記のみ残り、カップ戦の「ベスト8」は除外されること
    assert len(result["seasonalPerformances"]) == 1
    assert result["seasonalPerformances"][0]["resultLabel"] == "3位"
    print("OK: 複数候補から最長のnewsList・本物のplayer一覧を選び、カップ戦の非順位成績は除外される")


def test_build_club_extra_skips_failed_club_and_continues() -> None:
    """1クラブの取得失敗は、そのクラブだけスキップして続行すること。"""
    clubs = [
        {"idTeam": "1", "ja": "壊れるクラブ", "league": "j2", "slug": "broken"},
        {"idTeam": "2", "ja": "正常クラブ", "league": "j2", "slug": "ok"},
    ]

    ok_html = make_next_html(
        [
            '10:{"newsList":[{"id":1,"title":"N","publishDisplayDate":"2026-01-01 00:00:00+00"}]}',
            '11:{"players":[{"playerId":"1","playerName":"P1","uniformNo":"1","position":"GK"}]}',
        ]
    )

    def flaky_fetch(slug: str) -> str:
        if slug == "broken":
            raise RuntimeError("simulated network failure")
        return ok_html

    out = build_club_extra(clubs, fetch_fn=flaky_fetch, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    assert "1" not in out["clubs"], "失敗したクラブは結果に含まれないはず"
    assert "2" in out["clubs"], "他のクラブは失敗の影響を受けず続行するはず"
    assert out["meta"]["failed"] == ["1(壊れるクラブ)"]
    assert out["meta"]["clubCount"] == 2
    print("OK: 取得失敗はそのクラブだけスキップし、他は続行する")


def main() -> None:
    tests = [
        test_extract_and_search_chunks_across_split_scripts,
        test_parse_publish_date,
        test_build_media_url_uses_filename_not_api_path,
        test_parse_club_page_picks_longest_news_and_valid_players,
        test_build_club_extra_skips_failed_club_and_continues,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
