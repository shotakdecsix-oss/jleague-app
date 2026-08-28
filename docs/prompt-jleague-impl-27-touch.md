# 第27弾 実装指示：スマホの操作感(スワイプ切替・引っ張って更新・共有)

対象: `index.html` のみ。データ取得スクリプト・workflow・JSONスキーマは変更しない。

前提ドキュメント: `docs/handoff-jleague-dashboard.md`、`docs/prompt-jleague-impl-10.md`(オンデマンド更新)

---

## 0. この弾の位置づけ

第24弾で「押しても反応が返らない」「押しにくい」は潰した。この弾は**一段上**、
スマホで当然のようにできてほしい操作を足す。

1. **左右スワイプでタブを移動**(6タブを毎回上部で狙わせない)
2. **引っ張って更新**(既存のオンデマンド更新に、スマホの標準的な入り口を付ける)
3. **結果の共有**(ただし3章で書くとおり、このアプリの設計方針と衝突する点がある)

**1 → 2 → 3 の順に価値が高い。** 3は読んだうえで見送る判断もあり得る。

---

## 1. 左右スワイプでタブを移動

### 1-1. 満たすべき条件

- **横スクロールできる要素の上では発火しない。** 該当するのは
  `.chart-scroll`(確率推移グラフ)と `.ec-rounds`(天皇杯のラウンド切替)の2つ。
  ここで横に指を動かすのは中身をスクロールしたいときなので、タブが変わったら事故。
- **縦スクロール中に誤爆しない。** 指は完全にまっすぐ動かないので、
  「横の移動量が縦の1.5倍以上」かつ「横に60px以上」を条件にする。
- **モードは跨がない。** クラブモードの右端(ニュース)から全体モードへ移らない。
  端では**何も起きない**(循環もしない)。端に達したことは、タブバーの見た目で分かる。
- **`preventDefault()` を呼ばない。** 呼ぶと縦スクロールまで止まる。
  リスナーは `{ passive: true }` で登録する。

### 1-2. 実装

`bindTabBarEvents()` の近くに置き、`init()` から**1回だけ**呼ぶ
(`renderActiveTab()` は `#app` の中身を毎回入れ替えるので、`#app` にリスナーを付け直すと
多重登録になる。`document` に付けて委譲する)。

```js
/* 左右スワイプでの隣タブ移動(第27弾)。
   preventDefault()は呼ばない(縦スクロールを殺すため)。よってリスナーはpassiveでよい。 */
const SWIPE_MIN_X = 60;        // これ未満の移動は「押しただけ」とみなす
const SWIPE_RATIO = 1.5;       // 横 > 縦 * この倍率 でなければ縦スクロールの一部とみなす
const SWIPE_MAX_MS = 600;      // ゆっくりした指の移動はスクロールの意図とみなして無視する

function bindSwipeNavigation() {
  let sx = 0, sy = 0, st = 0, active = false;

  document.addEventListener("touchstart", ev => {
    if (ev.touches.length !== 1) { active = false; return; }
    // 横スクロールを持つ要素の上から始まったスワイプは、その要素のものとして扱う
    if (ev.target.closest && ev.target.closest(".chart-scroll, .ec-rounds")) { active = false; return; }
    const t = ev.touches[0];
    sx = t.clientX; sy = t.clientY; st = Date.now(); active = true;
  }, { passive: true });

  document.addEventListener("touchend", ev => {
    if (!active) return;
    active = false;
    const t = ev.changedTouches && ev.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - sx, dy = t.clientY - sy;
    if (Date.now() - st > SWIPE_MAX_MS) return;
    if (Math.abs(dx) < SWIPE_MIN_X) return;
    if (Math.abs(dx) < Math.abs(dy) * SWIPE_RATIO) return;

    const list = currentTabList();
    const idx = list.findIndex(t2 => t2.id === currentActiveTabId());
    if (idx < 0) return;
    const next = idx + (dx < 0 ? 1 : -1);   // 左へ払う = 次のタブ
    if (next < 0 || next >= list.length) return;  // 端では何もしない(循環させない)
    if (state.viewMode === "all") setAllTab(list[next].id);
    else setActiveTab(list[next].id);
  }, { passive: true });
}
```

`setActiveTab()` / `setAllTab()` が `window.scrollTo(0,0)` と `syncHash()` を既にやるので、
**この関数以外に触る場所は無い**。

### 1-3. やらないこと

