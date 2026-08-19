# 実装指示：Jリーグ・ダッシュボード 第2弾（取得バッチ本体 ＋ 順位表計算）

これは実装チャットに渡す指示書です。設計側で検証済みの内容のみを書いています。
**ここに書いてある実測値・仕様は推測せず、そのまま実装してください。**

---

## 0. 前提（この環境の制約）

- **Python 3.10**。`datetime.UTC` / `itertools.batched` / `match` の新機能などは使わない。`timezone.utc` を使うこと。
- 実行は **Windows PowerShell 5.x**。コマンド連結は `&&` ではなく `;` を使う。
- **ファイルは最初から Write ツールで作成すること。** bash の `cp`/`echo >` で作ったファイルは後から編集できなくなる（EPERM）。
- 外部課金なし。追加ライブラリは `requests` のみ（既に使用中）。

## 1. 既存資産（再実装しないこと）

| ファイル | 中身 |
|---|---|
| `scripts/fetch_utils.py` | `Outcome` / `RoundResult` / `FetchAllResult` / `fetch_round_raw()` / `fetch_all_rounds()` |
| `scripts/team_matching.py` | `normalize_name()` / `build_lookup()` / `match_teams()` / `load_master_teams()` / `check_key_collisions()` |
| `scripts/time_utils.py` | `JST` / `KickoffJst` / `derive_kickoff_jst(str_timestamp)` |
| `data/masters/j1_teams_2026-27.json` `j2_master_2026-27.json` `j3_teams_2026-27.json` | 60クラブ、`idTeam`補完済み・`aliases`付き |

**マスタのチームdictの実フィールド**：`idTeam` / `en` / `aliases` / `ja` / `short` / `idVenue` / （湘南のみ `isFavorite: true`）。
注意：`load_master_teams()` は `league/idTeam/en/aliases/ja` しか返さない。`short` と `idVenue` が必要なので、
**バッチ側では生JSONの `teams` 配列も別途保持して `idTeam` で引けるようにすること**（`load_master_teams` は照合用途に限定して使う）。

## 2. 確定値

```
リーグID     J1: 4633 / J2: 4824 / J3: 4967
シーズン      "2026-2027"（3リーグ共通。0件なら "2026" をフォールバックで試す）
エンドポイント https://www.thesportsdb.com/api/v1/json/123/eventsround.php?id={league}&r={1-38}&s=2026-2027
節数          1〜38
```

## 3. レート制限（PC実機で計測済み。推測で変えないこと）

```
制限     : 約25リクエスト/分（26件目から429）
429の中身 : CloudflareのHTMLエラーページ（固定7196バイト）。JSONではない
回復     : 最後の429から60〜75秒で復帰
ヘッダ    : RateLimit-* / Retry-After は一切返らない → ヘッダを読む実装は無意味
```

**`fetch_utils.py` の現状は指数バックオフ（1/2/4/8秒）になっており、この制限には足りない。以下に修正すること。**

- 通常の節間ウェイト：`sleep_between = 2.5` 秒
- **429を検知したら 75秒固定で待機し、同じ節をリトライする**（バックオフではなく固定待機）
- タイムアウト・5xx・不正JSONは従来どおり指数バックオフでよい
- 429の発生回数をカウントして呼び出し元に返す（`FetchAllResult` に `count_429: int` を追加）

これで114件（3リーグ×38節）が約5分。GitHub Actions無料枠には影響しない。

## 4. `scripts/fetch_batch.py`（新規）

### CLI

```
python scripts/fetch_batch.py --league j2
python scripts/fetch_batch.py --league j2 --rounds 1-5      # 部分取得（検証用）
python scripts/fetch_batch.py --league all
```

### 処理

1. 対象リーグのマスタJSONを読む
2. `fetch_all_rounds()` で1〜38節を取得（`sleep_between=2.5`）
3. **各イベントのフィルタ（必須）**
   - `strSeason == "2026-2027"`（フォールバック時は `"2026"`）
   - `intRound != "0"` … 春開催の「J2・J3百年構想リーグ」とカップ戦の混入を排除
   - 上記を通らなかったイベントは件数だけ `meta.filteredOut` に記録して捨てる
