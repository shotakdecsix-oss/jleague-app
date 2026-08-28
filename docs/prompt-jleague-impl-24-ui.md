# 第24弾 実装指示：UI/UXブラッシュアップ(静的レビュー由来)

対象: **`index.html` のみ**(`<style>`ブロックと、テーマ関連の数関数)。
`scripts/` 配下、`.github/workflows/`、`data/` のスキーマは**一切変更しない**。

前提ドキュメント: `docs/handoff-jleague-dashboard.md`

---

## 0. この指示書の性格と、守ってほしい制約

この弾は**新機能をひとつも足さない**。第23弾までに出来上がった画面の、
「特定のクラブを選ぶと文字が読めない」「押した感がない」といった**作りの粗を潰すだけ**。

- **表示ロジックとデータフローを変えない。** 出す情報・並び順・タブ構成・データの取り方は現状のまま。
- **外部ライブラリを追加しない。** 単一HTMLで自己完結する構成を崩さない。
- **`index.html` を1文字でも変えたら、最後に必ず `python scripts/build_dist.py` を実行する。**
  Renderが配信しているのは `dist/` であって `index.html` ではない。実行を忘れると
  「デプロイは成功しているのに画面が変わらない」事故になる(2026-08-27に発生済み)。
- 優先度 **A → B → C → D** の順に入れる。AだけでもBだけでも独立して意味がある。
  **E(ダークモード)は分量が別格なので、無理に同じ弾でやらなくてよい。**

---

## 1. 優先度A: 実際に読めない・ズレている(バグ扱い)

ここは好みの問題ではない。**特定のクラブを選ぶと文字が読めなくなる**という不具合。

### 1-1. クラブカラーの上に載せる文字色の判定しきい値が誤っている(最重要)

現状 `applyTheme()`:

```js
root.setProperty("--accent-ink", luminance(c) > 0.5 ? "#111111" : "#ffffff");
root.setProperty("--accent-sub-ink", luminance(sub) > 0.5 ? "#111111" : "#ffffff");
```

`0.5` というしきい値が**大きすぎる**。白文字との比 `1.05/(L+0.05)` と黒文字との比 `(L+0.05)/0.05`
が入れ替わるのは **L ≈ 0.179** であって 0.5 ではない。結果、**中くらいの明るさの色に対して
白文字を選びすぎている**。

`data/masters/*.json` の全60クラブ × `color`/`colorSub` = 120色を実際に計算したところ、
**51色が現状コントラスト比 4.5:1 未満**だった。最悪ケース:

| クラブ | 色 | 現状(白文字) | 反対色なら |
|---|---|---|---|
| 湘南 `color` | `#82c039` | **2.20:1** | 9.55:1 |
| 愛媛 `color` | `#f39800` | 2.26:1 | 9.30:1 |
| 鳥取 `color` | `#6eba3d` | 2.40:1 | 8.75:1 |
| 清水 `color` | `#f18900` | 2.52:1 | 8.32:1 |
| 讃岐 `color` | `#65aadd` | 2.52:1 | 8.35:1 |

湘南を選ぶと、ヘッダーのタイトル・各セクションの `h2` 見出し・フッターが
**淡い黄緑の地に白文字**になる。屋外のスマホではまず読めない。

**変更後** — しきい値の数字を差し替えるのではなく、両方のコントラスト比を実際に比べて選ぶ。
`luminance()` の直後あたりに追加:

```js
/* 相対輝度 l1,l2 のコントラスト比(WCAG 2.x)。 */
function contrastRatio(l1, l2) {
  const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}
/* 背景hexの上に載せる文字色を、白/黒のうちコントラスト比が高い方から選ぶ。
   旧実装は luminance>0.5 の固定しきい値だったが、これは中間輝度の色で白を選びすぎていた
   (湘南 #82c039 で 2.2:1)。暗い側に純黒を使うのは、#111111 だと大分のcolorSub #6079b6 が
   4.41:1 とAAの4.5:1にわずかに届かないため。目視で #111111 との差は分からない。 */
const INK_ON_LIGHT = "#000000", INK_ON_DARK = "#ffffff";
function pickInk(hex) {
  const L = luminance(hex);
  return contrastRatio(L, 0) >= contrastRatio(L, 1) ? INK_ON_LIGHT : INK_ON_DARK;
}
```

