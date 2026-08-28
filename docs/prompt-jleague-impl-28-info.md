# 第28弾 実装指示：シーズンの進行度に合わせた情報設計

対象: `index.html` のみ。データ取得スクリプト・JSONスキーマは変更しない。

前提ドキュメント: `docs/handoff-jleague-dashboard.md`、
`docs/prompt-jleague-impl-9.md`(全体ビュー)、`project_jleague_standings_requirements` のメモ

---

## 1. 背景 — 今まさに、ホームの主役が「まだ動く数字」になっている

クラブモードのホームタブは、**シーズンのどの時点でも同じ順番**で出している:

```
次戦(または試合中) → 直近結果 → 昇格/残留確率 → 他会場インパクト
```

2026-27シーズンは **2026-08-07開幕**。この指示書を書いている時点(2026-08-27)は
まだ数節しか消化していない。つまり今、ホームで一番大きな面積を占めているのは
**アプリ自身が「まだ大きく動きます」「序盤はブレが大きくなります」と注記を添えている数字**になっている。

実際、コードには既にその自覚がある:

- `simulationNoteHtml()` は `range.max < 5` を `early` として注記を強調表示する
- `renderPromotion()` は `modeRankProb < 0.20` のとき「まだ大きく動きます」と出す
- `renderAllPredict()` は「開幕直後はモデルがリーグ平均に寄る」と断り書きを出す

**注記で守るのではなく、並び順で守る**ほうが素直だ、というのがこの弾の趣旨。

あわせて、順位表が常に全クラブ(J1なら20行、J3なら20行)を出しているのも見直す。
スマホで知りたいのは多くの場合「自分の上下」で、20行を上から数えるのは操作として重い。

---

## 2. 何を作るか

### 2-1. シーズンの進行度を返す関数(新規)

`promotionRules.totalRounds` が既にマスタに入っている(J1なら38)。
`computePlayedRange()` と組み合わせれば進行度が出る。

```js
/* シーズンの進行度。ホームの並び順を決めるためだけに使う。
   しきい値は「順位表がどれくらい信用できるか」の体感で決めている:
     early = 消化20%未満。順位より次戦・直近結果のほうが情報量がある。
             (J1の38節なら7節あたりまで)
     late  = 消化75%以上。残り試合が少なく、他会場の結果の重みが増す。
   totalRoundsが取れないリーグでは "mid" を返して現状の並びのままにする(安全側)。 */
function seasonStage(allMatches, rules) {
  const total = rules && rules.totalRounds;
  if (!total) return "mid";
  const range = computePlayedRange(allMatches);
  if (!range.max) return "early";
  const ratio = range.max / total;
  if (ratio < 0.20) return "early";
  if (ratio >= 0.75) return "late";
  return "mid";
}
```

### 2-2. ホームの並び替え

`renderActiveTab()` のホーム分岐(`else` の側)を、並びだけ差し替える。
**セクションを消さない。順番を変えるだけ。**

| 進行度 | 並び |
|---|---|
| `early` | 次戦/ライブ → 直近結果 → **順位ミニ表(2-3)** → 昇格確率 → 他会場インパクト |
| `mid` | 次戦/ライブ → 直近結果 → 昇格確率 → 他会場インパクト **(現状のまま)** |
| `late` | 次戦/ライブ → 直近結果 → 昇格確率 → **他会場インパクト**(※下記) |

`late` では他会場インパクトの重みが上がるが、**昇格確率とインパクトは隣り合っているので
入れ替えの効果は小さい**。`late` は当面 `mid` と同じ扱いでよい
(`seasonStage()` は将来のために3値を返しておく)。

**実質的な変更は `early` の1ケースだけ。** 差分を小さく保つこと。

```js
const stage = seasonStage(all, master.promotionRules);
const promoHtml = renderPromotion(cache.simulation, team, all, master.promotionRules, league);
const impactHtml = renderImpactSection(cache.impact, team, master.promotionRules, league);
// 序盤は確率がリーグ平均に寄って動かないので、その時期だけ順位表を確率より上に出す。
const nearbyHtml = stage === "early"
  ? renderStandingsNearby(cache.standings, team, master, league)
  : "";
html = nextOrLiveHtml + renderResults(mine, team, league) + nearbyHtml + promoHtml + impactHtml;
```

`renderStandingsNearby()` にはホームタブで `cache.standings` が必要になる。
**`ensureTabData()` のホーム分岐に `ensureStandings(league)` を足すこと**(これを忘れると
序盤にホームを開いたとき、順位ミニ表だけ空になる)。順位データは gzip後で数KBなので
追加の通信コストはほぼ無い。