4. **日時**：`derive_kickoff_jst(ev["strTimestamp"])` のみ。`dateEvent` / `dateEventLocal` / `strTimeLocal` は絶対に読まない
5. **チーム名照合**：`strHomeTeam` / `strAwayTeam` をマスタに照合。**未一致が1件でもあれば、そのリーグの出力を書かずに異常終了し、未一致名を標準エラーに出す**（黙って落とさない）
6. **表示名はマスタの `ja` / `short` を使う。** APIの文字列を表示用に出力へ入れない
7. **試合完了判定**：`strStatus == "FT"` かつ `intHomeScore` と `intAwayScore` が非null。それ以外は `finished: false` かつスコアは `null`
8. `data/processed/{league}_matches.json` に書き出す（`ensure_ascii=False`, `indent=2`）

### 出力フォーマット `data/processed/{league}_matches.json`

```json
{
  "meta": {
    "league": "j2",
    "idLeague": "4824",
    "season": "2026-2027",
    "generatedAtJst": "2026-08-13T21:00:00+09:00",
    "totalRounds": 38,
    "okRounds": [1, 2, 3],
    "badRounds": [{ "round": 12, "outcome": "empty", "detail": "events_null_or_empty" }],
    "aborted": false,
    "count429": 0,
    "filteredOut": 4,
    "matchCount": 380
  },
  "matches": [
    {
      "idEvent": "2491617",
      "round": 5,
      "kickoffJst": "2026-09-09T19:00:00+09:00",
      "kickoffDate": "2026-09-09",
      "kickoffTime": "19:00:00",
      "status": "FT",
      "finished": true,
      "home": { "idTeam": "137715", "ja": "湘南ベルマーレ", "short": "湘南", "score": 2 },
      "away": { "idTeam": "137706", "ja": "北海道コンサドーレ札幌", "short": "札幌", "score": 1 },
      "idVenue": "16580"
    }
  ]
}
```

- `matches` は `kickoffJst` 昇順でソート
- 同一 `idEvent` の重複は後勝ちで排除

### `data/processed/meta.json`（全リーグ共通の運用ログ）

実行のたびに追記形式で更新。**日次の累積リクエスト制限が未検証なので、429の発生回数を運用しながら見る。**

```json
{
  "runs": [
    { "at": "2026-08-13T21:00:00+09:00", "leagues": ["j2"], "requests": 38, "count429": 0, "durationSec": 112 }
  ]
}
```

直近30件だけ残して古いものは捨てる。

## 5. `scripts/standings.py`（新規）

**ネットワークアクセスなし。** `{league}_matches.json` を読んで順位表を作るだけ。

### 集計対象

`finished == true` の試合のみ。

### 順位決定基準（この順）

1. 勝点（勝3・分1・負0）
2. 得失点差
3. 総得点
4. **当該チーム間の勝点**
5. 当該チーム間の得失点差
6. 当該チーム間の総得点

### 当該チーム間比較の仕様（重要）

- **3クラブ以上が1〜3で並んだ場合は、並んだクラブ同士だけでミニリーグを構成して比較する**（全体成績ではなく、そのクラブ同士の直接対決のみを集計）
- **ミニリーグ内の直接対決が0試合の場合は比較不能 → 同順位扱い**。2回戦総当たりなのでシーズン中盤ではこれが常態。無理に順序をつけないこと
- 同順位になったクラブは `tiedWith` に相手の `idTeam` を並べる
- 順位番号は競技順位（1, 1, 3, 4, ...）

### 関数の切り出し（必須）

**この比較ロジックは順位表とモンテカルロ・シミュレーションの両方から呼ぶ。独立した純粋関数にすること。**