`applyTheme()` の該当2行を置き換える:

```js
root.setProperty("--accent-ink", pickInk(c));
root.setProperty("--accent-sub-ink", pickInk(sub));
```

**検証済みの期待値**: この変更で120色すべてがコントラスト比 **4.64:1 以上**になる(計算で確認済み)。

### 1-2. `.tag.home` だけ文字色が白固定になっている

```css
.tag.home{background:var(--accent); color:#fff}
```

ホームタブ「次戦」カードの HOME バッジ。背景はクラブカラーなのに文字だけ白固定なので、
**柏・北九州・栃木SC(`#fff100`)を選ぶと白地に白文字で完全に消える**(1.18:1)。
千葉 `#ffe100`(1.31:1)、仙台 `#fcc800`(1.57:1) も同様。

**変更後**:

```css
.tag.home{background:var(--accent); color:var(--accent-ink)}
```

`.pill`(勝敗)と `.league-badge`(J1/J2/J3)の `color:#fff` は背景が固定色なのでこのままでよい。
**触るのは `.tag.home` の1箇所だけ。**

### 1-3. 白い背景の上でクラブ1stカラーを文字色に使っている

白背景 `#fff` の上で `color:var(--accent)` を使っている箇所:

| 行(現状) | セレクタ | 用途 |
|---|---|---|
| 60 | `.mode-toggle button.active` | ヘッダーのクラブ/全体切替(選択中) |
| 176 | `a.btn` | カレンダー配信などのボタン文字 |
| 224 | `td.stat-rank-top` | スタッツでリーグ上位の数値 |
| 239 | `details > summary` | シーズン別成績の開閉ラベル |
| 484 | `.lineups-col.me .lineups-team` | 出場メンバーの自クラブ名 |

**60クラブ中20クラブで、この文字が白背景に対して4.5:1未満**になる。最悪は
柏・北九州・栃木SC の `#fff100` で **1.18:1**(白地に黄色＝ほぼ見えない)、
千葉 `#ffe100` 1.31:1、仙台 `#fcc800` 1.57:1、FC大阪 `#7fcaf1` 1.81:1、湘南 `#82c039` 2.20:1。

**変更後(推奨案)** — 「白背景の上で読める明るさまで暗くしたクラブ色」を別の変数として持つ。
既存の `darken()` は `rgb(...)` 文字列を返して再計算に使えないので、hexを返す版を足す。

```js
/* darken()と同じ考え方だが、繰り返し適用できるようhexで返す。 */
function darkenHex(hex, factor) {
  const [r, g, b] = hexToRgb(hex);
  const f = v => Math.min(255, Math.round(v * factor));
  return "#" + [f(r), f(g), f(b)].map(v => v.toString(16).padStart(2, "0")).join("");
}
/* 白背景の上で本文として読める(4.5:1)明るさになるまで、必要な回数だけ暗くしたクラブ色。
   クラブの色味は保ったまま暗くなるので、固定色に逃がすよりブランド感が残る。 */
function accentOnLight(hex) {
  let c = hex;
  for (let i = 0; i < 14 && contrastRatio(luminance(c), 1) < 4.5; i++) c = darkenHex(c, 0.85);
  return c;
}
```

`applyTheme()` に1行足す:

```js
root.setProperty("--accent-on-light", accentOnLight(c));
```

