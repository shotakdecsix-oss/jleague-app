# 実装指示：第3弾（昇格確率モンテカルロ ／ ニュース取得 ／ OB選手追跡）

第2弾（取得バッチ・順位表）は完了・検証済みです。フロントのMVP（`index.html`）も動いています。
今回は3機能。**A → B → C の順に実装し、それぞれ動作確認してから次に進んでください。**

## 0. 前提（変更なし）

- **Python 3.10** / Windows PowerShell 5.x（`;` 区切り）
- **追加ライブラリを増やさないこと。** 現状 `requests` のみ。**numpy も使わない**（実機に入っている保証がない）。RSSのパースは標準ライブラリの `xml.etree.ElementTree` で足りる
- ファイルは最初から Write ツールで作成する（bashの `cp` で作ると後から編集できなくなる）
- `data/processed/{league}_matches.json` と `{league}_standings.json` は3リーグぶん生成済み

---

# A. 昇格確率モンテカルロ（`scripts/simulate.py`）

## A-1. 使うもの

`standings.py` の `build_records()` / `rank_teams()` を**そのまま再利用する**。順位決定ロジックを二重に書かないこと。
`rank_teams()` は純粋関数で、仮想の試合結果リストを `matches` として渡せば同じ形で順位が出ます。

## A-2. 得点モデル

消化済み試合から、クラブごとの攻撃力・守備力とホームアドバンテージを推定し、ポアソン分布で得点を生成します。

**シーズン序盤はデータが極端に少ない**（現時点で各クラブ1試合）ので、素の平均を使うと「1試合で3点取ったクラブの攻撃力＝3.0」のような無意味な値になります。
**リーグ平均への縮約（shrinkage）を必ず入れてください。**

```python
SHRINK_K = 6.0   # 縮約の強さ。消化6試合で「自チーム実績:リーグ平均 = 1:1」になる

# リーグ全体
league_avg_goals = 全消化試合の総得点 / (消化試合数 * 2)   # 1チーム1試合あたりの平均得点

# クラブiの攻撃力・守備力（1.0 が平均）
w_i = n_i / (n_i + SHRINK_K)          # n_i = クラブiの消化試合数
atk_i = w_i * (得点_i / n_i) / league_avg_goals + (1 - w_i) * 1.0
def_i = w_i * (失点_i / n_i) / league_avg_goals + (1 - w_i) * 1.0
# n_i == 0 のクラブは atk_i = def_i = 1.0

# ホームアドバンテージ（リーグ全体から1つだけ推定）
hfa = (全ホーム得点 / 消化試合数) / (全アウェイ得点 / 消化試合数)
# 消化試合が少ないうちは暴れるので 1.0〜1.5 にクリップし、
# 消化10試合未満なら既定値 1.20 を使う
```

期待得点は乗法モデルで出します。

```python
lambda_home = league_avg_goals * atk_home * def_away * hfa
lambda_away = league_avg_goals * atk_away * def_home / hfa
# 念のため 0.05〜5.0 にクリップする
```

**このモデルの限界をdocstringに明記しておくこと**：得点の独立性を仮定していて、0-0や1-1が実際より少なく出ます（Dixon-Coles補正なし）。スコアだけしか無いデータでできる範囲の近似です。

## A-3. ポアソン乱数（標準ライブラリのみ）

`random` にポアソン分布は無いので、Knuthの方法を自前で書きます。λが小さいので十分速いです。

```python
def poisson(lam: float, rnd: random.Random) -> int:
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rnd.random()
        if p <= L:
            return k
        k += 1
```

## A-4. シミュレーション

```
1. {league}_matches.json を読む
2. finished の試合 → 確定結果としてそのまま使う
3. finished でない試合 → 各試行でポアソン乱数からスコアを生成
4. 確定＋生成した全試合を rank_teams() に渡して最終順位を出す
5. 上記を TRIALS 回繰り返し、クラブごとに順位の出現回数を数える
```

- `TRIALS = 10000` を既定にし、`--trials` で変更できるようにする
- **`random.Random(seed)` を使い、`--seed` で固定できるようにする**（既定 `--seed 42`）。再現できないと検証ができません
- 進捗を1000試行ごとに `[info] 3000/10000` の形で出す（数分かかるため）
- `kickoffTbd` の試合も「いずれ開催される」ものとしてシミュレーション対象に含める（延期であって中止ではない）

**性能の注意**：`rank_teams()` は同勝点・同得失点差・同総得点が並んだときだけミニリーグ計算に入る作りなので、通常は軽いです。ただし10000試行×380試合なので、まず `--trials 100` で所要時間を測り、10000回に何分かかるか見当をつけてから本番を回してください。**5分を大きく超えるようなら報告してください**（試行回数の既定値を見直します）。

## A-5. 出力 `data/processed/{league}_simulation.json`

```json
{
  "meta": {
    "league": "j2",
    "season": "2026-2027",
    "generatedAtJst": "2026-08-14T09:00:00+09:00",
    "trials": 10000,
    "seed": 42,
    "basedOnMatches": 10,
    "remainingMatches": 370,
    "leagueAvgGoals": 1.35,
    "homeAdvantage": 1.20,
    "modelNote": "得点は独立ポアソン。Dixon-Coles補正なし。序盤は縮約(K=6)によりリーグ平均に強く寄る"
  },
  "teams": [
    {
      "idTeam": "137715", "ja": "湘南ベルマーレ", "short": "湘南",
      "currentRank": 7,
      "expectedPoints": 58.3,
      "expectedRank": 6.4,
      "autoPromotion": 0.182,
      "playoff": 0.315,
      "relegation": 0.008,
      "rankDistribution": [0.04, 0.05, 0.06, "...20クラブぶん、合計1.0"],
      "attackRating": 1.12,
      "defenseRating": 0.94
    }
  ]
}
```

