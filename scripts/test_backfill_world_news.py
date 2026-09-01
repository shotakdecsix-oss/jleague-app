"""backfill_world_news.parse_list_page のテスト。

サンプルは 2026-09-01 に実物の
https://web.gekisaka.jp/article/foreign?news_type=news から採取したマークアップ。
「?time= を持つ記事だけを拾う(定型記事は捨てる)」という判定が肝なので、
画像あり2件・画像なし1件を含めてある。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_world_news import collect, parse_list_page  # noqa: E402

SAMPLE = """
<div id="article-list" class="article-list news">
	<div class="article-block"  id="n457979">
		<a href="//web.gekisaka.jp/news/world/detail/?457979-457979-fl" rel="bookmark">
			<div class="thumbnail news"><div>
				<img itemprop="image" src="//f.image.geki.jp/data/image/news/800/458000/457979/news_457979_1.webp?time=20260901110510" class="geki_image" />
			</div></div>
			<div class="article-info">
				<div class="title">
										堂安律がサウジ移籍を拒否か&hellip;アルイテハド移籍目前から一転して破談と報道				</div>
						<div class="new">NEW</div>
			</div>
		</a>
	</div>
	<div class="article-block"  id="n457976">
		<a href="//web.gekisaka.jp/news/world/detail/?457976-457976-fl" rel="bookmark">
			<div class="thumbnail news"><div class="image-none"></div></div>
			<div class="article-info">
				<div class="title">
										バルセロナvsラージョ 試合記録				</div>
			</div>
		</a>
	</div>
	<div class="article-block"  id="n457899">
		<a href="//web.gekisaka.jp/news/world/detail/?457899-457899-fl" rel="bookmark">
			<div class="thumbnail news"><div>
				<img itemprop="image" src="//f.image.geki.jp/data/image/news/800/458000/457899/news_457899_1.webp?time=20260831031403" class="geki_image" />
			</div></div>
			<div class="article-info">
				<div class="title">
										田中碧は後半から出場で今季プレミアリーグ初出場				</div>
			</div>
		</a>
	</div>
</div>
"""


def test_parses_only_articles_with_image_timestamp() -> None:
    items = parse_list_page(SAMPLE)
    assert len(items) == 2, f"?time=のある2件だけ拾うはず: {items}"
    titles = [i["title"] for i in items]
    assert "バルセロナvsラージョ 試合記録" not in titles, "定型記事は捨てるはず"
    first = items[0]
    assert first["title"] == "堂安律がサウジ移籍を拒否か…アルイテハド移籍目前から一転して破談と報道", first
    assert first["link"] == "https://web.gekisaka.jp/news/world/detail/?457979-457979-fl", first
    assert first["publishedJst"] == "2026-09-01T11:05:10+09:00", first
    assert first["source"] == "ゲキサカ(海外)" and first["sourceType"] == "feed", first
    print("OK: ?time=を持つ記事だけを拾い、HTMLエンティティ復元・URL補完・JST変換が正しい")


def test_ignores_broken_markup() -> None:
    assert parse_list_page("") == []
    assert parse_list_page("<div class='article-block' id='n1'>壊れている") == []
    print("OK: 想定外のマークアップでは例外を投げず空リストを返す")


def test_collect_stops_when_reaching_cutoff() -> None:
    """cutoffより古い記事が出たページで打ち切る(無駄にページを送らない)。"""
    pages = {1: SAMPLE, 2: SAMPLE.replace("20260831031403", "20200101000000")}
    fetched: list[int] = []

    def fake_fetch(page: int) -> str:
        fetched.append(page)
        return pages.get(page, "")

    got = collect(days=30, max_pages=10, fetch_fn=fake_fetch, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    assert fetched == [1, 2], f"2ページ目で打ち切るはず: {fetched}"
    assert len(got) == 4, got
    print("OK: cutoffより古い記事に届いたページで取得を打ち切る")


def test_collect_stops_on_empty_page() -> None:
    """記事0件のページ(末尾)に来たら止める。"""
    fetched: list[int] = []

    def fake_fetch(page: int) -> str:
        fetched.append(page)
        return SAMPLE if page == 1 else ""

    collect(days=3650, max_pages=10, fetch_fn=fake_fetch, sleep_fn=lambda s: None, log=lambda *a, **k: None)
    assert fetched == [1, 2], f"空ページで止まるはず: {fetched}"
    print("OK: 記事0件のページで取得を打ち切る")


def main() -> None:
    tests = [
        test_parses_only_articles_with_image_timestamp,
        test_ignores_broken_markup,
        test_collect_stops_when_reaching_cutoff,
        test_collect_stops_on_empty_page,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
