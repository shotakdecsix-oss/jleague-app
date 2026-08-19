# 修正指示：ニュースが増えない原因（3つ重なっています）

`data/processed/news.json` が実質空です。

```json
{ "meta": { "generatedAtJst": "2026-08-17T01:23:47+09:00", "queryCount": 0, "failed": [] },
  "teams": {}, "obPlayers": {} }
```

---

## 原因1：`aliasesJa` が照合に届いていない（**これが本命**）

`team_matching.load_master_teams()` は **`league` / `idTeam` / `en` / `aliases` / `ja` の5つしか返しません。**
マスタに追加した **`aliasesJa` がここで落ちています。**

さらに `match_teams_in_text()` は `ja` と **英語の `aliases`** を照合しています。日本語のニュース記事に
`Shonan` や `Consadole Sapporo` は出てこないので、**実質「正式名フルネームが一字一句書かれた記事」しか
拾えていません。**

手元で再現しました。

```
'DF濃野がD.C.ユナイテッドへ完全移籍【鹿島】'  -> []          ← 鹿島が拾えない
'ベルマーレが山形に1-0で勝利、第2節'          -> []          ← 2クラブとも拾えない
'C大阪とFC大阪が対戦'                         -> ['FC大阪']  ← C大阪が拾えない
'湘南ベルマーレ、ホームで連勝'                -> ['湘南ベルマーレ']
```

見出しは「鹿島」「ベルマーレ」と略すのが普通なので、**ほぼ全滅**です。

### 修正

**(a) `load_master_teams()` に `aliasesJa` を通す**

```python
"aliasesJa": t.get("aliasesJa", []),
```

J1/J3のlist形式・J2のdict形式の**両方の分岐に入れてください。**

**(b) `match_teams_in_text()` の照合語を `ja` ＋ `aliasesJa` にする**（英語 `aliases` は使わない）

**(c) 長い語から順に照合し、マッチした部分を文字列から取り除く**

`docs/note-aliases-ja.md` に書いた規則です。まだ入っていません。「C大阪」が「FC大阪」の部分文字列なので、
これが無いとFC大阪の記事がセレッソ大阪に混入します。

```python
def match_teams_in_text(text, all_teams):
    norm = normalize_name(text)
    # (正規化した語, チーム) を長い順に並べる
    terms = []
    for team in all_teams:
        for cand in [team.get("ja")] + list(team.get("aliasesJa", [])):
            key = normalize_name(cand or "")
            if key:
                terms.append((key, team))
    terms.sort(key=lambda x: -len(x[0]))

    matched, seen = [], set()
    for key, team in terms:
        if key in norm:
            norm = norm.replace(key, "\u0000")   # マッチした部分を消費する
            if team["idTeam"] not in seen:
                seen.add(team["idTeam"])
                matched.append(team)
    return matched
```

**テストに入れるケース**

```
'DF濃野がD.C.ユナイテッドへ完全移籍【鹿島】'  -> 鹿島アントラーズ
'ベルマーレが山形に1-0で勝利'                 -> 湘南ベルマーレ, モンテディオ山形
'FC大阪が勝利'                                -> FC大阪のみ（セレッソ大阪に入らないこと）
'C大阪とFC大阪が対戦'                         -> 両方
```

## 原因2：`watchlist.json` の `teams` が空

```json
"teams": [],
```

**Google Newsのクラブ検索が1件も走っていません**（`queryCount: 0` がその証拠です）。

### 修正：Google Newsを**全60クラブ**に広げる

`watchlist.teams` に湘南を足すだけでは不足です。**クラブ別ニュースの最大の供給源はGoogle News**で、
そこを一部クラブに絞っているのが「少ない」の直接原因です。

- **`watchlist.teams` が空のときは全クラブを対象**にする（明示的に列挙したときだけ絞り込み）
- クエリは `ja` ＋ `aliasesJa` のうち**カタカナ愛称と、3文字以上の語だけ**を使う
  （「鹿島」のような2文字の語をGoogle Newsに単独で投げると、サッカー以外の記事が混ざります）
- 所要時間は60クラブ×2〜3クエリ×2秒で**5分程度**。日次バッチとして許容範囲です
- `watchlist.json` の `note` も、この挙動に合わせて書き換えること

## 原因3：毎回上書きしていて、蓄積しない

RSSは**最新20件程度しか返しません。**ゲキサカとサッカーキングは全国のサッカー全般を扱うので、
特定クラブに当たるのは1日あたり数件です。**それを毎回上書きしていては、何回実行しても増えません。**

### 修正：`news.json` を追記型にする

- 実行時に**既存の `news.json` を読み込み、新しく取れた記事とマージ**してから書き出す
- 重複排除は既存の `dedupe_news_items()` をそのまま使う（URL正規化＋タイトル一致）
- **`publishedJst` が60日より古い記事は捨てる**（無限に増やさない）
- 1クラブあたりの上限 `MAX_ITEMS_PER_TEAM` を **30 → 100** に上げる
- `meta` に `totalItems` と `newItems`（今回の実行で増えた件数）を出す。
  **増えているかどうかが一目で分かるようにするのが目的です**

これで、日次で回すほど過去記事が積み上がります。

---

## 確認手順

```powershell
cd C:\Users\Shoichi\Desktop\projects\jleague-app; python scripts/fetch_news.py
```

1. `[info]` に**クラブ別ニュースの件数が3桁で出ること**（現状は0件）
2. もう一度実行して、`newItems` が小さく、`totalItems` が減っていないこと（蓄積が効いている）
3. 湘南のニュースが10件以上あること
4. `C大阪` と `FC大阪` の振り分けテストが通ること

**なお、改修後の `fetch_news.py` はまだ一度も実行されていません**（`news.json` の日時が
コードの更新日時より古いままです）。上記を直したうえで実行してください。
