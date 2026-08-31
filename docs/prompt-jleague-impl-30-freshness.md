# 第30弾 実装指示：更新が画面に届かない(未反映)の解消

対象ファイル:

- `index.html`（1〜3章）
- `scripts/build_dist.py`（3章）
- `scripts/fetch_official.py`（5章）
- `.github/workflows/update.yml`, `.github/workflows/match_events.yml`（4章・6章）

前提ドキュメント: `docs/handoff-jleague-dashboard.md`

---

## 0. この指示書の性格と、守ってほしい制約

2026-08-31 に「更新したのにアプリに反映されない」の原因を静的レビューで洗い出し、
15件の指摘のうち**効果の大きい5件だけ**を1弾にまとめたもの。

**新機能はひとつも足さない。** 出す情報・並び順・タブ構成・データのスキーマは現状のまま。
やるのは「作ったデータを利用者の画面まで確実に届ける」ことだけ。

守る制約:

- **外部ライブラリを追加しない。** 単一HTMLで自己完結する構成を崩さない。
- **Service Worker は入れない。** 更新が届かない問題を解こうとしてキャッシュ層を足すと、
  今度は「古いキャッシュを配り続ける」という逆向きの事故になる。現状SWが無いのは正しい状態。
- **`index.html` を1文字でも変えたら、最後に必ず `python scripts/build_dist.py` を実行する。**
  Renderが配信しているのは `dist/` であって `index.html` ではない。
- 章は 1 → 2 → 3 → 4 → 5 → 6 の順に入れる。**1章と2章だけでも独立して意味がある**（体感は
  ここでほぼ解決する）。4〜6章はサーバー側なので、別コミットに分けてよい。

### なぜこの弾が要るのか（実際に起きた事故）

2026-08-30 の夜から 8/31 の朝まで、ローカルPCの `live_watch.py` が10回連続で push に
失敗していた。`git pull --rebase` が `dist/ics/*.ics` の生成物で毎回同じコミットにぶつかり、
`--abort` して次回に賭ける作りだったので、次回も同じ所で止まる＝永久に詰まった。

そこまでは「押せていないだけ」だが、**pull が通らないので `data/processed/j3_matches.json` が
8/30 22時台のまま凍りついた**。その中では 8/30 の J3 6試合が `status: NS`（未開始）のまま。
`fetch_match_events.py` は試合終了後にしかハイライト動画を探しに行かないので、
**動画の検索が一度も走らなかった**（`daznSearchAttempts: 0` として記録が残っている）。

push詰まりが、静かにデータの中身まで壊していた。しかもこの間、GitHub Actions は
**緑のチェックマークを出し続けていた**（4章）。この弾はこの種の事故を、
「起きにくくする」より先に「**起きたら分かる・利用者が古い画面を見続けない**」ようにする。

---

## 1. 【最重要】⟳ボタンが3ファイルしか読み直さない

### 現状

`index.html` の `refreshStaticMatchData()`（3734行付近）:

```js
async function refreshStaticMatchData() {
  state.calendar = undefined;
  for (const lg of Object.keys(LEAGUES)) {
    leagueCache(lg).matchEvents = undefined;
    leagueCache(lg).matches = undefined;
  }
  await Promise.all([
    ensureCalendar(),
    ...Object.keys(LEAGUES).map(lg => ensureMatchEvents(lg)),
    ...Object.keys(LEAGUES).map(lg => ensureMatches(lg)),
  ]);
  renderActiveTab();
}
```

捨てているのは `calendar` / `matches` / `matchEvents` の3つだけ。
一方 `ensure*` 系（1237〜1332行）は**すべて「一度読んだら二度と読まない」ガード**を持つ:

| キャッシュ | 置き場所 | ⟳で捨てているか |
|---|---|---|
| `matches` | `state.cache[league].matches` | ○ |
| `matchEvents` | `state.cache[league].matchEvents` | ○ |
| `calendar` | `state.calendar` | ○ |
| `standings` | `state.cache[league].standings` | **×** |
| `simulation` | `state.cache[league].simulation` | **×** |
| `stats` | `state.cache[league].stats` | **×** |
| `impact` | `state.cache[league].impact` | **×** |
| `history` | `state.cache[league].history` | **×** |
| `rankHistory` | `state.cache[league].rankHistory` | **×** |
| `clubExtra` | `state.clubExtra` | **×** |
| `news` | `state.news` | **×** |
| `emperorsCup` | `state.emperorsCup` | **×** |
| `emperorsCupEvents` | `state.emperorsCupEvents` | **×** |

