# 実装指示：第4弾（スタッツ＋リーグ順位 ／ 選手一覧の取り込み）

第3弾（モンテカルロ・ニュース）と並行して進められます。**第3弾のAを実装するとき、ポアソンモデルの部分を
`scripts/poisson_model.py` として切り出してください。** ここで作る `stats.py` からも同じものを使います。

前提は第2弾・第3弾と同じ（Python 3.10 / PowerShell 5.x / 追加ライブラリなし / Writeツールでファイル作成）。

---

# E. スタッツ＋リーグ順位（`scripts/stats.py`）

## E-1. 方針

**すべての指標にリーグ内順位を付ける。** 数値だけでは「多いのか少ないのか」が分からないためです。
公式サイトに無い指標は自前で計算します（シュート数・支配率はデータ自体が無いので対象外）。

ネットワークアクセスなし。`{league}_matches.json` を読んで計算するだけです。

## E-2. 指標一覧

`finished == true` の試合のみを集計対象にします。

**基礎**

| key | 表示名 | 良い方向 |
|---|---|---|
| `points` | 勝点 | 高 |
| `played` | 消化 | — （順位を付けない） |
| `win` `draw` `loss` | 勝 分 敗 | 高 / — / 低 |
| `gf` `ga` `gd` | 得点 失点 得失点差 | 高 低 高 |

**平均・内訳（ここから自前計算）**

| key | 表示名 | 定義 | 良い方向 |
|---|---|---|---|
| `gfPerGame` | 平均得点 | `gf / played` | 高 |
| `gaPerGame` | 平均失点 | `ga / played` | 低 |
| `pointsPerGame` | 平均勝点 | `points / played` | 高 |
| `homePoints` | ホーム勝点 | ホーム戦のみ | 高 |
| `awayPoints` | アウェイ勝点 | アウェイ戦のみ | 高 |
| `cleanSheets` | 完封 | 失点0で終えた試合数 | 高 |
| `blanks` | 無得点 | 得点0で終えた試合数 | 低 |
| `form5Points` | 直近5試合の勝点 | 新しい順に最大5試合 | 高 |

**モデルベース（`poisson_model.py` を使う）**

| key | 表示名 | 定義 | 良い方向 |
|---|---|---|---|
| `attackRating` | 攻撃力 | 縮約後の攻撃レーティング（1.0が平均） | 高 |
| `defenseRating` | 守備力 | 縮約後の守備レーティング（**1.0が平均で、低いほど堅い**） | 低 |
| `xPoints` | 期待勝点 | 消化済み各試合の勝/分/敗の確率をポアソンモデルで出し、`3*P(勝)+1*P(分)` を合計 | 高 |
| `pointsOverX` | 勝点の上振れ | `points - xPoints` | 高 |
| `remainingDifficulty` | 残り日程の難易度 | 残り試合の対戦相手の `(attackRating + (2 - defenseRating)) / 2` の平均。**ホーム/アウェイ補正も掛ける** | 低 |

`pointsOverX` は「実力以上に勝点を拾えているか」を見る指標です。**大きく正なら今後落ちる可能性、
大きく負なら不運で今後上がる可能性がある**、という読み方をするので、符号を反転させないでください。

## E-3. 順位の付け方

- 全指標について、そのリーグ内の順位を出す（1が最良）
- `betterIsHigh` が false の指標（`gaPerGame` `defenseRating` `blanks` `remainingDifficulty` など）は**昇順で1位**
- **同値は同順位**、次は飛ばす（1, 2, 2, 4）
- `played == 0` のクラブがいる場合、平均系は 0 として扱い、順位は最下位側に並ぶ
- `played` には順位を付けない（`rank` を `null` にする）

## E-4. 出力 `data/processed/{league}_stats.json`

**メトリクスの定義そのものを出力に含めてください。** フロント側が定義をハードコードせずに表を組めるようにするためです。

```json
{
  "meta": {
    "league": "j2", "season": "2026-2027",
    "generatedAtJst": "2026-08-14T09:00:00+09:00",
    "basedOnMatches": 10, "clubCount": 20,
    "note": "順位は同値同順位。シュート数・支配率はデータソースに存在しないため対象外"
  },
  "metrics": [
    { "key": "points", "label": "勝点", "betterIsHigh": true, "format": "int", "group": "基礎" },
    { "key": "gaPerGame", "label": "平均失点", "betterIsHigh": false, "format": "float2", "group": "平均" },
    { "key": "pointsOverX", "label": "勝点の上振れ", "betterIsHigh": true, "format": "signed2", "group": "モデル" }
  ],
  "teams": [
    {
      "idTeam": "137715", "ja": "湘南ベルマーレ", "short": "湘南",
      "values": { "points": 26, "gaPerGame": 0.92, "pointsOverX": 2.4 },
      "ranks":  { "points": 1,  "gaPerGame": 3,    "pointsOverX": 2 }
    }
  ]
}
```

