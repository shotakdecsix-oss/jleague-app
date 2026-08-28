# 第25弾 実装指示：PWAとして成立させる(アイコン・manifest・オフライン)

対象: `icons/`(**PNGは生成済み**)、`manifest.webmanifest`、`index.html`、
`scripts/build_dist.py`、`sw.js`(新規)

前提ドキュメント: `docs/handoff-jleague-dashboard.md`

---

## 0. 背景 — 「ホーム画面に追加して使う」が今は成立していない

`manifest.webmanifest` があり `display:standalone` を宣言しているので、
**このアプリはホーム画面から起動して使う想定**になっている。ところが実態は:

1. **iOSではアイコンが出ない。** `<link rel="apple-touch-icon" href="icons/icon.svg">` と書いてあるが、
   **iOS Safari は apple-touch-icon にSVGを受け付けない**。ホーム画面に追加すると、
   アイコンは「ページのスクリーンショットの縮小版」になる。
2. **manifestの色が湘南グリーン(`#82c039`)でハードコード**されていて、`index.html` の
   `<meta name="theme-color" content="#0b6b3a">` とも食い違っている。
3. **Service Workerが無い。** 圏外・地下鉄・スタジアムの中では**真っ白な画面**になる。
   「試合を見に行った先で順位表を確認する」という、このアプリが一番使われる場面で開かない。

この弾は 1→2→3 の順に潰す。**1と2だけでも独立して意味がある**(30分で終わる)。
3(Service Worker)は事故ると「古い画面が張り付いて更新されない」状態になるので、
**4章の更新戦略を必ず読んでから**書くこと。

---

## 1. アイコン(PNGは生成済み。参照を張り替えるだけ)

### 1-1. 生成済みのファイル

`scripts/build_icons.py`(新規・作成済み)を実行して、以下が `icons/` に生成済み:

| ファイル | 用途 |
|---|---|
| `icon-180.png` | iOSの `apple-touch-icon`。iOS側が角を丸めるので、**角丸にせず透明部分も作らない**正方形で描いてある(透明にするとiOSが黒で埋める) |
| `icon-192.png` | Android/Chrome の `purpose:"any"` |
| `icon-512.png` | 同上(スプラッシュ用) |
| `icon-maskable-512.png` | `purpose:"maskable"`。端末が最大20%を切り落とすので、文字を中央の安全域に収めて小さめに描いてある |

既存の `icon.svg` は**残す**(対応ブラウザではベクタのまま使える)。

色を変えたくなったら `scripts/build_icons.py` の `BG_COLOR` を書き換えて再実行する。
そのときは manifest と `index.html` の `theme-color` も合わせて直すこと(**3箇所ある**)。

`icons/` は `build_dist.py` の `COPY_DIRS` に既に入っているので、**配信側の対応は不要**。

### 1-2. `index.html` の `<head>`

```html
<link rel="apple-touch-icon" href="icons/icon-180.png">
<link rel="icon" type="image/svg+xml" href="icons/icon.svg">
<link rel="icon" type="image/png" sizes="192x192" href="icons/icon-192.png">
```

`apple-touch-icon` に `sizes` を書く必要はない(1枚しか置かないため)。

---

## 2. manifest の色と icons

**変更後の `manifest.webmanifest` 全文**:

```json
{
  "name": "Jリーグ ダッシュボード",
  "short_name": "Jリーグ",
  "start_url": ".",
  "scope": ".",
  "display": "standalone",
  "background_color": "#33404f",
  "theme_color": "#33404f",
  "icons": [
    { "src": "icons/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any" },
    { "src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" },
    { "src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```

`#33404f` は `index.html` の `NEUTRAL_COLOR`(全体モードの中立色)と同じ値。
**manifestは静的ファイルなのでクラブごとに変えられない**。特定クラブの色を焼き付けるより、
アプリ共通の中立色にしておくほうが、どのクラブを選んでいる利用者にも違和感が無い。
起動後は `applyTheme()` が `<meta name="theme-color">` を選択中のクラブ色に書き換えるので、
**中立色が見えるのは起動直後のスプラッシュの一瞬だけ**になる。

`index.html` の `<meta name="theme-color" content="#0b6b3a">` も `#33404f` に揃える
(このmetaの値はJSが上書きするまでの初期値でしかない)。

