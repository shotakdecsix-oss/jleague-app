/* Jリーグ ダッシュボード Service Worker (第25弾)

   目的は「圏外・地下鉄・スタジアムの中で真っ白な画面にしない」こと。
   このアプリはホーム画面に追加して使う前提(manifestが display:standalone)なので、
   通信が無いときに何も出ないのは致命的だった。

   CACHE_VERSION は scripts/build_dist.py がビルドのたびに index.html の内容ハッシュへ
   置換する。ソースツリー上はプレースホルダのままなので、リポジトリ直下でサーバーを
   立てて開発するときは DevTools の "Update on reload" を有効にすること
   (そうしないと古いキャッシュが配られ続け、「直したのに変わらない」ことになる)。 */
const CACHE_VERSION = "a3b3dd65c453";
const CACHE_NAME = "jleague-" + CACHE_VERSION;
const NETWORK_TIMEOUT_MS = 3000;

/* インストール時に先読みするもの。データJSONは入れない。
   重いうえに(club_extra.jsonだけで1.3MB)、すぐ古くなるため。 */
const PRECACHE = [
  "./", "./index.html", "./manifest.webmanifest",
  "./icons/icon.svg", "./icons/icon-192.png", "./icons/icon-512.png",
];

self.addEventListener("install", ev => {
  /* skipWaiting() は呼ばない。開いているタブが古いSWで動いている最中に新しいSWが
     データを差し替えると、表示中の画面と取得元がちぐはぐになる。
     新版は「全タブを閉じた次の起動」から効く。 */
  ev.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(PRECACHE)).catch(() => {}));
});

self.addEventListener("activate", ev => {
  // 自分以外の世代を消す。これを忘れると端末に何世代も残る。
  ev.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/* 変わらないものだけ cache-first。 */
function isCacheFirst(url) {
  return url.pathname.includes("/icons/") || url.pathname.endsWith("manifest.webmanifest");
}

/* 触らないもの:
     deploy-time.txt / deploy-version.txt … 鮮度表示と更新検知そのもの(第30弾)。
                                            キャッシュすると「新しい版が出た」を永久に見逃す
     ics/*.ics                           … 端末のカレンダーアプリが直接取りに行く */
function isNeverCache(url) {
  return url.pathname.endsWith("deploy-time.txt")
      || url.pathname.endsWith("deploy-version.txt")
      || url.pathname.includes("/ics/");
}

/* データは network-first。stale-while-revalidate(先にキャッシュを返して裏で更新)にはしない。
   「一瞬古い順位が出てから目の前で書き換わる」のは、このアプリでは誤読の原因になる。
   タイムアウトを付けるのは、電波が弱いときに数十秒白画面で待たせないため。 */
async function networkFirst(req) {
  const cache = await caches.open(CACHE_NAME);
  try {
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
  let url;
  try {
    url = new URL(req.url);
  } catch (e) {
    return;
  }
  /* 別オリジン(TheSportsDBのオンデマンド取得、YouTube埋め込み、ニュースのサムネイル)は
     一切触らない。CORSやレスポンスの種類が絡んで壊れやすく、キャッシュする利点も無い。 */
  if (url.origin !== self.location.origin) return;
  if (isNeverCache(url)) return;
  ev.respondWith(isCacheFirst(url) ? cacheFirst(req) : networkFirst(req));
});