そのうえで、上表の5箇所の `var(--accent)` を **`var(--accent-on-light)`** に差し替える。
`.events-toggle.open{color:var(--accent)}` と `#tabbar button.active{border-bottom-color:var(--accent)}`
は**文字ではなく装飾**なので、ここでは触らない(後者は 1-5 で別途扱う)。

変換後の実例(計算済み):

| 元の色 | 変換後 | 比 |
|---|---|---|
| `#fff100`(柏) | `#716b00` | 5.52:1 |
| `#ffe100`(千葉) | `#857500` | 4.62:1 |
| `#82c039`(湘南) | `#507623` | 5.31:1 |
| `#f39800`(愛媛) | `#965e00` | 5.39:1 |
| `#e6002d`(浦和) | 変換なし | 4.77:1 |
| `#0b6b3a`(既定) | 変換なし | 6.61:1 |

もともと十分暗いクラブは1回も暗くならないので、**大多数のクラブでは見た目が変わらない**。

> 代案(実装は軽いがクラブ感が薄れる): 上記5箇所を `var(--ink)` / `var(--sub)` の固定色にする。
> 「アプリのアクセント色」という意図を捨てることになるので、**推奨案を採る**こと。

### 1-4. 予想タブの目盛りラベルが帯とズレている

```css
.predict-row{display:grid; grid-template-columns:20px 44px 1fr 32px; align-items:center; gap:8px; ...}
.predict-axis{display:flex; justify-content:space-between; font-size:10px; color:var(--sub); margin:4px 64px 0 64px}
```

帯(`.predict-track`)は3列目なので、行の左端から **20 + 8 + 44 + 8 = 80px** の位置から始まり、
右端から **32 + 8 = 40px** の位置で終わる。ところが軸ラベルは左右とも 64px。
**「1位」が帯の始点より16px左、「20位」が帯の終点より24px右**に出ていて、目盛りとして機能していない。

**変更後**:

```css
.predict-axis{display:flex; justify-content:space-between; font-size:10px; color:var(--sub); margin:4px 40px 0 80px}
```

グリッドの列幅を将来変えたときに再びズレるので、**この4つの数字は `.predict-row` の
`grid-template-columns` と `gap` から来ている**とコメントを添えておくこと。

### 1-5. アクティブなタブの下線が見えないクラブがある

```css
#tabbar{background:var(--accent-sub)}
#tabbar button.active{opacity:1; border-bottom-color:var(--accent)}
```

タブバーの地色は2ndカラー、選択中の下線は1stカラー。この2色が近いクラブでは下線がほぼ見えない:
**浦和(`#e6002d`/`#ef5977`, 1.45:1)、名古屋(同, 1.45:1)、福島(`#e60012`/`#ef5965`, 1.44:1)**。

**変更後** — 下線を、同じ地色の上で必ず読める色(＝タブ文字と同じ `--accent-sub-ink`)にする。
選択中のタブは `opacity:1` なので、文字と下線の明度が揃って「選択中」がはっきりする。

```css
#tabbar button.active{opacity:1; border-bottom-color:var(--accent-sub-ink)}
```

---

## 2. 優先度B: スマホでの操作性

### 2-1. タップしたときの反応が一切ない

`index.html` 全体で **`:active` の指定がゼロ**(`:hover` は4箇所あるが、指で触る端末では発火しない)。
タブ・チップ・行を押しても押した瞬間に何も変わらないので、反応が遅いときに
「効いていないのでは」と二度押しされる。第10弾のオンデマンド更新のような
「1回しか押してほしくないボタン」で特に困る。

**変更後** — `<style>` の末尾にまとめて追加する。

