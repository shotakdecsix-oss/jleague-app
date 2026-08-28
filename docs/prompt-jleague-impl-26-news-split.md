# 第26弾 実装指示：news.json をクラブ別に分割する(通信量の削減)

対象: `scripts/build_dist.py`、`index.html`
**`scripts/fetch_news.py` は触らない**(理由は1-2)

前提ドキュメント: `docs/handoff-jleague-dashboard.md`、`docs/fix-incremental-merge.md`

---

## 1. 背景

### 1-1. 実測

配信物の実サイズ(gzip後は `gzip -6` で計測):

| ファイル | 生 | gzip後 |
|---|---|---|
| `data/processed/news.json` | 2,968 KB | **987 KB** |
| `data/processed/club_extra.json` | 1,335 KB | 142 KB |
| `data/processed/j1_matches.json` | 207 KB | 7 KB |
| `data/processed/calendar.json` | 157 KB | 13 KB |

**gzipが効いていないのは `news.json` だけ**(記事タイトル・URL・サムネイルURLが並ぶので圧縮しにくい)。
`club_extra.json` は生では大きいが圧縮後142KBなので、この弾では**触らない**。

### 1-2. 構造上の無駄

`news.json` は `{ meta, teams: { <idTeam>: [items] } × 60クラブ, obPlayers }`。
ところが `renderNews()` が読むのは `news.teams[team.idTeam]` の**1クラブぶんだけ**。
つまり**60分の1のために約1MBを落としている**。

なお `fetch_news.py` 側は「前回のnews.jsonに新規取得分をマージして累積する」設計で、
`load_existing_news()` / `merge_with_existing()` がその中核。ここは
`docs/fix-incremental-merge.md` の事故を経て固まった部分なので、**この弾では一切触らない。**
分割は**配信物を作る段階(`build_dist.py`)でだけ**行う。

---

## 2. 方針：ソースは1ファイルのまま、配信物だけ分割する

```
data/processed/news.json           ← ソース。今までどおり1ファイル(fetch_news.pyが累積更新)
        ↓ build_dist.py が分割
dist/data/processed/news/<idTeam>.json   ← 60ファイル。{ meta, items: [...] }
dist/data/processed/news_ob.json         ← { meta, obPlayers: {...} } (全クラブ共通)
dist/data/processed/news.json            ← 配信しない(除外する)
```

この形にすると:

- `fetch_news.py` と `test_fetch_news.py` は**無変更**。累積マージの事故リスクがゼロ。
- ニュースタブを開いたときの通信は **1クラブぶん(数十KB)** になる。
- OB選手ニュース(`obPlayers`)は全クラブ共通なので別の1ファイルにする
  (現状 `obPlayers` は空だが、`data/config/watchlist.json` を設定すれば入る)。

---

## 3. `scripts/build_dist.py` の変更

### 3-1. 落とし穴を先に

**`copy_dirs()` は `f.is_file()` のファイルしかコピーしない。**
`dist/data/processed/news/` のような**サブディレクトリは自動では作られない**。
`split_news()` の中で自分で `mkdir(parents=True, exist_ok=True)` すること。

**`clean_dist()` が毎回 `dist/` を消す**ので、`split_news()` は `copy_dirs()` より後に呼ぶ。

### 3-2. 追加するコード

```python
COPY_DIRS_EXCLUDE: dict[str, set[str]] = {
    "data/history": {"ics_state.json"},
    # news.jsonは丸ごと配信すると約1MB(gzip後)ある。split_news()がクラブ別に分割して
    # 出し直すので、元のファイルはdistに置かない。
    "data/processed": {"news.json"},
}


def split_news() -> None:
    """
    data/processed/news.json を、配信用にクラブ別へ分割して dist/ に書く。

    ソース側(data/processed/news.json)は1ファイルのまま変えない。fetch_news.pyの
    累積マージ(load_existing_news / merge_with_existing)がそこに依存しているため、
    分割はあくまで配信物を作る段階だけで行う。

    出力:
        dist/data/processed/news/<idTeam>.json  {"meta": ..., "items": [...]}
        dist/data/processed/news_ob.json        {"meta": ..., "obPlayers": {...}}

    ニュースが1件も無いクラブのファイルも空配列で必ず書く。書かないと
    ブラウザ側が404を引き、「取得に失敗したのか、記事が無いのか」を区別できなくなる。
    """
    src = BASE_DIR / "data" / "processed" / "news.json"
    if not src.exists():
        print("[warn] data/processed/news.json が無い。ニュースの分割はスキップ", file=sys.stderr)
        return
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[error] news.json を読めない: {e}", file=sys.stderr)
        sys.exit(1)

    meta = data.get("meta", {})
    teams = data.get("teams", {}) or {}
    out_dir = DIST_DIR / "data" / "processed" / "news"
    out_dir.mkdir(parents=True, exist_ok=True)

    total_items = 0
    for id_team, items in teams.items():
        items = items or []
        total_items += len(items)
        payload = {"meta": meta, "items": items}
        (out_dir / f"{id_team}.json").write_text(
            json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    ob_payload = {"meta": meta, "obPlayers": data.get("obPlayers", {}) or {}}
    (DIST_DIR / "data" / "processed" / "news_ob.json").write_text(
        json.dumps(ob_payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"[info] ニュースを分割: {len(teams)}クラブ / 記事{total_items}件")
```

`import json` を足し、`main()` の **`copy_dirs()` の後**に `split_news()` を呼ぶ。