> `icons` 配列でSVGを先頭に置いているのは、対応ブラウザにベクタを優先させるため。
> **SVGだけにしないこと** — それが今の不具合そのもの。

---

## 3. Service Worker: 何をキャッシュし、何をしないか

方針を先に決める。**全部を一律にキャッシュしない。** このアプリのファイルは性格が3種類ある。

| 種類 | 対象 | 戦略 | 理由 |
|---|---|---|---|
| アプリ本体 | `index.html` | **network-first**(3秒でタイムアウト→キャッシュ) | 最新のコードを最優先。ただし電波が悪いときに白画面で待たせない |
| データ | `data/**/*.json` | **network-first**(3秒→キャッシュ) | 順位・結果は鮮度が命。オフラインでは直前の内容を出す |
| 動かない資産 | `icons/*`, `manifest.webmanifest` | **cache-first** | 変わらないので毎回取りに行く必要が無い |
| キャッシュしない | `deploy-time.txt`, `deploy-version.txt`, `ics/*.ics`, TheSportsDBへの外部リクエスト | — | 鮮度表示そのもの / 端末のカレンダーアプリが直接取りに行く / **オリジンが違うものはSWで触らない** |

**データをstale-while-revalidate(先にキャッシュを返して裏で更新)にしないこと。**
「一瞬古い順位が出てから差し替わる」挙動は、このアプリでは誤読の原因になる。
フッターに取得時刻を出しているとはいえ、順位表の数字が目の前で書き換わるのは避ける。

### 3-1. `sw.js`(リポジトリ直下に新規作成)

```js
/* Jリーグ ダッシュボード Service Worker
   CACHE_VERSION は build_dist.py がビルドのたびに実際の値へ置換する(4章)。
   ソースツリー上のプレースホルダのままでは動かして良いが、その場合キャッシュは
   更新されないので、ローカル開発では DevTools の "Update on reload" を使うこと。 */
const CACHE_VERSION = "__CACHE_VERSION__";
const CACHE_NAME = "jleague-" + CACHE_VERSION;
const NETWORK_TIMEOUT_MS = 3000;

/* インストール時に先読みするもの。データは入れない(重いうえ、すぐ古くなる)。 */
const PRECACHE = [
  "./", "./index.html", "./manifest.webmanifest",
  "./icons/icon.svg", "./icons/icon-192.png", "./icons/icon-512.png",
];

self.addEventListener("install", ev => {
  // skipWaiting()は呼ばない。開いているタブが古いSWで動いている最中に
  // 新しいSWがデータを差し替えると、表示中の画面と取得元がちぐはぐになる。
  // 新版は「全タブを閉じた次の起動」から効く。
  ev.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(PRECACHE)).catch(() => {}));
});

self.addEventListener("activate", ev => {
  // 自分以外の世代のキャッシュを消す。これを忘れると端末に何世代も残る。
  ev.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isCacheFirst(url) {
  return url.pathname.includes("/icons/") || url.pathname.endsWith("manifest.webmanifest");
}
function isNeverCache(url) {
  return url.pathname.endsWith("deploy-time.txt")
      || url.pathname.endsWith("deploy-version.txt")
      || url.pathname.includes("/ics/");
}

async function networkFirst(req) {
  const cache = await caches.open(CACHE_NAME);
  try {
    // タイムアウトを付けるのは、電波が弱いときに数十秒白画面で待たせないため。
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), NETWORK_TIMEOUT_MS);
    const res = await fetch(req, { signal: ctrl.signal });
    clearTimeout(timer);
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  } catch (e) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw e;
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(CACHE_NAME);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res && res.ok) cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", ev => {
  const req = ev.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // 別オリジン(TheSportsDBのオンデマンド取得、YouTube埋め込み、ニュースのサムネイル)は
  // 一切触らない。CORSやレスポンスの種類が絡んで壊れやすく、キャッシュする利点も無い。
  if (url.origin !== self.location.origin) return;
  if (isNeverCache(url)) return;
  ev.respondWith(isCacheFirst(url) ? cacheFirst(req) : networkFirst(req));
});
```

### 3-2. `index.html` での登録

`init()` の**最後**(データ読み込みの成否に関わらず)に置く。登録の失敗でアプリを落とさない。

```js
/* Service Workerの登録。file://で開いたときやSW非対応のブラウザでは黙って何もしない。
   登録に失敗してもアプリ本体は通常どおり動く(オフラインで開けなくなるだけ)。 */
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => { /* 無視 */ });
  });
}
```