```css
/* 押した瞬間の反応。指の端末では:hoverが効かないので、押下状態だけは必ず返す。
   transformではなくopacityにしているのは、position:absoluteの子(帯・トースト)を持つ
   要素にtransformを掛けると座標基準がずれるため。 */
button:active, a.btn:active, .btn-secondary:active, .cal-loadmore:active,
.predict-row:active, .sched-row.has-events:active, .cal-row.has-events:active,
.ec-row.has-events:active, .team-link:active{
  opacity:.6;
}
/* iOS/Androidの既定のタップ時グレー矩形は、上の指定と二重にちらつくので消す */
button, a, .predict-row, .sched-row, .cal-row, .ec-row, .team-link{
  -webkit-tap-highlight-color:transparent;
}
```

### 2-2. タップ領域が小さい

指のタップ領域は44px四方が目安(Apple HIG)。現状の実寸(padding + line-height から計算):

| 要素 | 現状の高さ | 備考 |
|---|---|---|
| `.ondemand-info-btn` | **26px** | 最小。ヘッダーの「i」ボタン |
| `.chart-chip` | 31px | 確率推移のクラブ選択チップ。横に密集 |
| `.ec-rounds button` | 34px | 天皇杯のラウンド切替 |
| `.mode-toggle button` | 35px | ヘッダーのクラブ/全体 |
| `.seg-toggle button` / `.stats-subtabs button` / `.chart-series-tabs button` / `.cal-league-filter button` | 35px | |
| `.btn-secondary` / `.cal-loadmore` | 42px | 惜しい |
| `#tabbar button` | 約44px | **足りている。触らない** |
| `a.btn` | 52px | 足りている |

**変更方針を2つに分ける。**

**(a) 横幅のある帯状のボタンは、実寸で 44px を下限にする。**
高さが変わるだけでレイアウトは崩れない。

```css
/* 指のタップ領域の下限(44px, Apple HIG)。paddingを増やすのではなくmin-heightで
   下限だけ決め、中身はflexで中央に置く。既に44pxを超えている#tabbar/a.btnは対象外。 */
.seg-toggle button, .stats-subtabs button, .chart-series-tabs button,
.cal-league-filter button, .ec-rounds button, .chart-chip,
.btn-secondary, .cal-loadmore{
  min-height:44px; display:inline-flex; align-items:center; justify-content:center;
}
.btn-secondary, .cal-loadmore{display:flex}  /* 幅100%のものはblock相当のflexにする */
```

`.chart-chip` は横に密集するので、`min-height` だけ上げると隣同士の間隔が相対的に詰まって見える。
`.chart-chips{gap:6px}` → `gap:8px` に緩める。

**(b) ヘッダー内の2つは、見た目を変えずに当たり判定だけ広げる。**
ヘッダーは `position:sticky` で常に画面上部を占有しているため、実寸を増やすと
**本文の見える面積がそのぶん恒久的に減る**。ここは疑似要素で判定だけ拡張する。

```css
/* 見た目は26px/35pxのまま、透明な当たり判定だけ44pxに広げる。
   ヘッダーはstickyで常時表示なので、実寸を増やすと本文の可視領域が恒久的に減る。 */
.ondemand-info-btn, .mode-toggle button{position:relative}
.ondemand-info-btn::before, .mode-toggle button::before{
  content:""; position:absolute; top:50%; left:50%;
  min-width:44px; min-height:44px; width:100%; height:100%;
  transform:translate(-50%,-50%);
}
```

行そのものがタップできる `.predict-row`(30px) / `.cal-row`(34px) / `.sched-row`(36px) は、
**横幅が画面いっぱいあるので誤タップしにくい**。ここを44pxにすると1画面に入る試合数が
目に見えて減るので、**今回は触らない**。

### 2-3. iOSで日程タブのセレクトを触ると画面がズームする

```css
.schedule-controls select{width:auto; flex:1; min-width:110px; padding:8px 10px; font-size:13px; font-weight:600}
```

iOS Safari は **font-size が16px未満の `select`/`input` にフォーカスすると自動でズームする**。
汎用の `select{font-size:16px}` はその対策として正しく効いているのに、
日程タブの節ジャンプ(`#roundJump`)だけ 13px で上書きしていて、**ここだけ触るたびに画面が拡大する**
(拡大は自動で戻らないので、ユーザーが手でピンチして戻すことになる)。