### 2-3. 「自分の周辺だけ」の順位表

順位タブとホームのミニ表で**同じ部品**を使う。既存の `renderStandings()` を壊さないよう、
**行の絞り込みだけを行う薄い関数**を足す形にする。

満たすべき条件:

- 自分の**上3・自分・下3**の計7行を出す(端では自分の側に寄せて7行を保つ)
- **昇格圏・降格圏の境界にあたる行は、範囲外でも必ず出す。**
  「自分が今どのゾーンから何ポイント離れているか」がこの表の存在理由なので、
  ここを省くと意味が半減する。`resolveZones(rules)` で得たゾーンの
  **各境界の順位**(そのゾーンの最下位)を対象にする。
- 連続していない行の間には**省略を示す行**(`…`)を1行入れる。入れないと
  「7位の次が17位」に見えて誤読される。
- 自分の行の強調(`tr.me`)、`playedDiff` の表示、ゾーンの色帯は
  **既存の `renderStandings()` と完全に同じ**にする(`project_jleague_standings_requirements`
  の「playedDiff必須表示」はミニ表でも守る)。

順位タブ側には `.seg-toggle`(日程タブで使っているもの)を再利用したトグルを置く:

```
[ 全体 ] [ 自分の周辺 ]
```

選択状態は `localStorage` に保存する(既存の `STORE_KEY` / `VIEW_MODE_KEY` と同じ流儀、
`try/catch` で囲んで使えない環境でも落とさない)。キー名は `jleague-app.standingsScope`。

**既定値は「全体」**。今まで全体しか無かったので、既定を変えると
「順位表が壊れた」と受け取られる。

---

## 3. 決めてから書くこと

実装に入る前に、この3つを決める。**決めずに書くと後で作り直しになる。**

1. **`early` のしきい値は 20% でよいか。**
   J1の38節なら7節、J3(38節)も同じ。既存の `simulationNoteHtml()` は
   `range.max < 5` という**節数の絶対値**で判定している。
   しきい値の考え方が2つあると将来ズレるので、**どちらかに寄せる**こと。
   推奨は割合(`seasonStage`)に寄せ、`simulationNoteHtml()` の `early` も
   `seasonStage(...) === "early"` を使うように直す。
2. **ホームのミニ表に何列出すか。**
   順位タブの全列(順位・クラブ・試合数・勝点・得失点・直近5試合)をそのまま出すと
   ホームが重くなる。**順位・クラブ・勝点・自分との勝点差**の4列に絞るのを推奨。
   ただし `playedDiff`(消化試合数の差)は**必ず添える**こと。
3. **降格圏の扱い(J3)。** `project_jleague_impl9_overall_view` のメモに
   「J3の降格圏は出さない」という決定がある。`resolveZones()` はこれを既に反映しているはずなので、
   **境界行の抽出も `resolveZones()` の結果からだけ作る**こと。
   順位を直接ハードコードしない。

---

## 4. やらないこと

- タブの増減、タブ名の変更
- 昇格確率セクションそのものの作り替え(並び順以外は触らない)
- シミュレーションのモデル・しきい値の変更(`scripts/simulate.py` は対象外)
- 全体モード(`ALL_TABS`)の並び替え。あちらは「クラブを選ばずに見る」画面なので、
  自クラブ基準の進行度で並びを変える理屈が立たない
- 「注目試合」のピックアップ(`impact.json` を使えば作れるが、この弾のスコープを越える)

---

## 5. 確認手順

進行度による分岐は、**実データが該当の時期にならないと確認できない**。
`seasonStage()` の戻り値を一時的に固定して3通り確認すること。

1. `seasonStage()` を `return "early"` に固定 → ホームで
   **順位ミニ表が昇格確率より上**に出ること。自分の行が強調され、
   昇格圏/降格圏の境界行が省略行(`…`)を挟んで出ていること。
2. `return "mid"` に固定 → ホームが**変更前と1ピクセルも変わらない**こと
   (差分が `early` だけに閉じている確認)。
3. 固定を外し、実データ(現在は開幕直後なので `early` になるはず)で
   ホームを開いて意図どおりであること。
4. 順位タブで「自分の周辺」に切り替え → 7行+境界行になること。
   **リロードしても選択が保たれる**こと。
5. **クラブ未選択の状態**(あれば)と、**J1最下位のクラブ**、**J1首位のクラブ**を選んで、
   端でも7行を保てていること(上に3行取れないぶんは下に寄せる)。
6. J3のクラブを選び、**降格圏の境界行が出ていない**こと(3-3)。
7. `python scripts/build_dist.py` を実行し、`git status` の差分が
   `index.html` と `dist/**` であること。
