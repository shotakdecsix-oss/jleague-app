# 実装指示：第2弾の修正（レビュー指摘4件）

第2弾の実装をレビューしました。**テストが通っている範囲は問題ありません**（429の固定75秒待機、`.json()`を呼ばない分岐、ミニリーグ比較、`playedDiff`、純粋関数の切り出しはすべて指示どおり）。
以下4件だけ直してください。優先度順です。

---

## 【重大】1. `standings.py`：ミニリーグが未完のとき順位が捏造される

### 現象（実際に再現確認済み）

A・B・Cが勝点3／得失点差0／総得点1で完全同値。直接対決はA-Bの1試合（Aの勝ち）のみで、**Cは3者間の対戦が0試合**。

```
現状の出力: [['A'], ['C'], ['B']]   # Cが2位に入ってしまう
期待:       [['A', 'B', 'C']]        # 比較不能なので3者同順位
```

### 原因

`_resolve_tie_cluster()` の `mini_key()` が、ミニリーグに1試合も出ていないチームに `(0, 0, 0)` を返している。
Bは直接対決で負けているので `(0, -1, 0)`。結果、**対戦していないCが、負けたBより上に来る**。

`if not h2h` のガードは「クラスタ全員が1試合も対戦していない」場合しか捕まえられていない。

### 修正

ミニリーグが完全に埋まっているとき（**クラスタ内の全ペアが最低1試合を消化しているとき**）だけ順序をつける。
1ペアでも未消化なら比較不能としてクラスタ全員を同順位にする。

```python
from itertools import combinations

def _resolve_tie_cluster(cluster: list[str], matches: list[dict]) -> list[list[str]]:
    cluster_set = set(cluster)
    h2h = _head_to_head_matches(cluster_set, matches)
    if not h2h:
        return [list(cluster)]

    # 全ペアが最低1試合を消化しているか確認する。
    # 1ペアでも未消化ならミニリーグは成立しない -> 比較不能、全員同順位。
    met = set()
    for m in h2h:
        met.add(frozenset((m["home"]["idTeam"], m["away"]["idTeam"])))
    if any(frozenset(pair) not in met for pair in combinations(cluster, 2)):
        return [list(cluster)]

    ...  # 以降は現状のまま
```

2回戦総当たりなのでシーズン終了時は全ペアが2試合を消化しており、最終順位の挙動は変わりません。
変わるのはシーズン中盤だけで、そこでは「無理に順序をつけない」のが元々の設計方針です。

### テスト追加（`test_standings.py`）

- 3クラブが並び、A-Bのみ直接対決あり・Cは0試合 → **3クラブとも同順位**（`tiedWith`に互いが入る）
- 3クラブが並び、全ペアが直接対決を消化済み → ミニリーグ順に3グループへ分かれる（既存ケースがこれならOK）

---

## 【重大】2. `fetch_batch.py`：レート制限で中断したのにフォールバックで再突入する

現状の判定はこれだけです。

```python
if not fetch_result.ok_rounds:
    season_used = SEASON_FALLBACK
    fetch_result = fetch_all_rounds(...)   # 38節をもう一周
```

429が続いて `fetch_all_rounds` が `aborted=True` で打ち切られた場合も `ok_rounds` は空なので、
**レート制限を踏んでいる真っ最中に、もう一度同じリーグへ38節ぶん投げに行きます。** 状況を悪化させるだけです。

シーズン文字列のフォールバックは「APIは正常に応答しているが `2026-2027` に該当データが無い」ときだけ意味があります。

```python
if not fetch_result.ok_rounds and not fetch_result.aborted:
    ...  # フォールバックへ
```

`aborted` のときはフォールバックせず、そのまま異常終了させてください（現状どおり出力はしない）。

---

## 【中】3. `fetch_batch.py`：`strTimestamp` が null / 欠損だと落ちる

```python
kickoff = derive_kickoff_jst(ev["strTimestamp"])
```

`strTimestamp` が `null` の場合 `datetime.fromisoformat(None)` で `TypeError`、
キー自体が無ければ `KeyError` で、バッチ全体が止まります。

**延期試合が実在するリーグ**なので、日程差し替えの途中で `strTimestamp` が空になるケースは想定しておくべきです。
日程未定の試合を丸ごと落とすと日程表示から消えてしまうので、**試合は残して日時だけnullにする**のが正解です。

```python
ts = ev.get("strTimestamp")
if ts:
    kickoff = derive_kickoff_jst(ts)
    kickoff_iso, kickoff_date, kickoff_time, tbd = kickoff.iso, kickoff.date, kickoff.time, False
else:
    kickoff_iso = kickoff_date = kickoff_time = None
    tbd = True
```

- `match` に `"kickoffTbd": tbd` を追加する
- ソートで `None` が混ざると `TypeError` になるので `key=lambda m: m["kickoffJst"] or "9999"` として日時未定は末尾へ
- `build_records()` のソートも同様に `m.get("kickoffJst") or ""` で既に守られているのでそのままでよい
- 日時未定の件数を `meta.kickoffTbdCount` に出す（延期の検知に使う）

## 【中】4. `meta.json` の `requests` が実リクエスト数になっていない

```python
total_requests += len(fr.results)   # = 節数
```

これだと **429で待って撃ち直したぶんも、シーズンのフォールバックで撃ち直したぶんも数えていません。**
`meta.json` を置いている目的は「日次の累積リクエスト制限が未検証だから運用しながら見る」ことなので、
実際にAPIへ投げた回数を数えないと意味がありません。

- `fetch_all_rounds` が実リクエスト数を返す（`FetchAllResult.request_count` を追加し、`fetch_round_raw` の試行回数を積む）
- フォールバックした場合は1周目と2周目の両方を加算する
- `meta.json` に `"requests"`（実リクエスト数）と `"rounds"`（節数）を分けて記録する

---

## 参考：直さなくてよいと判断したもの

- `--rounds` が `"1-5"` 形式限定で `--rounds 5` を受けない → 検証用途なので現状で可
- 未一致チームがあった試合を `filteredOut` に数えていない → 未一致時はそのリーグを出力せず異常終了するので実害なし
- `finished=true` なのにスコアがnullの試合を `build_records` がスキップする一方、検算の分母には入る → 不正データ時に警告が出るだけなので現状で可