**変更後**:

```css
/* font-sizeは16px未満にしないこと(iOS Safariがフォーカス時に自動ズームし、自動で戻らない)。
   見た目を詰めたい場合はpaddingで調整する。 */
.schedule-controls select{width:auto; flex:1; min-width:110px; padding:8px 10px; font-size:16px; font-weight:600}
```

`.header-row2 select` は padding だけの上書きで font-size は16pxを継承しているので、**そのままでよい**。

### 2-4. `viewport-fit=cover` を宣言しているのに safe-area を使っていない

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
```

`viewport-fit=cover` は「切り欠き(ノッチ)やホームインジケータの下まで描画する」宣言。
にもかかわらず `env(safe-area-inset-*)` が**1箇所も使われていない**。実機での症状:

- iPhoneを**横向き**にすると、ヘッダーのタイトル・クラブ選択・タブバーの端が切り欠きに隠れる
- 縦向きでも、最下部のフッターやカードがホームインジケータのバーと重なる

**変更後** — 4箇所。

```css
body{ ...既存... ; padding-bottom:calc(24px + env(safe-area-inset-bottom)) }
header{ padding:12px calc(14px + env(safe-area-inset-right)) 12px calc(14px + env(safe-area-inset-left)) }
#tabbar{ padding-left:env(safe-area-inset-left); padding-right:env(safe-area-inset-right) }
main{ padding:16px calc(16px + env(safe-area-inset-right)) 16px calc(16px + env(safe-area-inset-left)); max-width:640px; margin:0 auto }
```

ヘッダー内にオーバーレイ表示される2つも、`header` の内側ではなく `header` を基準にした
`position:absolute` なので、個別に効かせる:

```css
.ondemand-toast, .ondemand-popover{
  left:calc(12px + env(safe-area-inset-left)); right:calc(12px + env(safe-area-inset-right));
}
```

`env()` は非対応ブラウザでは0扱いになる(`calc` ごと無効にはならない)ので、
Androidや PC Chrome での見た目は変わらない。

---

## 3. 優先度C: アクセシビリティ

### 3-1. キーボードフォーカスがどこにあるか分からない

`:focus-visible` の指定が**ゼロ**。ブラウザ既定のフォーカスリングは、
`button{border:none; background:none}` を当てている要素では見えないことが多い。
PCで Tab キーで辿ると、現在位置が分からないまま進むことになる。

**変更後** — クラブカラーに依存しない固定色にする(クラブ色にすると、
そのクラブ色の背景の上でフォーカスリングが消える)。

```css
/* フォーカスリングはクラブカラーに依存させない。クラブ色にすると、その色の背景の上で消える。
   :focus-visibleなので、マウス/タップでの押下時には出ない。 */
button:focus-visible, select:focus-visible, a:focus-visible, summary:focus-visible{
  outline:3px solid #2563eb; outline-offset:2px; border-radius:6px;
}
```

### 3-2. ライブの点滅が止められない

```css
.live-dot{ ... animation:live-pulse 1.4s ease-in-out infinite }
```

`prefers-reduced-motion` 未対応。試合中は**画面上で赤い点が延々と点滅し続ける**。
OSで「視差効果を減らす」を有効にしている利用者には、これを止める手段がない。

**変更後** — 点滅は止めるが、**赤い点そのものは残す**。
点の有無が「ライブ中かどうか」を伝える唯一の手掛かりなので、`display:none` にはしないこと。

```css
@media (prefers-reduced-motion: reduce){
  .live-dot{animation:none}          /* 赤点は残す。点滅だけ止める */
  .events-panel{animation:none}
  .events-toggle{transition:none}
}
```

### 3-3. タブバーが「タブ」だと支援技術に伝わっていない

現状 `<nav id="tabbar">` の中に素の `<button>` が並ぶだけで、
どれが選択中かは `class="active"` という**見た目の情報でしか表現されていない**。

**変更後** — `renderTabButtons()` と `updateTabBarActive()` に属性を1つずつ足す。

```js
function renderTabButtons() {
  const bar = document.getElementById("tabbar");
  bar.innerHTML = currentTabList().map(t =>
    '<button type="button" data-tab="' + t.id + '" aria-current="false">' + esc(t.label) + '</button>'
  ).join("");
}
function updateTabBarActive() {
  const bar = document.getElementById("tabbar");
  const active = currentActiveTabId();
  for (const btn of bar.querySelectorAll("button[data-tab]")) {
    const on = btn.dataset.tab === active;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-current", on ? "page" : "false");
  }
}
```

`#modeToggle` の2つのボタンにも `updateModeToggleActive()` で同様に `aria-current` を入れる。

