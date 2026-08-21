# 第11弾 実装指示：GitHub Actions による定期バッチ

対象リポジトリ: `jleague-app`（`shotakdecsix-oss/jleague-app`、**private**）
前提ドキュメント: `docs/handoff-jleague-dashboard.md`、`docs/prompt-jleague-impl-10.md`

現在、データ更新は PC で `python scripts/update_all.py` を叩いたときにしか起きない。これを GitHub Actions で自動化する。

## 更新経路の棲み分け（確定）

| 経路 | 対象 | タイミング | 性質 |
|---|---|---|---|
| **端末トリガ**（第10弾） | 試合結果・暫定順位のみ | ユーザーが押した随時 | 暫定値 |
| **GitHub Actions**（本指示） | 全データ | 4時間おき | 確定値 |

端末トリガが「Actions が回るまでの隙間」を埋めるので、Actions 側を試合終了直後に張り付かせる必要はない。**4時間おきで十分**という設計判断。

---

## 1. 無料枠の制約（設計の出発点）

**リポジトリが private なので、Actions の無料枠は月2000分。** ここを外すと途中で止まる。

リポジトリを public にすれば分数は無制限になるが、`club_extra.json`（Jリーグ公式由来）がリポジトリに入っているため **public 化は選択肢に入れない**。

### 実行時間の試算

| ステップ | 目安 | 備考 |
|---|---|---|
| fetch_batch（増分・6〜10節） | 約30秒 | sleep 2.5秒/節が支配的 |
| fetch_batch（全38節×3リーグ） | 約345秒 | 実測値。**毎回はやらない** |
| build_calendar | 数秒 | |
| standings ×3 | 数秒 | |
| simulate ×3（10000試行＋インパクト） | 60〜120秒 | |
| fetch_news（Google News×60クラブ＋2フィード） | 60〜180秒 | |
| fetch_official（60クラブ） | 60〜120秒 | |
| stats ×3 / build_dist / build_ics | 約20秒 | |
| git 操作 | 約10秒 | |
| **1回あたり合計** | **約6分** | |

4時間おき（1日6回）→ 6分 × 6回 × 31日 = **約1120分/月**。2000分に対して余裕がある。

3時間おき（1日8回）にすると約1490分になり、全38節フル同期や再実行の余地がなくなる。**4時間おきを推奨する。**

---

## 2. ワークフロー構成

2つ作る。

```
.github/workflows/update.yml   定期バッチ（本体）
.github/workflows/test.yml     push時にテストを走らせる（軽量）
```

`test.yml` は `scripts/test_*.py` を実行するだけ。定期バッチ側でテストを回すと毎回時間を食うので、テストは push 時だけにする。

### 依存

サードパーティ製の依存は `requests` のみ（他は全て標準ライブラリ）。**`requirements.txt` を新設する。**

```
requests>=2.31
```

Python は `actions/setup-python` で **3.12** を指定する。ローカルは 3.10.12 だが、コードは `X | None` 記法などを使っており 3.10 以上なら動く。`test.yml` が 3.12 で通ることを先に確認してから定期バッチを有効化すること。

---

## 3. update.yml の設計

### スケジュール

```yaml
on:
  schedule:
    - cron: "30 */4 * * *"   # UTC
  workflow_dispatch:          # 手動実行も可能にする
```

**JST では 01:30 / 05:30 / 09:30 / 13:30 / 17:30 / 21:30。**

30分ずらしているのは意味がある。ちょうどの時刻だと 19:00 キックオフの試合が終わった直後に当たり、`eventsround.php` のステータスがまだ `FT` に変わっていないことがある。21:30 なら反映済みになっている。14:00 キックオフの試合も 17:30 の回で確実に拾える。

`workflow_dispatch` は必ず付けること。手動で回せないと、失敗したときの再実行が面倒になる。

### 基本設定

```yaml
permissions:
  contents: write        # push するために必要。これ以外は付けない

concurrency:
  group: update-batch
  cancel-in-progress: false   # 走行中のバッチを途中で殺さない

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 20
```

`cancel-in-progress: false` が重要。実行中のバッチを途中でキャンセルすると、一部だけ更新された中途半端な状態でコミットされうる。

---

## 4. 増分取得（無料枠を守る肝）

**毎回38節×3リーグを取り直さない。** `fetch_batch.py` には既に `--rounds 1-5` 形式のオプションがあるので、これを動的に組み立てて渡す。

### 取得対象の決め方

```
1. 既存の {league}_matches.json を読む
2. 未消化(finished=false)の試合が属する round のうち、kickoffJst が現在時刻 +14日以内のもの
3. 直近で消化された試合が属する round（スコア訂正・後追い反映の回収用）
4. 2と3の和集合。空なら最小1節は取る
```

通常は1リーグ2〜3節、3リーグで6〜10リクエスト、約30秒で終わる。