- **指の動きに追従して画面が動くアニメーション**は入れない。
  `#app` を毎回 `innerHTML` で作り直す構造なので、隣のタブを事前に描いて横に並べる作りが要る。
  投資に見合わない。**指を離した瞬間に切り替わる**だけで十分実用になる。
- スワイプでのモード切替(クラブ↔全体)。上下に別の意味を割り当てると誤爆が増える。

---

## 2. 引っ張って更新(pull-to-refresh)

### 2-1. 先に注意すべきこと

- **Android Chromeにはブラウザ標準の引っ張って更新がある。** 何もしないと
  「ページ全体のリロード」と自前の更新が二重に走る。`overscroll-behavior-y: contain` で
  標準の挙動を止められる。
- **iOS Safariのゴムのような跳ね返り**は上記では止まらない。iOSでは
  `scrollY` が負にならないので、**`scrollY === 0` から下に引いた距離**で判定する。
- **既存のクールダウンを尊重する。** `ONDEMAND_MIN_INTERVAL_MS`(60秒)があり、
  ヘッダーのボタンはこれに従っている。引っ張って更新でも `runOndemandUpdate({})` を
  **`ignoreCooldown` を渡さずに**呼ぶ。連続で引っ張られても既存のガードが効く。
- **結果表示は既存の `showOndemandToast()` を使う。** 新しい通知UIを作らない。

### 2-2. CSS

```css
/* Android Chromeの標準の引っ張って更新を止める(自前の実装と二重になるため)。
   横方向は指定しない(.chart-scrollなどの横スクロールに影響させない)。 */
body{overscroll-behavior-y:contain}

/* 引っ張っている最中に上から出る帯。ヘッダー(sticky)の下に潜り込ませない。 */
.ptr-hint{
  position:fixed; top:0; left:0; right:0; z-index:30;
  display:flex; align-items:center; justify-content:center;
  height:0; overflow:hidden;
  background:var(--accent); color:var(--accent-ink);
  font-size:12px; font-weight:700;
}
```

高さはJSから直接指定する(指の移動に追従させるため)。

### 2-3. 実装

```js
/* 引っ張って更新(第27弾)。既存のオンデマンド更新にスマホ標準の入り口を足すだけで、
   更新の中身・クールダウン(60秒)・結果表示は runOndemandUpdate / showOndemandToast のまま。 */
const PTR_TRIGGER_PX = 70;   // これ以上引いたら発火
const PTR_MAX_PX = 110;      // 帯の見た目の上限(これ以上は伸ばさない)

function bindPullToRefresh() {
  const hint = document.createElement("div");
  hint.className = "ptr-hint";
  document.body.appendChild(hint);

  let sy = 0, pulling = false, dist = 0;

  document.addEventListener("touchstart", ev => {
    // 一番上まで戻っているときだけ受け付ける(途中から引くと縦スクロールと区別できない)
    pulling = window.scrollY <= 0 && ev.touches.length === 1;
    if (pulling) sy = ev.touches[0].clientY;
    dist = 0;
  }, { passive: true });

  document.addEventListener("touchmove", ev => {
    if (!pulling) return;
    dist = ev.touches[0].clientY - sy;
    if (dist <= 0) { hint.style.height = "0"; return; }
    // 引くほど重くなる感触にする(指の移動量をそのまま高さにすると軽すぎて誤爆する)
    const h = Math.min(PTR_MAX_PX, dist * 0.5);
    hint.style.height = h + "px";
    hint.textContent = dist >= PTR_TRIGGER_PX ? "指を離して更新" : "引っ張って更新";
  }, { passive: true });

  document.addEventListener("touchend", () => {
    if (!pulling) return;
    pulling = false;
    hint.style.height = "0";
    if (dist >= PTR_TRIGGER_PX) {
      // ignoreCooldownは渡さない。連打対策(60秒)は既存の実装に任せる。
      runOndemandUpdate({});
    }
    dist = 0;
  }, { passive: true });
}
```

`init()` の最後で `bindSwipeNavigation()` と `bindPullToRefresh()` を呼ぶ。

### 2-4. 1章のスワイプとの共存

両方 `document` の `touchstart` を見るが、**判定軸が違う**(横 vs 縦)ので競合しない。
実際に指を斜めに動かしたとき、1章は「横 > 縦×1.5」を要求し、
2章は「一番上から下向き」を要求するので、**同時に成立することはない**。

---

## 3. 結果の共有 — 実装する前に読むこと

