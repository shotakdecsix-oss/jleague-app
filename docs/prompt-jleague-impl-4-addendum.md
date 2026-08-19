# 第4弾 追補（`fetch_official.py` 実装済みを受けての改訂）

指示書 `prompt-jleague-impl-4.md` を書いた時点で `scripts/fetch_official.py` の存在を把握していませんでした。
実物（`data/processed/club_extra.json`）を確認したうえで、以下のとおり改めます。

---

## 1. F章「選手一覧の取り込み」は**破棄**

**`scripts/fetch_squads.py` は作らないでください。`data/processed/squads.json` も作りません。**
選手データは既存の `fetch_official.py` / `club_extra.json` に一本化します。

理由：

- 二重管理を避けられる
- **取れる情報が多い** — F章の想定は氏名・背番号・ポジションだけだったが、`club_extra.json` には
  `playerId` / `uniformNo` / `position` / `birthday` / `height` / `weight` / `isHomeGrown` /
  `totalGameCount` / `totalGoalCount` まで入っている
- **壊れにくい** — HTML構造への依存より、Next.jsの埋め込みJSONのほうが安定している
- 60クラブを1回で取れるので `watchlist.json` への依存が不要

**選手個別ページへのリンク**は、フロント側で `playerId` から組み立ててください。

```
https://www.jleague.jp/player/{playerId}/
```

## 2. 著作権対応の適用範囲を拡大する

F章に書いた「非公開前提」の扱いは、**`club_extra.json` にそのまま、より広い範囲で適用します。**
ニュース見出し・選手の個人情報・スタッツと、公式サイト由来のものが全部入っているためです。

`.gitignore` に以下を追加してください。

```
data/processed/club_extra.json
data/tmp/
```

**`data/tmp/sample_club_top.html` と `sample_club_player.html` は特に注意。** 公式ページのHTMLが
1MBずつ丸ごと保存されており、複製として最も直接的です。デバッグ用に手元に残すのは構いませんが、
リポジトリには絶対に入れないでください。

`docs/handoff-jleague-dashboard.md` に次を明記すること。

> Jリーグ公式由来のデータ（`club_extra.json` 、`data/tmp/` 配下のHTML）は非公開前提。
> 公式サイトの利用規約が「文章・画像等の無断での複製・転載」を禁じているため、
> GitHub Pages等で公開する場合はこれらを配信対象から外し、フロントは公式へのリンクのみに戻すこと。
> TheSportsDB由来のデータ（`*_matches.json` / `*_standings.json` / `*_simulation.json`）はこの制約の対象外。

**フロントは `club_extra.json` が無くても動くこと。** 無ければ選手一覧・公式スタッツ・公式ニュースの
各セクションを出さず、クラブ公式へのリンク1本に戻す。これで、あとから公開したくなったときに
ファイルを配信対象から外すだけで規約に沿った状態へ戻せます。

## 3. E章「スタッツ＋リーグ順位」の改訂

**公式のスタッツには既にリーグ内順位が付いています。** `club_extra.json` の `clubStats.items` は
`{key, label, value, rank}` の形で、以下6項目が入っています。

```
scorePg      1試合平均得点数
passCountPg  1試合平均パス数
ballRate     平均ボール保持率
distance     1試合平均走行距離
sprint       1試合平均スプリント回数
cleanSheet   無失点試合総数
```

**このうちパス数・ボール保持率・走行距離・スプリント回数は、設計段階では「TheSportsDBに無いので
実装不可（Tier 2）」と判断していた項目です。** 公式から取れるようになったので、自前で計算し直さず
**公式の値と順位をそのまま使ってください。**

そのうえでE章の方針をこう改めます。

- **公式にある指標** → `club_extra.json` の `value` と `rank` をそのまま使う。再計算しない
- **公式に無い指標** → `stats.py` で計算し、自前で順位を付ける（E-2〜E-3の仕様どおり）
- 出力の各指標に **`source`（`"official"` または `"computed"`）** を持たせ、フロントで区別できるようにする

自前計算が必要なのは、E-2の表のうち公式と重複しないものです。特に以下は公式に無いので必ず残します。

```
homePoints / awayPoints   ホーム・アウェイの勝点内訳
form5Points               直近5試合の勝点
attackRating / defenseRating   縮約後のレーティング
xPoints / pointsOverX     期待勝点と、その上振れ
remainingDifficulty       残り日程の難易度
```

**`cleanSheet` は公式にもE-2にもあります。公式を優先し、自前計算は削ってください。**
`scorePg` も同様に公式優先（E-2の `gfPerGame` は削る）。ただし**平均失点 `gaPerGame` は公式に無いので残します。**

### 確認してほしいこと

- `clubStats.seasonKey` が `"2026-2"` になっています。**これが2026/27シーズンを指しているのか、
  それとも別のステージ区分なのかを確認してください。** 違うシーズンの数字を混ぜると全体が狂います
- 公式の `rank` が**リーグ内順位（20クラブ中）**であることを、複数クラブで突き合わせて確認してください
  （同じ指標で1〜20が過不足なく現れるか）

## 4. `leaders` と `seasonalPerformances` の活用

`club_extra.json` にはもう2つ使えるものが入っています。フロントに追加してください（優先度は低め）。

- **`leaders`** — 得点・アシスト・デュエル勝利数などのクラブ内トップ選手。選手一覧セクションの上に
  「今季の主役」として3項目ほど出すと映えます。`playerId` から個別ページへリンクできます
- **`seasonalPerformances`** — 過去の年度別成績（35年ぶん）。クラブの歴史として折りたたみで出す程度で十分です