### 週1回のフル同期

**日曜 JST 01:30 の回だけ全38節を取得する。** 日程変更・延期の差し替えは未来の節に対しても起きるため、増分取得だけでは取りこぼしが蓄積する。

ワークフロー内で曜日と時刻を見て分岐するか、`--full` 相当の入力を `workflow_dispatch` から渡せるようにする。フル同期の回は約6分余計にかかるが、月4回なので枠には影響しない。

---

## 5. 失敗時の扱い

**壊れたデータをコミットしないことが最優先。**

- `fetch_utils.py` には既にサーキットブレーカー（N節連続で異常なら中断）がある。これが発動した場合、`fetch_batch.py` は異常終了する。
- **fetch_batch が失敗したら、以降のステップを全てスキップしてジョブを fail させる。** standings や simulate を欠損データの上で走らせない。
- `fetch_news` / `fetch_official` の失敗は**致命的ではない**。これらが落ちても、試合データ側の更新はコミットしてよい。ステップに `continue-on-error: true` を付け、最後にサマリを出す。
- private リポジトリの Actions が失敗すると、GitHub がオーナーにメールを送る。追加の通知設定は不要。

---

## 6. コミットと push

### コミット対象

```
data/processed/     （全JSON）
data/history/       （probability_history × 3、ics_state.json）
dist/               （build_dist の出力一式）
```

**`data/history/` を必ずコミットすること。** Render のディスクは揮発するため、確率の推移履歴と ics の SEQUENCE 状態はリポジトリ内にあることだけが永続化の担保になっている。ここを `.gitignore` に入れたり、コミット対象から漏らしたりすると、配信済みの ics が壊れる。

### 変更が無ければコミットしない

```bash
git add -A data/processed data/history dist
git diff --cached --quiet && echo "変更なし" && exit 0
```

試合が無い時間帯は毎回これで終わる。無駄なコミットとデプロイを増やさない。

### ローカル実行との競合

自分の PC で `update_all.py` を回して push することも引き続きあるため、Actions 側は競合前提で書く。

```bash
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -m "auto: データ更新 $(date -u -d '+9 hours' '+%Y-%m-%d %H:%M') JST"

# push 前に必ず rebase。失敗したら2回までリトライ
for i in 1 2 3; do
  git pull --rebase origin main && git push origin main && break
  sleep 5
done
```

コミットメッセージには JST の時刻を入れる。UTC のままだと後から履歴を追うときに混乱する。

---

## 7. 併せて直すこと：meta.json の肥大化

`data/processed/meta.json` の `runs` 配列は実行のたびに1件ずつ追記される。現在7件だが、**1日6回 × 365日で年2190件**になる。

**末尾200件で切り詰める処理を `fetch_batch.py` に入れること。** 今のうちに直しておかないと、静かに肥大化してフロントの読み込みを重くする。

---

## 8. Render 側の確認

- Auto-Deploy が有効になっていること。Actions の push でデプロイが走る。
- **1日6回デプロイが走ることになる。** Render の無料 Static Site のビルド回数・帯域の制限に引っかからないか、ダッシュボードで確認しておくこと（`dist/` をコミット済みなのでビルド自体は軽いはずだが、上限の有無は未確認）。
- デプロイ後、`dist/deploy-time.txt` の表示が Actions の実行時刻になることを確認する。

---

## テスト・確認手順

1. **先に `test.yml` を作り、Python 3.12 で既存テストが全て通ることを確認する。** ここが通らないうちに定期バッチを有効化しない。
2. `update.yml` を `workflow_dispatch` のみで作り、手動実行で最後まで通ることを確認する。
3. 手動実行を2回連続で回し、2回目が「変更なし」で終わることを確認する。
4. `data/history/` の3ファイルと `ics_state.json` がコミットに含まれていることを確認する。
5. `dist/ics/137715.ics`（湘南）の `SEQUENCE` が、Actions 実行をまたいでもリセットされないことを確認する。**ここが壊れると購読済みカレンダーが全滅するので必ず見る。**
6. わざと fetch_batch を失敗させ（存在しないリーグIDなど）、以降のステップがスキップされてコミットが発生しないことを確認する。
7. ローカルで先に push した状態で Actions を走らせ、rebase して push できることを確認する。
8. 問題なければ `schedule` を有効化する。
9. 有効化から数日後、リポジトリの Settings → Billing で Actions の消費分数を確認し、試算どおり（1日あたり40分前後）に収まっているか検証する。

---

## 実装順の推奨

1. `requirements.txt` と `test.yml`
2. `meta.json` の切り詰め（第7章）
3. `fetch_batch.py` に増分取得の節決定ロジックを追加（第4章）
4. `update.yml` を `workflow_dispatch` のみで作成
5. 手動実行で検証（確認手順1〜7）
6. `schedule` を有効化