`navigator.share` は未使用で、共有の導線は無い(`navigator.clipboard` は
カレンダー(.ics)のURLコピーにだけ使われている)。

**ただし、このアプリは「共有しない」前提で作られている。**

- `index.html` に `<meta name="robots" content="noindex, nofollow">`
- `robots.txt` あり
- `scripts/build_dist.py` の冒頭に
  「フィルタは一切かけない(個人利用のダッシュボードで、**URLを共有せず**検索にも載せない前提のため)」

さらに `data/processed/club_extra.json` の選手データはJリーグ公式のNext.js由来で、
**非公開前提**という判断が既にある(`project_jleague_player_data` のメモ)。
**URLを共有すると、この前提が崩れる。**

### 3-1. したがって、共有するのは「テキストだけ」にする

`navigator.share()` は `url` を省略できる。**URLを含めない**。

```js
/* 結果の共有(第27弾)。URLは含めない — このアプリはnoindex/robots.txtで
   「URLを共有しない」前提で作られており、選手データも非公開前提のため。
   共有するのは、その場で読める試合結果のテキストだけ。 */
function shareMatchText(text) {
  if (navigator.share) {
    navigator.share({ text: text }).catch(() => { /* 利用者がキャンセルしただけ。無視する */ });
  } else if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => showOndemandToast("コピーしました", true)).catch(() => {});
  }
}
```

置き場所は**試合詳細パネル(`renderMatchEventsPanel()`)の末尾に小さなボタン1つ**だけにする。
ホーム・順位・日程それぞれに共有ボタンを散らすと、画面がボタンだらけになる。

共有するテキストの形(例):

```
柏 2-1 湘南 (J1 第25節 8/23)
得点: 細谷 12' / 小屋松 67' / 鈴木 80'
```

`navigator.share` は **httpsか localhost でのみ動く**(Renderは https なので配信時は問題ない)。
非対応環境ではクリップボードに落ちる。**両方無い環境ではボタン自体を出さない**こと:

```js
const canShare = !!(navigator.share || (navigator.clipboard && navigator.clipboard.writeText));
```

### 3-2. 見送る判断もある

「誰にも共有しないから個人用に割り切っている」のであれば、**3章は丸ごと実装しなくてよい**。
1章と2章だけでこの弾は成立する。

---

## 4. ついでに: 日程タブの「今節へ」

クラブモードの日程タブには節ジャンプの `<select>`(`#roundJump`)しかない。
全体モードのカレンダーには基準日への自動スクロールがあるのに、
クラブモードでは**今どのあたりを見るべきかに毎回自分で合わせる**必要がある。

`computeCurrentRound(allMatches)` が既にあるので、`.schedule-controls` に
ボタンを1つ足して、その節へ `scrollIntoView({ block: "start" })` するだけでよい。
`.sched-round{scroll-margin-top:90px}` が既に入っているので、
**固定ヘッダーの下に潜る心配はない**。

```js
'<div class="seg-toggle"><button type="button" data-jumpcurrent>今節へ</button></div>'
```

**新しいクラスを作らず、`.seg-toggle` でラップする。** `.seg-toggle button` には
第24弾で `min-height:44px` が入っており、`.schedule-controls` の既存トグルと
同じ見た目・同じタップ領域がそのまま効く(単独ボタンなので `.active` は付けない)。

---

## 5. 確認手順

**1〜4はスマホ実機でしか確認できない。** PCのDevToolsのデバイスモードでは
タッチイベントの再現が不完全で、特に2章(引っ張って更新)は判定が合わない。

1. スマホでタブを**左右に払って**隣のタブに移ること。
   **右端(ニュース)からさらに左へ払っても何も起きない**こと。
2. **確率推移グラフを横にスクロール**しても、タブが変わらないこと(1-1の除外)。
   天皇杯のラウンド切替(横スクロール)でも同じ。
3. 縦に長い順位表を**素早く縦スクロール**して、タブが変わらないこと。
4. 一番上で**下に引っ張る**と帯が出て、離すと更新が走ること。
   **続けてもう一度引っ張ったら、60秒のクールダウンのメッセージ**が出ること。
5. **ページの途中で**下に引っ張っても帯が出ないこと。
6. Androidで、引っ張ったときに**ブラウザ標準のリロードが二重に走らない**こと(2-2)。
7. (3章を実装したなら)共有シートに**URLが含まれていない**こと。
8. `python scripts/build_dist.py` を実行し、`git status` の差分が
   `index.html` と `dist/**` であること。