つまり**順位表・優勝/昇格確率・スタッツ・クラブ情報・ニュース・天皇杯は、
アプリを完全に閉じて開き直すまで一生更新されない**。⟳を押しても変わらない。
第15弾のコメント（3728〜3733行）は同じ問題を3ファイルぶんだけ直した記録で、
残りは手つかずのまま残っている。

### やること

`refreshStaticMatchData()` を「静的JSONのキャッシュを**全部**捨てる」に変える。
`state.masters`（クラブのマスタ。ビルドしないと変わらない）と
`state.ondemand`（localStorage由来。`ondemandActiveCacheForLeague()` が
`generatedAtJst` の比較で自動的に破棄する）は**触らない**。

```js
/* 第30弾: 静的JSONのキャッシュを全部捨てる。第15弾では3ファイルだけを対象にしたが、
   順位表・確率・スタッツ・ニュースが「⟳を押しても変わらない」まま残っていた。
   自前の静的JSONは回数制限が無いので、⟳のたびに全部読み直してよい。
   state.masters は触らない(ビルドしないと変わらない)。
   state.ondemand も触らない(ondemandActiveCacheForLeague がgeneratedAtJst比較で自動破棄する)。 */
function clearStaticCaches() {
  state.cache = {};
  state.calendar = undefined;
  state.clubExtra = undefined;
  state.news = undefined;
  state.emperorsCup = undefined;
  state.emperorsCupEvents = undefined;
}

async function refreshStaticMatchData() {
  clearStaticCaches();
  // 描画に必要なぶんは renderActiveTab() -> ensureTabData() が自分で取り直す。
  // ここで先読みするのは、どのタブに居ても日程の得点速報が最新になるようにするため。
  await Promise.all([
    ensureCalendar(),
    ...Object.keys(LEAGUES).map(lg => ensureMatchEvents(lg)),
    ...Object.keys(LEAGUES).map(lg => ensureMatches(lg)),
  ]);
  await renderActiveTab();
}
```

注意点:

- **`state.cache = {}` で丸ごと捨ててよい。** `state.cache` に書き込むのは
  `leagueCache()`（1234行）経由の `ensure*` だけで、他の用途には使われていない（確認済み）。
- `renderActiveTab()`（1687行）は内部で `ensureTabData(league, state.activeTab)` を
  呼ぶので、いま開いているタブに必要なJSONは自動的に取り直される。**先読みを増やす必要はない。**
- `renderActiveTab()` は `async` なので `await` を付ける。現状は付いていない。
- 全体モード（`state.viewMode === "all"`）でも `renderActiveTab()` が
  `renderAllModeActiveTab()` に振り分けるので、分岐を書き足す必要はない。

---

## 2. 【最重要】アプリに戻ってきたときに何も読み直さない

### 現状

`index.html` 1620行付近:

```js
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    stopLivePolling();
  } else {
    evaluateLiveSchedule();
  }
});
```

再開しているのは **TheSportsDB のライブスコアのポーリングだけ**。
自前の静的JSONは一切読み直さない。

ホーム画面に追加した standalone PWA は実質「閉じない」ので、
1章を直しても**利用者が⟳を押さない限り、初回ロード時のデータのまま何日も表示され続ける**。
1章と2章はセットで入れて初めて効く。

### やること

可視に戻ったとき、前回の静的再取得から一定時間経っていれば `refreshStaticMatchData()` を呼ぶ。

```js
/* 第30弾: 可視に戻ったときの静的JSON再取得。
   スロットルが要る。タブを行き来するだけで毎回3リーグ×3ファイルを取りに行くと、
   標準クラス(club_extra.json 1.3MB / news.json 3.0MB)まで含めて通信量が跳ねる。
   5分という値は、バッチ(4時間おき)と live_watch(5分おき)のうち短い方に合わせたもの。 */
const STATIC_REFRESH_MIN_INTERVAL_MS = 5 * 60 * 1000;
let lastStaticRefreshAt = 0;

async function maybeRefreshStaticOnVisible() {
  const now = Date.now();
  if (now - lastStaticRefreshAt < STATIC_REFRESH_MIN_INTERVAL_MS) return;
  lastStaticRefreshAt = now;
  await refreshStaticMatchData();
}
```