> **`role="tablist"` / `role="tab"` は付けないこと。** その role を名乗ると
> 支援技術は「左右矢印キーでタブを移動できる」ことを期待するが、その実装は今ここにない。
> 中途半端に名乗るより、`aria-current` だけの方が実態に合っている。

---

## 4. 優先度D: 体感速度

### 4-1. 起動時のマスター取得が直列になっている

`init()` の冒頭:

```js
for (const key of Object.keys(LEAGUES)) {
  state.masters[key] = await getJson(LEAGUES[key].master);
}
```

J1 → J2 → J3 の**3ファイルを1本ずつ順番に**待っている。互いに依存していないので並列でよい。
最初の画面が出るまでの時間が、実質 3往復から1往復ぶんに縮む
(モバイル回線のレイテンシが1往復100〜200msなら、200〜400msの短縮)。

**変更後**:

```js
const leagueKeys = Object.keys(LEAGUES);
const masterList = await Promise.all(leagueKeys.map(k => getJson(LEAGUES[k].master)));
leagueKeys.forEach((k, i) => { state.masters[k] = masterList[i]; });
```

`catch` に入る条件も、その後の `state.teamsById` を組み立てるループも**変えない**
(`Promise.all` はどれか1つが失敗すれば reject するので、既存の `try/catch` がそのまま機能する)。

### 4-2. タブ切替中の表示が文字1行しかない

```js
app.innerHTML = '<p class="muted">読み込み中…</p>';
```

一瞬で `<p>` 1行になり、直後に本来の高さに戻るので、**タブを切り替えるたびに画面がガタつく**。
`renderActiveTab()` と `renderAllModeActiveTab()` の2箇所とも同じ。

**変更後(任意)** — 中身の分からない骨組みでよいので、**高さのある箱**を出す。

```css
.skeleton{background:#fff; border:2px solid var(--accent-sub); border-radius:var(--radius); padding:16px}
.skeleton-line{height:12px; border-radius:6px; background:#eceff3; margin-bottom:10px}
.skeleton-line:last-child{margin-bottom:0; width:60%}
@media (prefers-reduced-motion: no-preference){
  .skeleton-line{animation:skeleton-pulse 1.2s ease-in-out infinite}
  @keyframes skeleton-pulse{0%,100%{opacity:1} 50%{opacity:.5}}
}
```

```js
/* タブ切替中のつなぎ。高さのある箱を出すことで、本来の中身が入ったときの
   レイアウトの飛びを抑える。中身の正確な形を模す必要はない。 */
const LOADING_HTML =
  '<section><div class="skeleton" aria-busy="true" aria-label="読み込み中">' +
  '<div class="skeleton-line"></div><div class="skeleton-line"></div>' +
  '<div class="skeleton-line"></div><div class="skeleton-line"></div>' +
  '</div></section>';
```

`app.innerHTML = '<p class="muted">読み込み中…</p>';` の2箇所を `app.innerHTML = LOADING_HTML;` に置換。