> `indent=2` を付けていないのは配信物だから(改行とインデントぶんがそのまま通信量になる)。
> ソース側の `news.json` は今までどおり `indent=2` のままでよい(gitの差分が読めるように)。

### 3-3. マスタに居ないクラブへの備え

`teams` のキーはマスタの `idTeam` と一致している前提だが、
**一致を検証しない**(fetch_news.py側が既に照合済み)。
将来ズレたら「ニュースが出ないクラブがある」形で表面化するので、
`report_size()` の出力でファイル数が60であることを目視すれば足りる。

---

## 4. `index.html` の変更

### 4-1. `ensureNews()` だけを書き換える(呼び出し側は無変更)

現状:

```js
async function ensureNews() {
  if (state.news === undefined) state.news = await getJsonOptional("data/processed/news.json");
  return state.news;
}
```

呼び出しは2箇所(`ensureTabData()` 内と、オンデマンド更新後の再取得)。
**どちらも引数を取らない**ので、`ensureNews()` の中で自分のクラブを解決する形にすれば
呼び出し側は1文字も変えなくて済む。

```js
/* ニュースはクラブ別に分割配信されている(第26弾)。表示中のクラブのぶんだけ取りに行き、
   renderNews()が期待する形({meta, teams:{idTeam:[...]}, obPlayers})に組み直して返す。
   renderNews()側は無変更。

   ローカル開発ではリポジトリ直下でサーバーを立てる運用があり、そこには
   data/processed/news/ が存在しない(分割はdistを作るときだけ行うため)。
   分割ファイルが引けなかったときは、元の news.json にフォールバックする。 */
async function ensureNews() {
  const found = findTeam(state.idTeam);
  const idTeam = found ? found.team.idTeam : null;
  const cache = state.newsCache || (state.newsCache = {});

  if (idTeam && cache[idTeam] === undefined) {
    cache[idTeam] = await getJsonOptional("data/processed/news/" + idTeam + ".json");
  }
  if (state.newsOb === undefined) {
    state.newsOb = await getJsonOptional("data/processed/news_ob.json");
  }

  const one = idTeam ? cache[idTeam] : null;
  if (one) {
    state.news = {
      meta: one.meta || null,
      teams: idTeam ? { [idTeam]: one.items || [] } : {},
      obPlayers: (state.newsOb && state.newsOb.obPlayers) || {},
    };
    return state.news;
  }

  // フォールバック(ローカル開発、または分割前の配信物を見ているとき)
  if (state.newsWhole === undefined) {
    state.newsWhole = await getJsonOptional("data/processed/news.json");
  }
  state.news = state.newsWhole;
  return state.news;
}
```

### 4-2. クラブを切り替えたとき

`state.newsCache` はクラブごとに別キーなので、切り替えれば自動で取り直される。
一度見たクラブのぶんはセッション中キャッシュされる(60クラブぶんを持っても数MBには届かない)。
**`selectTeam()` に追加のリセット処理は要らない。**

### 4-3. 第25弾(Service Worker)との関係

`getJsonOptional()` は `fetch(path, { cache: "no-store" })` を使っている。
これはブラウザのHTTPキャッシュを避ける指定で、**Service WorkerのCache Storageとは別物**。
第25弾のSWは `fetch` イベントで通常どおり介入できるので、この2つは共存する。
分割によって「オフラインで持っておくべきファイル」が1個から61個に増えるが、
第25弾のSWは**データを先読みしない**(network-first + 実際に見たものだけキャッシュ)ので、
設定変更は不要。**見たことのあるクラブのニュースだけオフラインで読める**、という自然な挙動になる。

---

## 5. 確認手順

1. `python scripts/build_dist.py`
2. `dist/data/processed/news/` に **60ファイル**あること。
   `dist/data/processed/news.json` が**無い**こと(除外が効いている)。
   `dist/data/processed/news_ob.json` があること。
3. `report_size()` の出力で、上位5件から `news.json` が消えていること。
   `dist/` 合計が **4MB前後**に減っていること(元は約7.8MB)。
4. `cd dist && python -m http.server 8000` → ニュースタブを開く。
   DevTools → Network で、落ちているのが **`news/<idTeam>.json` の1本だけ**で、
   サイズが数十KBであること。
5. **クラブを3つ切り替えて**、それぞれ別のファイルが1本ずつ落ちること。
   2回目に同じクラブへ戻したときは**追加のリクエストが飛ばない**こと(キャッシュ)。
6. **リポジトリ直下**(`dist` ではない)で `python -m http.server 8000` を起動し、
   ニュースタブが**従来どおり表示される**こと(4-1のフォールバックの確認)。
   このときNetworkには `news/<idTeam>.json` の404 → `news.json` の200 が並ぶ。
7. `git status` の差分が `index.html` / `scripts/build_dist.py` / `dist/**` であること。
   **`scripts/fetch_news.py` と `data/processed/news.json` に差分が出ていないこと**
   (出ていたら、この弾の範囲を越えている)。

---

## 6. この先やるなら(この弾ではやらない)

- `club_extra.json`(gzip後142KB)の分割。同じ手が使えるが、効果は10分の1以下。
- `news_ob.json` を選手別に分割。現状 `obPlayers` は空なので不要。
- 記事の件数上限(`MAX_ITEMS_PER_TEAM`)の見直し。これは `fetch_news.py` 側の話になるので、
  触るなら `docs/fix-news-volume.md` を読んでから別の弾で。