`refreshStaticMatchData()` の中でも `lastStaticRefreshAt = Date.now()` を更新すること
（⟳を押した直後に画面を切り替えて戻っただけで再取得が走らないように）。

`visibilitychange` のハンドラを次のようにする:

```js
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    stopLivePolling();
  } else {
    evaluateLiveSchedule();
    maybeRefreshStaticOnVisible();   // 第30弾: 戻ってきたら静的JSONも見直す
    checkForNewDeploy();             // 第30弾: 3章。新しい版が出ていないか確認する
  }
});
```

`maybeRefreshStaticOnVisible()` は `await` しない（`visibilitychange` のハンドラを
`async` にして待たせる必要はない。取得が終わり次第 `renderActiveTab()` が走る）。
失敗しても例外を投げてはいけない — `getJsonOptional` 系の流儀に合わせ、
`.catch(() => {})` を付けて握りつぶす。**再取得の失敗でライブポーリングを巻き込まないこと。**

---

## 3. 【高】新しい版が出たことを検知して知らせる

### 現状

`index.html` 1379行付近:

```js
Promise.all([getTextOptional("deploy-time.txt"), getTextOptional("deploy-version.txt")]).then(([t, v]) => {
  state.deployTime = t;
  state.deployVersion = v;
  const badge = document.getElementById("deployBadge");
  if (badge) badge.textContent = [v ? "v" + v : "", t || ""].filter(Boolean).join(" ・ ");
});
```

起動時に1回取って表示するだけ。ポーリングも比較も通知も自動リロードも無い。

**このバッジは今、積極的に嘘をついている。** `deploy-version.txt` は `no-store` で
取りに行くので**常に最新**が返る。一方 `index.html` 本体はブラウザのナビゲーションで
取得されるため `fetch` のオプションが効かず、キャッシュから古いものが動きうる。
つまり「**古いHTMLが動いているのに、バッジには最新のハッシュが出る**」。
デバッグ用に置いた仕組みが、未反映の切り分けを妨げている。

原因は「いま動いているHTML自身のバージョン」がどこにも焼かれていないこと。

### やること

#### 3-1. ビルド時に index.html へバージョンを焼き込む

`index.html` の JS 冒頭（`const state = {` の直前あたり）に定数を置く:

```js
/* 第30弾: ビルド時に scripts/build_dist.py が dist/index.html の中だけを置換する。
   ソースの index.html にはプレースホルダのまま残るので、ローカルで直接開いたときは "dev" になる。
   dist/deploy-version.txt と同じビルドで同じ値が書かれるため、
   「焼き込まれた値 != deploy-version.txt」= ブラウザが古いHTMLを掴んでいる、と判定できる。 */
const APP_BUILD_VERSION = "__DEPLOY_VERSION__";
```

`scripts/build_dist.py` の `write_deploy_version()` を、`dist/index.html` の
プレースホルダ置換も行うように変える:

```python
def write_deploy_version() -> None:
    """
    dist/deploy-version.txt に直近のgitコミットの短縮ハッシュを書き、
    同じ値を dist/index.html の __DEPLOY_VERSION__ に埋める(第30弾)。

    このビルド自体はまだコミットされていないので、値は厳密には「1つ前のコミット」を指す。
    だがHTMLとtxtに同じビルドで同じ値を書くので、両者の比較は正しく機能する
    (絶対値ではなく、一致するかどうかしか見ていない)。
    gitが無い/コミットが1つも無い環境では黙ってスキップする(アプリは落とさない)。
    """
    ...（既存のgit rev-parse部分はそのまま）...
    version = out.stdout.strip()
    (DIST_DIR / "deploy-version.txt").write_text(version, encoding="utf-8")

    # dist/index.html にも同じ値を焼き込む。ソース側の index.html は書き換えない
    # (置換するのはコピー後の dist/ の方だけ。ソースを汚すとgitの差分が毎回出てしまう)。
    dist_index = DIST_DIR / "index.html"
    if dist_index.exists():
        html = dist_index.read_text(encoding="utf-8")
        if "__DEPLOY_VERSION__" not in html:
            print("[warn] index.html に __DEPLOY_VERSION__ が無い。"
                  "更新検知が働かなくなるので確認すること", file=sys.stderr)
        else:
            dist_index.write_text(html.replace("__DEPLOY_VERSION__", version), encoding="utf-8")
            print(f"[info] dist/index.html にバージョンを焼き込み: {version}")
    print(f"[info] デプロイバージョンを記録: {version}")
```