> 優先度は低い。1-1〜1-5 と 2-1〜2-4 を入れ終えてから着手すること。

---

## 5. 優先度E: ダークモード対応(別弾に切り出してよい)

`prefers-color-scheme` の指定が**ゼロ**。夜、暗い部屋でスマホを見たときに
**画面全体がクラブカラーで明るく光る**。「試合前後にベッドで結果を見る」用途では効く改善だが、
**分量はA〜Dの合計より大きい**。無理に同じ弾に押し込まず、別弾にしてよい。

着手する場合、以下は**先に設計を決めてから**手を動かすこと。

1. **クラブカラーのベタ塗りをどうするか。**
   現状 `body` と `header` はクラブ1stカラーのベタ塗りで、これがこのアプリの見た目の核。
   ダークモードでこれを消すとアプリの個性が失われ、残すと暗い部屋では明るすぎる。
   **推奨: 残したうえで、ダーク時だけ `darkenHex(c, 0.55)` 程度に落とした色を使う**
   (1-3 で足す `darkenHex()` がそのまま使える)。`--accent-ink` の判定は 1-1 の `pickInk()` が
   暗くなった色に対しても正しく効くので、追加の分岐は要らない。

2. **`:root` の変数だけで済まない箇所を洗い出す。** 現状ハードコードされている白/淡色:
   - `.card{background:#fff}`、`.mode-toggle button.active{background:#fff}`
   - `select{background:#fff}` と、その中の **`background-image` に埋め込まれた矢印SVG**
     (`stroke='%236b7280'`。暗い背景では見えないので、ダーク用にもう1つ定義が要る)
   - `.promo-cell` / `.promo-note-emphasis` / `.predict-detail` / `.events-row` の `#f7f8fa` 系
   - `.predict-track{background:#f0f1f3}`、`.news-thumb{background:#f0f1f3}`
   - `.predict-med` / `.predict-cur` の `#fff`(帯の上の目印。地の色が変わると見えなくなる)
   - `.live-card{background:#fff7f7}`、`.cal-row.live{background:#fff7ed}`、
     `.provisional-badge{background:#fef3c7}`、`.ondemand-basis{background:#fef3c7}`、
     `.err{background:#fff2f0}` — **意味を持つ色**(ライブ/暫定/エラー)なので、
     単に暗くするのではなく暗い地の上で同じ意味に読める配色を決め直すこと
   - `.highlight-video-frame{background:#000}` は変更不要

3. **`--me-bg` の作り方を変える。**
   `applyOwnClubAccent()` の `mixWhite(c, 0.14)` は「クラブ色を白で薄める」実装なので、
   ダークでは自クラブ行が**白く光る帯**になる。ダーク時は白ではなく地の色と混ぜる
   (`darkenHex` 側で作る)実装が要る。

4. **`meta[name="theme-color"]`** も `applyTheme()` で書き換えているので、ダーク時の色に追随させる。

5. **手動切替を付けるかどうか。** OS設定への追随だけで足りるか、ヘッダーに切替を置くかは
   別途決める。置く場合、状態は `localStorage`(既存の `STORE_KEY` と同じ流儀)に保存する。

---

## 6. ついでに直してよい小さな瑕疵

- **空のCSSルール**: `ul.results li{ }` は中身が無い。削除する。
- **`h2{text-transform:uppercase}`**: 中身が日本語なので**何の効果もない**(`letter-spacing` だけが効いている)。
  英字が入る予定がないなら削除してよい。
- **`.cal-row.me` の `box-shadow:inset 3px 0 0 var(--me-accent)`**:
  `--me-accent` は `applyOwnClubAccent()` が呼ばれたときだけ定義され、
  `applyThemeForMode()` は `if (found)` の中でしか呼んでいない。
  クラブ未選択の状態では変数が未定義になり、**`box-shadow` プロパティ全体が無効**になる
  (フォールバック無しの `var()` は、その宣言ごと落ちる)。
  `.sched-row.me` の `var(--me-bg,#eaf6ef)` と同じように、
  **`var(--me-accent, var(--accent))` とフォールバックを書く**。`.ec-row.me` も同じ。