```python
def build_records(matches: list[dict]) -> dict[str, TeamRecord]:
    """finished な試合から idTeam -> TeamRecord を作る。"""

def rank_teams(
    records: dict[str, TeamRecord],
    matches: list[dict],
) -> list[list[str]]:
    """
    順位グループのリストを返す。外側リストが順位順、内側リストが同順位のidTeam。
    例: [["137715"], ["137706", "137708"], ["137711"]] → 1位/2位タイ/4位
    matches は当該チーム間比較のために必要（finished のみ参照する）。
    """
```

モンテカルロ側は「仮想の試合結果リスト」を `matches` として渡せば同じ関数で順位が出る形にする。
そのため `rank_teams` は **ファイルI/O・print・グローバル状態を一切持たないこと**。

### `playedDiff`（必須）

**延期試合が実在する**（idEvent 2491617 の例で確認済み）。勝点だけの比較は誤解を生むので、
`playedDiff = リーグ内の最多消化数 - そのチームの消化数` を必ず出力に含める。

### 出力 `data/processed/{league}_standings.json`

```json
{
  "meta": {
    "league": "j2",
    "season": "2026-2027",
    "generatedAtJst": "2026-08-13T21:00:00+09:00",
    "basedOnMatches": 120,
    "maxPlayed": 12
  },
  "table": [
    {
      "rank": 1,
      "idTeam": "137715",
      "ja": "湘南ベルマーレ",
      "short": "湘南",
      "played": 12,
      "win": 8, "draw": 2, "loss": 2,
      "gf": 22, "ga": 11, "gd": 11,
      "points": 26,
      "playedDiff": 0,
      "recent5": ["W", "W", "D", "L", "W"],
      "tiedWith": []
    }
  ]
}
```

- `recent5` は新しい順。試合数が足りなければ短くてよい
- 1試合も消化していないクラブも `played: 0` で表に載せる（マスタの全クラブが必ず並ぶ）

## 6. 検証手順（この順で）

**いきなり3リーグを回さないこと。**

1. `python scripts/fetch_batch.py --league j2 --rounds 1-3` … フィルタ・照合・日時変換の確認。未一致0件であること
2. `python scripts/fetch_batch.py --league j2` … 38節通し。所要時間と `count429` を記録
3. `python scripts/standings.py --league j2` … 順位表が出ること。**勝点合計 = 消化試合数×3 − 引き分け数** が一致するかを検算
4. `playedDiff` が0以外のクラブが出るか確認（延期試合が反映されているはず）
5. 問題なければ `--league all` で3リーグへ拡張

### 単体テスト（`scripts/test_standings.py`）

最低限これは書くこと。ネットワーク不要の固定データで。

- 勝点・得失点差・総得点だけで一意に決まるケース
- 2クラブが完全に並び、直接対決2試合で決着するケース
- 3クラブが並び、ミニリーグで決着するケース
- 3クラブが並ぶが直接対決が0試合 → 3クラブとも同順位（`tiedWith` に互いが入る）
- `playedDiff` が正しく出るケース（1クラブだけ1試合少ない）

## 7. やってはいけないこと（検証済みの地雷）

- `lookuptable.php` を使う（新シーズンの順位表は未生成。`s=2026` で返るのは別大会）
- `eventslast.php` を使う（カップ戦をJ2リーグと誤ラベルして返す）
- `strCurrentSeason` を信用する（J1が `2027` と返すが実データは `2026-2027`）
- `dateEventLocal` / `dateEvent` / `strTimeLocal` を読む（延期反映漏れで矛盾している実例あり）
- `RateLimit-*` / `Retry-After` ヘッダを読む（返らない）
- 429のレスポンスに `resp.json()` をかける（HTMLが返る。**必ずステータスコードで分岐**）
- クラブ名・スタジアム名をAPIの文字列から表示に使う（自前マスタのみ）
- bash の `cp` でファイルを作る（後から編集不能になる）

## 8. 今回やらないこと

昇格確率モンテカルロ、OB選手管理、フロント実装、Jリーグ公式のスクレイピング（シュート数・支配率等）は次弾以降。
ただし `rank_teams` の関数シグネチャだけは上記のとおりモンテカルロから再利用できる形にしておくこと。