**プレースホルダが消えたことに気づけるよう、警告は必ず出すこと。**
静かに失敗すると、また「動いているつもりで動いていない」が増える。

#### 3-2. 比較して知らせる

```js
/* 第30弾: 配信されている版と、いま動いているHTMLの版を比べる。
   一致しなければブラウザ(またはPWA)が古いHTMLを掴んでいる。
   自動リロードはしない。試合を見ながら操作している最中に画面が飛ぶ方が困る。 */
async function checkForNewDeploy() {
  if (APP_BUILD_VERSION === "__DEPLOY_VERSION__") return; // ローカルで直接開いている
  const latest = await getTextOptional("deploy-version.txt");
  if (!latest || latest === APP_BUILD_VERSION) return;
  showUpdateBanner();
}
```

通知は既存の `showOndemandToast()`（3614行）を流用せず、**専用のバナーを出す**。
トーストは3.5秒で消えるので、見逃したら二度と出ない。
ヘッダー直下（`#ondemandToast` の隣、714〜728行のブロック）に置く:

```html
<div id="updateBanner" class="update-banner" hidden>
  新しいバージョンがあります
  <button type="button" id="updateReloadBtn">再読み込み</button>
</div>
```

```js
function showUpdateBanner() {
  const el = document.getElementById("updateBanner");
  if (el) el.hidden = false;
}
```

ボタンのハンドラ:

```js
document.getElementById("updateReloadBtn").addEventListener("click", () => {
  location.reload();
});
```

呼ぶタイミングは2か所だけ:

1. 起動時（既存のバッジ取得の直後）
2. 可視に戻ったとき（2章の `visibilitychange` ハンドラ内）

**定期ポーリングは足さない。** 可視復帰のたびに1回、数バイトのテキストを取るだけで足りる。

#### 3-3. バッジの表示も直す

焼き込まれた版と配信版が食い違っているときは、バッジにもそれが分かるようにする:

```js
if (badge) {
  const stale = APP_BUILD_VERSION !== "__DEPLOY_VERSION__" && v && v !== APP_BUILD_VERSION;
  badge.textContent = [v ? "v" + v : "", t || ""].filter(Boolean).join(" ・ ") + (stale ? " ⚠" : "");
}
```

---

## 4. 【高】Actionsのpush失敗が「成功」として終わる

### 現状

`.github/workflows/update.yml` 162〜170行（`match_events.yml` 101〜109行も同じ）:

```bash
for i in 1 2 3; do
  if git pull --rebase origin main && git push origin main; then
    echo "push成功 (試行$i回目)"
    break
  fi
  echo "push失敗、リトライします ($i/3)"
  sleep 5
done
```

**3回とも失敗するとループが自然終了し、`for` の終了コードは最後に実行された
`sleep 5` の 0 になる。** ステップは成功、ジョブは緑。実際に再現して確認済み:

```
fail 1 / fail 2 / fail 3
EXIT=0
```

コミットはランナーのローカルにしか存在せず、ジョブ終了と同時に**ランナーごと破棄される**。
GitHub の標準メール通知はジョブが `failure` になったときしか飛ばないので、
**この構成では永久に気づけない**。ローカルの `live_watch.py` は `return False` を返し、
さらに自動回復まで持っているのに、Actions側だけが無防備。

### やること

両方のワークフローで、ループの後に成否を検査する:

```bash
pushed=0
for i in 1 2 3; do
  if git pull --rebase origin main && git push origin main; then
    echo "push成功 (試行$i回目)"
    pushed=1
    break
  fi
  echo "push失敗、リトライします ($i/3)"
  sleep 5
done
# 第30弾: forループの終了コードは最後のコマンド(sleep)のものになるため、
# 何もしないと3回とも失敗しても緑で終わる。コミットはランナーごと破棄されるのに、
# 履歴上は成功に見える。必ず明示的に失敗させること。
if [ "$pushed" != "1" ]; then
  echo "::error::3回ともpushに失敗した。コミットは失われた"
  exit 1
fi
```

### あわせて直す（1行）

`.github/workflows/match_events.yml` 91行:

```bash
git add -A data/processed dist          # 現状
git add -A data/processed data/history dist   # 修正後
```