---

## 7. やらないこと(この弾のスコープ外)

- データ取得スクリプト(`scripts/`)、workflow、JSONのスキーマの変更
- タブ構成・情報設計の変更(どのタブに何を置くか)
- 新しい可視化・新しいデータ源の追加
- CSSフレームワークやアイコンフォントの導入(単一HTML自己完結を維持)
- `#tabbar button` と `a.btn` のサイズ変更(既に44pxを満たしている)
- タップ可能な**行**(`.predict-row` / `.cal-row` / `.sched-row`)の高さ変更(2-2 の理由)

---

## 8. 確認手順

1. `python -m http.server 8000` を起動し `http://localhost:8000/` を開く
   (ファイルを直接開くとJSONの読み込みがブロックされる)。
2. **クラブを切り替えながら目視**。最低この4クラブは必ず見ること:
   - **柏(`#fff100`)** — ヘッダー文字が黒に変わること。ホームタブの `HOME` バッジが読めること。
     「シーズン別成績」の開閉ラベルとカレンダー配信ボタンの文字が読めること(1-1/1-2/1-3)
   - **湘南(`#82c039`)** — `h2` 見出しとフッターが黒文字になっていること(1-1)
   - **浦和(`#e6002d`)** — タブバーの選択中の下線が見えること(1-5)
   - **既定の緑(`#0b6b3a`)相当のクラブ** — 変更前と見た目が変わっていないこと(退行の確認)
3. **全体モード → 予想タブ**で、「1位」「10位」「20位」のラベルが帯の左端・中央・右端に
   揃っていること(1-4)。J1/J2/J3すべてで確認する(クラブ数が違う)。
4. **日程タブの節ジャンプ**をタップして、**画面が拡大しないこと**(2-3)。iOS実機でのみ再現する。
5. **タブ・チップを押した瞬間に薄くなること**(2-1)。二度押ししたくなる遅さが消えているか。
6. PCで **Tabキー**を押していき、フォーカス位置が青い枠で分かること(3-1)。
7. **iPhoneを横向き**にして、タイトル・タブバーの端が切り欠きに隠れないこと(2-4)。
8. DevToolsで **Rendering → Emulate `prefers-reduced-motion: reduce`** にして、
   ライブ中の赤い点が**点滅を止めつつ残っている**こと(3-2)。
9. 起動時の Network タブで、**3つのマスターJSONが並列に飛んでいる**こと(4-1)。
10. **`python scripts/build_dist.py` を実行する。**
11. `git status` で、変更されたファイルが **`index.html` と `dist/index.html` の2つ**であることを確認。
    `data/` 配下に差分が出ていたらそれは別件なので、**この弾のコミットに混ぜないこと**。

---

## 9. 変更の入れ方(コミット単位)

1つのコミットに全部入れると、見た目の退行が出たときに切り分けられない。
**最低でもこの4つに分ける**:

1. `fix(ui): クラブカラー上の文字色コントラストを修正` — 1-1 / 1-2 / 1-3 / 1-5
2. `fix(ui): 予想タブの目盛りラベルのズレを修正` — 1-4
3. `feat(ui): スマホの操作性を改善(タップ反応・タップ領域・ズーム・safe-area)` — 2-1〜2-4
4. `feat(a11y): フォーカス表示・動きの抑制・aria-currentに対応` — 3-1〜3-3

4-1(並列化)、4-2(スケルトン)、6(小瑕疵)は、それぞれ独立したコミットにするか3〜4に混ぜてよい。
**各コミットの前に `build_dist.py` を実行し、`dist/index.html` を同じコミットに含めること。**