`format` は `int` / `float2`（小数2桁）/ `signed2`（符号つき小数2桁）の3種類。

## E-5. 検証（`scripts/test_stats.py`）

- 同値のクラブが同順位になり、その次が飛ぶこと（1, 2, 2, 4）
- `betterIsHigh: false` の指標で、**値が小さいクラブが1位**になること
- `homePoints + awayPoints == points` が全クラブで成立すること
- `cleanSheets` と `blanks` が手計算した固定データと一致すること
- 全クラブ `played == 0` のとき、例外を投げずに全指標0で返ること（シーズン開幕前）

---

# F. 選手一覧の取り込み（`scripts/fetch_squads.py`）

## F-1. 重要な前提

**このアプリは公開しない（自分の端末だけで見る）前提で実装します。**

Jリーグ公式の利用規約に「本サイトで使用している文章・画像等の無断での複製・転載を禁止します」とあります。
選手名を取り込んで手元で見るのは私的利用の範囲ですが、**GitHub Pages等で公開すると規約違反になります。**

そのため次を必ず守ってください。

- `.gitignore` に `data/processed/squads.json` を追加する
- `docs/handoff-jleague-dashboard.md` に「選手データは非公開前提。公開する場合は squads.json を配信対象から外し、フロントはクラブページへのリンク1本に戻す」と明記する
- フロントは **`squads.json` が無くても動く**こと（無ければ従来どおり公式へのリンクだけ出す）

## F-2. 取得

```
https://www.jleague.jp/club/{jleagueSlug}/player/
```

このページは**JavaScriptではなくHTMLで返ってきます**（湘南で38人ぶんを確認済み）。
クラブトップの `#player` アンカーはJS描画なので使えません。**必ず `/player/` のほうを使ってください。**

- 対象は `data/config/watchlist.json` の `teams` に入っているクラブのみ。**60クラブ全部を舐めないこと**（必要最小限に留める）
- 1クラブごとに `time.sleep(2)`
- パースは標準ライブラリの `html.parser` で行う。BeautifulSoupは使わない（依存を増やさない方針）
- 各選手について `/player/(\d+)/` のIDと、氏名・背番号・ポジションを拾う

**構造依存なので必ず失敗検知を入れてください。** 取得できた選手が **0人ならエラー扱い**にして、
`[error] league=j2 team=湘南ベルマーレ: 選手を1人も抽出できませんでした（HTML構造が変わった可能性）` と出し、
**既存の `squads.json` を上書きしないこと。**空データで潰すのが一番まずい失敗です。

## F-3. 出力 `data/processed/squads.json`

```json
{
  "meta": { "generatedAtJst": "2026-08-14T09:00:00+09:00", "source": "jleague.jp", "private": true },
  "teams": {
    "137715": {
      "fetchedAtJst": "2026-08-14T09:00:00+09:00",
      "players": [
        { "id": "1600514", "name": "山田 寛人", "number": "34", "position": "FW",
          "url": "https://www.jleague.jp/player/1600514/" }
      ]
    }
  }
}
```

- `position` は GK / DF / MF / FW
- 並びはページの掲載順のまま（ポジション順になっている）

---

# G. フロントへの反映（`index.html`）

`index.html` はすでにクラブカラーでテーマが切り替わる作りになっています（`applyTheme()`）。
**既存セクションの描画ロジックと `fmtDate()` には触らないでください。**

1. **スタッツ** — 順位表の下。`{league}_stats.json` を読み、`metrics` の `group` ごとに小見出しを付けて並べる
   - 各行は「指標名 ／ 値 ／ **`3位/20`** のような順位」の3列
   - 順位が上位3位以内なら順位を強調表示（クラブカラーを使う）
   - `rank` が `null` の指標は順位欄を空にする
   - `format` に従って数値を整形する

2. **選手一覧** — 一番下。`squads.json` があれば、選択クラブの選手をポジション別に並べる
   - 各行：背番号・氏名。氏名を `url` へのリンクにして別タブで開く
   - `squads.json` が無い、またはそのクラブのデータが無ければ、**いまと同じ「Jリーグ公式の選手一覧へ」のボタン1個**に戻す

## 補足：クラブカラーについて

`data/masters/*.json` の各クラブに `color` / `colorSub` が入っています。**手入力なので実際のクラブカラーと
ずれている可能性があります。**表示にしか使っていない（計算には一切使わない）ので、気づいたら直して構いません。
`color` を変えると、ヘッダー・ページ背景・順位表の自クラブ行・アクセントがまとめて変わります。