`build_dist.py` は `build_ics` 経由で `data/history/ics_state.json`（.ics の SEQUENCE 永続化用）を
書き換える。これがコミットされないと SEQUENCE が巻き戻り、
**カレンダー購読者のクライアントが新しい .ics を「古い版」とみなして無視する**。
`update.yml` 152行と `live_watch.py` 311行は既に正しいので、ここだけ揃える。

---

## 5. 【高】fetch_official.py が失敗クラブを黙って捨てて全書き換え

### 現状

`scripts/fetch_official.py` 288〜328行:

```python
out_clubs: dict[str, dict] = {}
failed: list[str] = []
for c in clubs:
    try:
        html = fetch_fn(c["slug"])
        out_clubs[c["idTeam"]] = parse_club_page(html)
    except Exception as e:
        log(f"[warn] {c['ja']}({c['slug']}) の取得に失敗: {e}", file=sys.stderr)
        failed.append(f"{c['idTeam']}({c['ja']})")
...
out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

問題が4つ重なっている:

1. **既存の `club_extra.json` を一切読まない。** 失敗したクラブは `out_clubs` に入らず、
   全体を上書きするので**前回取れていたデータが消える**。
   他のfetcherが全部持っている「既存とマージ」がここだけ無い。
2. **件数のサニティチェックが皆無。** 60クラブ全滅しても `{"clubs": {}}` を平然と書き出す。
3. **`main()` は必ず exit 0。** `update.yml` 99行の `continue-on-error: true` は
   「非ゼロ終了」を前提にしているので、**このガードは一度も作動しない**。
4. `fetch_club_html`（275〜282行）は**単発GET**。リトライもバックオフも429対応も無い。
   `fetch_utils.py` 109〜173行に実測ベースの優秀なリトライ実装があるのに、
   jleague.jp 経路からは使われていない。

`fetch_batch.py` の `check_not_shrunk`（165〜181行）や
`fetch_emperors_cup_events.py` の件数減少チェック（546〜548行）の系譜から、
**ここだけが漏れている**。

そして空になった `club_extra.json` は `git_diff_ignoring_timestamps.py` から見れば
立派な「実質的な変更」なので、そのままコミット・デプロイされる。さらに
`stats.py`（387〜394行）は club_extra が無いと公式指標を黙って落とすので、
**公式スタッツの節が無警告で `*_stats.json` からも消える**。

### やること

```python
# 第30弾: 失敗クラブの扱い。既存を読んで穴埋めし、失敗が多すぎるときは書かずに落ちる。
# 2026-08-31のレビューで、ここだけが「取得失敗 = 静かなデータ消失」になっていることが分かった。
MAX_FAILURE_RATIO = 0.2   # これを超えたら書き出さずに異常終了する


def load_existing_clubs() -> dict:
    path = PROCESSED_DIR / "club_extra.json"
    if not path.exists():
        return {}
    try:
        return (json.loads(path.read_text(encoding="utf-8")) or {}).get("clubs") or {}
    except (json.JSONDecodeError, OSError) as e:
        log(f"[warn] 既存のclub_extra.jsonを読めなかった。穴埋めなしで続行: {e}", file=sys.stderr)
        return {}
```

`build_club_extra()` の中で、失敗したクラブは既存の値を残す:

```python
    existing = load_existing_clubs()
    ...
    except Exception as e:
        log(f"[warn] {c['ja']}({c['slug']}) の取得に失敗: {e}", file=sys.stderr)
        failed.append(f"{c['idTeam']}({c['ja']})")
        old = existing.get(c["idTeam"])
        if old:
            out_clubs[c["idTeam"]] = old   # 前回のデータを維持する。空にするより古い方がまし
            log(f"[info] {c['ja']}: 前回のデータを維持した")
```

`main()` に閾値ガードを入れる:

```python
    out = build_club_extra(clubs)
    n_failed = len(out["meta"]["failed"])
    if n_failed > max(3, int(len(clubs) * MAX_FAILURE_RATIO)):
        print(f"[error] {n_failed}/{len(clubs)}クラブで取得に失敗した。"
              f"一時的な障害の可能性が高いので、書き出さずに終了する", file=sys.stderr)
        sys.exit(1)