---

## 4. 「古い画面が張り付く」事故を防ぐ(ここが本題)

Service Workerは **`sw.js` のバイト列が変わったときにだけ**更新される。
`CACHE_VERSION` をプレースホルダのままにすると、`index.html` をいくら直しても
**SWは古いキャッシュを配り続ける**。ここを外すと、第24弾のときの
「デプロイは成功しているのに画面が変わらない」と同じ事故が、今度は**利用者の端末側で**起きる。

`scripts/build_dist.py` に以下を足す:

```python
TOP_LEVEL_FILES = ["index.html", "manifest.webmanifest", "robots.txt"]  # sw.jsはここに入れない


def write_service_worker() -> None:
    """
    sw.js を dist/ に書き出す。単純コピーではなく、CACHE_VERSION を
    index.html の内容ハッシュに置換してから書く。

    deploy-version.txt(gitの短縮ハッシュ)を使わないのは、あれが「1つ前のコミット」を
    指すため。index.html を直しただけでまだコミットしていない状態でビルドすると
    値が変わらず、SWが更新されない。内容ハッシュなら中身が1バイトでも変われば必ず変わる。
    """
    src = BASE_DIR / "sw.js"
    if not src.exists():
        print("[warn] sw.js が無い。オフライン対応はスキップ", file=sys.stderr)
        return
    index_bytes = (BASE_DIR / "index.html").read_bytes()
    version = hashlib.sha256(index_bytes).hexdigest()[:12]
    text = src.read_text(encoding="utf-8").replace("__CACHE_VERSION__", version)
    (DIST_DIR / "sw.js").write_text(text, encoding="utf-8")
    print(f"[info] sw.js を生成: CACHE_VERSION={version}")
```

`import hashlib` を足し、`main()` の `copy_top_level_files()` の後に `write_service_worker()` を呼ぶ。

### 4-1. Renderのキャッシュヘッダに注意

`sw.js` 自体がCDNに長期キャッシュされると、新しいSWが端末に届かない。
Renderの静的サイトの設定で **`/sw.js` に `Cache-Control: no-cache`** を付けること
(Render側のヘッダ設定か `render.yaml` の `headers`)。
`index.html` も同様にしておくのが安全。
**この1点を落とすと、他を全部正しく実装しても更新が届かない。**

### 4-2. ローカル開発でSWが居座る

`localhost:8000` で一度SWを登録すると、`index.html` を直しても古い版が出続けて
「直したのに変わらない」と混乱する。開発中は DevTools → Application → Service Workers の
**"Update on reload"** を有効にしておくこと。

---

## 5. やらないこと

- プッシュ通知(別の許可・別のサーバー基盤が要る。この弾では扱わない)
- バックグラウンド同期
- データJSONの先読み(precache)。**重く、すぐ古くなる**ので入れない
- `skipWaiting()` / 「新しいバージョンがあります」バナー(3-1のコメントの理由で今回は見送り)
- 別オリジン(TheSportsDB・YouTube・ニュースのサムネイル)のキャッシュ

---

## 6. 確認手順

1. `python scripts/build_icons.py`(生成済みなら省略可) → `python scripts/build_dist.py`
2. `dist/sw.js` の1行目付近で `CACHE_VERSION` が**実際のハッシュに置換されている**こと。
   `__CACHE_VERSION__` のままなら4章の実装が漏れている。
3. `cd dist && python -m http.server 8000` で開き、DevTools → Application →
   Service Workers が **activated** になっていること。
4. DevTools → Network を **Offline** にしてリロード。
   **順位表・日程が直前の内容で表示されること**(真っ白にならないこと)。
5. `index.html` を1文字変えて `build_dist.py` を再実行 → リロード**2回**。
   2回目で新しい内容になること(1回目は古いSWが応答するのが正しい挙動)。
6. **iPhone実機**でホーム画面に追加し、**アイコンが緑地に白い「J」**になること
   (スクリーンショットの縮小版になっていたら1章が効いていない)。
7. 機内モードにして、ホーム画面のアイコンから起動 → 画面が出ること。
8. `git status` の差分が `index.html` / `dist/**` / `manifest.webmanifest` /
   `icons/*.png` / `sw.js` / `scripts/build_dist.py` / `scripts/build_icons.py` であること。
