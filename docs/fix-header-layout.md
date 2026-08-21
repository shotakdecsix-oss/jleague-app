# 修正指示：ヘッダのレイアウト崩れと縦幅の圧縮

対象: `index.html`（CSSのみ。**JavaScript とアプリの挙動は一切変更しない**）

## 症状

スマホ幅で、ヘッダの `<h1>Jリーグ ダッシュボード</h1>` が**1文字ずつ縦に積まれ**、ヘッダの縦幅が200px以上に膨らむ。

## 原因

`.header-row1` は `display:flex` で、`h1` は既定の `flex:0 1 auto` のため**縮む対象**になっている。

右側の `.header-row1-right` は `⟳ --:--` ボタン（`min-width:44px` ＋ padding）、`i` ボタン（26px）、`deployBadge` で150px前後を占めるため、`h1` に残る幅がほとんど無い。

そして `h1` に `white-space` の指定が無く、**日本語は任意の文字位置で改行できる**ため、`h1` の min-content 幅が「1文字ぶん」になる。flex はそこまで縮めるので、12文字が縦に積まれる。

---

## 修正1：h1 が潰れないようにする（必須）

`header h1` のルール（現在は37行目付近の1行）を差し替える。

```css
header h1{
  margin:0; font-size:15px; font-weight:600; letter-spacing:.04em; opacity:.9;
  flex:1 1 auto;
  min-width:0;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
```

`.header-row1-right` に `flex:none` を追加する。

```css
.header-row1-right{display:flex; align-items:center; gap:6px; flex:none}
```

### 注意

**`min-width:0` を省略しないこと。** flex アイテムの既定は `min-width:auto` で、これがあると `overflow:hidden` を付けても縮まずに右へはみ出す。`white-space:nowrap` と `min-width:0` と `overflow:hidden` の3つが揃って初めて「入らなければ末尾を … で省略」という挙動になる。

`h1` のテキスト自体は変更しない。CSS だけで解決する。

---

## 修正2：ヘッダの縦幅を詰める

`⟳` ボタンの `min-height:44px` は**タップターゲットの推奨最小値なので維持する**。削るのは余白側。

```css
header{
  background:var(--accent); color:var(--accent-ink);
  padding:12px 14px;              /* 20px 16px 18px から */
  box-shadow:0 1px 6px rgba(0,0,0,.15);
}
.header-row1{display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px}  /* margin-bottom 10px から */
.header-row2 select{padding:8px 12px}
```

`.header-row2 select{padding:8px 12px}` は、既存の汎用 `select{padding:10px 12px}` を上書きする形で**ヘッダ内の select だけ**詰める。本文中の select には影響させないこと。

これで概ね 132px → 110px 前後になる。sticky ヘッダなので、この20px強は体感で効く。

### 狭い端末向け

既存の `@media (max-width:360px)` ブロックに1行追加する。

```css
@media (max-width:360px){ header h1{font-size:13px} }
```

このブレークポイントでは `deployBadge` が既に `display:none` になるため、右側が約70px軽くなる。あわせて幅320pxでも省略記号が出ずに収まるようになる。

---

## 修正3：トースト／ポップオーバーをオーバーレイにする

現在 `.ondemand-toast` と `.ondemand-popover` は header 内の**通常フロー**（`margin:0 0 10px`）に置かれている。このままだと、更新ボタンを押してトーストが出た瞬間に**ヘッダの高さが変わり、下のコンテンツが飛ぶ**。sticky ヘッダなので特に目立つ。

```css
header{ position:relative }   /* 修正2のブロックに追加 */

.ondemand-toast{
  position:absolute; top:100%; left:12px; right:12px; z-index:20;
  margin:6px 0 0; padding:8px 12px; border-radius:10px; background:rgba(0,0,0,.28);
  color:var(--accent-ink); font-size:12px; font-weight:600; text-align:center;
}
.ondemand-popover{
  position:absolute; top:100%; left:12px; right:12px; z-index:20;
  margin:6px 0 0; padding:12px 14px; border-radius:var(--radius); background:#fff; color:var(--ink);
  box-shadow:0 4px 16px rgba(0,0,0,.18); font-size:12px;
}
```

`#headerWrap` が `z-index:10` の sticky なので、その内側で `z-index:20` にすればタブバーより手前に出る。**`#headerWrap` 側の `z-index` や `position` は変更しないこと**（タブバーの固定が壊れる）。

トーストとポップオーバーが同時に表示されると重なるが、既存の JS が排他制御しているならそのままでよい。していない場合も**本修正では JS に手を入れない**。重なりが起きるようなら別途報告すること。

---

## 触ってはいけないもの

- JavaScript は一切変更しない。表示条件・タイミング・取得処理はそのまま。
- `h1` のテキスト、`aria-label`、`title` は変更しない。
- `.ondemand-header-btn` の `min-height:44px` / `min-width:44px` は下げない。
- `#headerWrap` の `position:sticky` / `z-index:10` は変更しない。
- `#tabbar` には触らない。
- 本文中の汎用 `select{}` ルールは変更しない（ヘッダ内だけを上書きする）。

---

## 確認手順

1. 幅 **320 / 360 / 390 / 430px** で、`h1` が1行に収まり縦積みにならないこと。
2. 幅320pxで `h1` が省略記号（…）になる場合も、右側のボタンにかぶらず、横スクロールバーが出ないこと。
3. ヘッダの実測高さが110px前後に収まっていること（開発者ツールで確認）。
4. `⟳` ボタンのタップ領域が44px以上を保っていること。
5. 更新ボタンを押してトーストが出たとき、**その下のコンテンツが縦に動かないこと**。消えたときも同様。
6. `i` ボタンでポップオーバーを開いたとき、タブバーの手前に表示され、コンテンツが動かないこと。
7. クラブ切替で「北海道コンサドーレ札幌」のような長い名前を選んでも、下段の select がはみ出さないこと（既存の `flex:1; min-width:0` で効いているはず。回帰確認のみ）。
8. モードを「全体」に切り替えてもヘッダの高さが変わらないこと。
9. 本文中に select を使っている箇所があれば、そのサイズが変わっていないこと。