```

**`sys.exit(1)` は「書き出さなかったとき」だけにすること。** 1〜2クラブが不調なだけで
毎回ジョブを赤くすると、赤が日常になって誰も見なくなる。

### 補足（余力があれば、この弾でなくてもよい）

`fetch_utils.py` のリトライ制御を `get_with_retry(url, ...)` として切り出し、
jleague.jp を叩く3経路（`fetch_official.py:275`、`fetch_match_events.py:98`、
天皇杯の `fetch_match_page`）から共有する。一過性の 429/503 と恒久的な 403 を
区別せず1回で諦めているのは、この3つだけ。

---

## 6. 【高】失敗しても誰にも通知が飛ばない

### 現状

`.github/workflows/update.yml` 138〜144行:

```yaml
- name: Optional steps summary
  if: always()
  run: |
    echo "fetch_news: ${{ steps.fetch_news.outcome }}"
    echo "fetch_official: ${{ steps.fetch_official.outcome }}"
    ...
```

4つの `continue-on-error` ステップの結果を**ログに echo するだけ**。
`outcome` が `failure` でもジョブは成功のまま終わる。
`if: failure()` のステップも通知アクションもIssue起票も、リポジトリ全体で**ひとつも無い**。

つまりニュース取得やクラブ情報が恒久的に壊れても、
Actions は4時間おきに緑を出し続け、誰も気づかない。

### やること

外部サービスは使わない。**GitHubの標準通知（ジョブが赤くなるとメールが飛ぶ）に乗せる**のが
いちばん確実で、追加の秘密情報も要らない。

サマリのステップを **`Commit and push` の後ろに移動**し、失敗を集計してジョブを落とす:

```yaml
      # 第30弾: 任意ステップの失敗を可視化する。
      # Commit and push の「後ろ」に置くのが重要。ここでジョブを落としても、
      # 取得できたぶんのデータは既にコミット・push済みなので失われない。
      # GitHubの標準通知はジョブがfailureのときしか飛ばないので、echoするだけでは誰も気づけない。
      - name: Optional steps summary
        if: always()
        run: |
          FAILED=""
          for s in "fetch_news:${{ steps.fetch_news.outcome }}" \
                   "fetch_official:${{ steps.fetch_official.outcome }}" \
                   "fetch_emperors_cup:${{ steps.fetch_emperors_cup.outcome }}" \
                   "fetch_emperors_cup_events:${{ steps.fetch_emperors_cup_events.outcome }}"; do
            echo "$s"
            case "$s" in *:failure) FAILED="$FAILED ${s%%:*}";; esac
          done
          {
            echo "### 任意ステップの結果"
            if [ -n "$FAILED" ]; then echo "- 失敗:$FAILED"; else echo "- すべて成功"; fi
          } >> "$GITHUB_STEP_SUMMARY"
          if [ -n "$FAILED" ]; then
            echo "::error::任意ステップが失敗した:$FAILED"
            exit 1
          fi
```

**ステップの並び順を必ず確認すること。** サマリが `Commit and push` より前にあると、
ジョブが落ちてコミットに到達せず、取得できたデータまで捨ててしまう。順序が逆転すると
この章は「未反映を直すつもりで未反映を増やす」変更になる。

---

## 7. 確認手順

### 1〜3章（アプリ側）

1. `python scripts/build_dist.py` を実行し、`dist/index.html` に
   `__DEPLOY_VERSION__` が**残っていない**ことを確認する:
   `Select-String -Path dist\index.html -Pattern "__DEPLOY_VERSION__"` が0件。
2. `dist/index.html` に焼かれた値と `dist/deploy-version.txt` の中身が**一致**すること。
3. `dist/` をローカルサーバーで開く（`python -m http.server` などで
   `file://` では開かない。JSONの読み込みが拒否される）。
4. 順位表タブを開く → 別プロセスで `data/processed/j1_standings.json` を書き換えて
   `build_dist.py` を再実行 → アプリに戻って**⟳を押す** → 変更が反映されること。
   （修正前は反映されない。これが1章の回帰テスト）
5. 同じ状態で、⟳を押さずに**タブを別アプリに切り替えて5分後に戻る** → 反映されること（2章）。
6. `dist/deploy-version.txt` を手で書き換える → アプリに戻る →
   「新しいバージョンがあります」バナーが出て、押すとリロードされること（3章）。
7. `index.html` を直接 `file://` で開いたときに、バナーが**出ない**こと
   （`APP_BUILD_VERSION === "__DEPLOY_VERSION__"` の早期リターン）。

### 4〜6章（サーバー側）