- `autoPromotion` / `playoff` / `relegation` は、そのリーグのマスタの `promotionRules` から判定する。**`promotionRules` が無いリーグ（J1・J3）では `null` にして、`rankDistribution` と `expectedPoints` だけ出す**
- 同順位（タイ）が出た試行では、そのクラブ全員をその順位として数える
- `teams` は `expectedRank` の昇順で並べる

## A-6. 検証（`scripts/test_simulate.py`）

- 同じ `--seed` で2回実行すると完全に同じ結果になること
- 全クラブの `rankDistribution` の合計がそれぞれ 1.0（誤差 1e-9 以内）
- 各順位について、全クラブぶんの確率を足すと 1.0 になること（タイがあると1.0を超えるので、**タイを含む試行の扱いを決めてテストに書く**。「タイのときは全員がその順位」なら合計は1.0以上になるので、そのことをテストで明示する）
- 消化0試合の人工データを与えると、全クラブの `attackRating` / `defenseRating` が 1.0 になること
- `autoPromotion + playoff` が1.0を超えないこと（昇格圏とPO圏は排他）

---

# B. ニュース取得（`scripts/fetch_news.py`）

## B-1. 方針の確定

**OB選手リストとニュース対象は、リポジトリ内のJSONで持つ。** 設計時に保留していた論点はこれで決着とします。

理由：**バッチはいまPC上で走っている**ので、「リストは端末内・取得はサーバー側」という矛盾がそもそも発生しません。GitHub Actions化したあとも、リストがリポジトリにあれば同じスクリプトがそのまま動きます。アプリから編集したくなった時点で GitHub API 経由の更新を足せばよく、後戻りになりません。

## B-2. 設定ファイル `data/config/watchlist.json`（新規・手で編集する）

```json
{
  "note": "ニュースを追う対象。手で編集する。idTeam は data/masters/*.json 参照",
  "teams": ["137715"],
  "obPlayers": [
    { "name": "山田太郎", "note": "2024年まで湘南。現在は○○FC", "extraQuery": "サッカー", "enabled": true }
  ]
}
```

`obPlayers` は**空配列で作っておいてください。**中身は利用者が後から追記します。

## B-3. 取得

Google News RSS を使います。認証不要・無料です。

```
https://news.google.com/rss/search?q={URLエンコードしたクエリ}&hl=ja&gl=JP&ceid=JP:ja
```

- クラブのクエリ：マスタの `ja`（例 `湘南ベルマーレ`）
- OB選手のクエリ：`name`（`extraQuery` があれば半角スペースで連結）。**選手名だけだと同姓同名を拾うので `extraQuery` を用意しています**
- `xml.etree.ElementTree` でパースし、`item` の `title` / `link` / `pubDate` / `source` を取る
- `pubDate` はRFC822形式（`Mon, 10 Aug 2026 12:00:00 GMT`）。**JSTに変換して保存する。** `email.utils.parsedate_to_datetime()` を使えば標準ライブラリで済みます
- 1件あたり **`time.sleep(2)`**。TheSportsDBとは別サーバーなので互いのレート制限には影響しませんが、礼儀として空けます
- 各クエリ**最大20件**、`pubDate` の新しい順
- **取得失敗（HTTPエラー・XMLパース失敗）は、そのクエリだけスキップして続行する。**ニュースが取れないことでバッチ全体を落とさないこと

## B-4. 出力 `data/processed/news.json`

```json
{
  "meta": { "generatedAtJst": "2026-08-14T09:00:00+09:00", "queryCount": 3, "failed": [] },
  "teams": {
    "137715": [
      { "title": "…", "link": "https://…", "publishedJst": "2026-08-12T18:30:00+09:00", "source": "○○新聞" }
    ]
  },
  "obPlayers": {
    "山田太郎": [ { "title": "…", "link": "…", "publishedJst": "…", "source": "…" } ]
  }
}
```

## B-5. 検証

- `watchlist.json` の `obPlayers` が空でもエラーにならないこと
- 存在しない `idTeam` を書いたら、そのエントリを警告付きでスキップして続行すること
- `pubDate` がJSTに変換されていること（GMT表記の実例をテストに1つ入れる）

---

# C. フロントへの反映（`index.html`）

既存の見た目を壊さず、セクションを2つ足します。**既存の3セクション（次の試合・直近の結果・順位表）の描画ロジックには触らないこと。**

1. **昇格確率** — 順位表の下。`{league}_simulation.json` があれば表示、無ければセクションごと出さない
   - 選択クラブの `autoPromotion` / `playoff` / `relegation` を大きめに3つ並べる
   - `promotionRules` が無いリーグでは代わりに `expectedRank` と `expectedPoints` だけ出す
   - **`meta.basedOnMatches` が少ないときは「消化N試合時点の推定」と必ず添える。**序盤の数字が一人歩きしないように

2. **ニュース** — 一番下。`news.json` があれば、選択クラブぶんを最大10件。OB選手のニュースがあればその下に選手名つきで
   - 各項目は `title` と `publishedJst`（`M/D`）、リンクは別タブ

**日時の表示は必ず既存の `fmtDate()` を通すこと。** `new Date().getHours()` を新たに書かないでください。端末のタイムゾーンで9時間ずれます（実際に踏んで修正済みのバグです）。

---

# D. まとめて実行できるように

`scripts/update_all.py` を作り、以下を順に呼ぶ。どれか1つが落ちても残りは続行し、最後にまとめて結果を出す。

```
fetch_batch.py --league all → standings.py x3 → simulate.py x3 → fetch_news.py
```

GitHub Actions化（次回）は、このスクリプトを1本呼ぶだけで済む形にしておいてください。