8. push失敗の検査は、ローカルで挙動だけ確かめられる:
   `bash -c 'for i in 1 2 3; do if false; then break; fi; sleep 0; done; echo $?'` が
   `0` を返すことを確認 → 修正後の形なら `exit 1` に到達すること。
9. `python scripts/fetch_official.py` を、`fetch_fn` を全件例外にするテストで実行し、
   **`club_extra.json` が書き換わっていない**こと・終了コードが1であることを確認する
   （`scripts/test_fetch_official.py` にケースを足す）。
10. 1クラブだけ失敗させたときは、そのクラブが**前回のデータのまま残り**、
    終了コードが0であることを確認する。
11. ワークフローは `workflow_dispatch` で1回手動実行し、
    サマリが `Commit and push` の**後**に出ていることをログの並びで確認する。

---

## 8. 変更の入れ方（コミット単位）

1. `feat(app): 静的JSONのキャッシュを⟳と可視復帰で全部読み直す`（1章＋2章）
2. `feat(app): 新しいデプロイを検知して再読み込みを促す`（3章、`build_dist.py` 含む）
3. `fix(ci): pushに3回失敗したらジョブを失敗させる / match_eventsもdata/historyをadd`（4章）
4. `fix(fetch_official): 失敗クラブは前回値を維持し、失敗が多いときは書かずに落ちる`（5章）
5. `fix(ci): 任意ステップの失敗でジョブを赤くする`（6章）

1と2を入れたら**一度デプロイして実機で確かめる**こと。3〜5はサーバー側なので、
アプリ側の効果を確認してから入れた方が切り分けが楽。

---

## 9. やらないこと（この弾のスコープ外）

同じレビューで見つかったが、今回は入れない10件。**次弾以降の候補として残す**。

| 内容 | 場所 | なぜ今回やらないか |
|---|---|---|
| `render.yaml` を追加して `index.html` に `Cache-Control: no-cache` を明示 | リポジトリ直下（現在ファイル自体が無い） | 3章の更新検知が入れば実害は出なくなる。ヘッダー整備は別途まとめてやる価値がある |
| CI の変更判定に `index.html` を含める | `update.yml:125-136` | 手元の deploy コマンドが `build_dist.py` を回しているので現状は表面化していない |
| `build_dist.py` に「index.html に未コミット変更があれば中止」ガード | `build_dist.py` | 編集中に `live_watch` が発火すると書きかけのHTMLが配信されうる。頻度は低い |
| 差分判定スクリプトの終了コードを3値化（0=変更あり/1=変更なし/2=判定不能） | `git_diff_match_events.py:47-53` ほか | Pythonの未捕捉例外も1なので「異常」が「変更なし」と同義になっている。設計変更なので単独の弾にする |
| `VOLATILE_KEYS` から `at` / `runs` / `fetchedAt` を外す | `git_diff_ignoring_timestamps.py:34` | 短く汎用的な語をキー名だけで全階層から除去していて危険 |
| `fetchState.attempts` を差分判定から除外しない | 同上 | `attempts` は天皇杯の打ち切り判定を駆動する実質的な状態。除外するとコミットされず、上限もクールダウンも永久に発動しない |
| フル同期の判定を「時刻の一致」から「前回からの経過日数」に | `update.yml:46-56` | scheduleが遅延すると `HOUR=16` を外して丸ごと飛ぶ。`meta.json` に `lastFullSyncAtJst` を持たせる設計が要る |
| フル同期時の `timeout-minutes` を上げる | `update.yml:33` | 114リクエスト×2.5秒＋429待機で20分を超えうる |
| 試合詳細の退行ガードを種別ごとにする | `fetch_match_events.py:359-369` | いまは goals+cards+subs の合計でしか見ておらず、goalsだけ空振りすると通過してしまう |
| `fetch_batch.py` の部分成功をコミットする経路 | `fetch_batch.py:456-458` | 1リーグ失敗で3リーグ分が捨てられる。終了コードの設計変更が要る |

### 参考: リポジトリの公開設定について

`update.yml` 10行目に「リポジトリがprivateなのでActionsの無料枠は月2000分」とあるが、
**このリポジトリは実際には public**（認証なしで clone できることを 2026-08-31 に確認）。
publicリポジトリのActionsは無料枠が無制限なので、増分取得にこだわる前提が崩れている。
上表の「フル同期の判定」を直すときに、頻度を上げる選択肢も一緒に検討するとよい。
（なお public である以上、GitHubトークンをアプリに埋め込まない判断は引き続き正しい。）
