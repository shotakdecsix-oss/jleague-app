# 第13弾 実装指示：試合内容の詳細(得点者・カード・交代)へのドリルダウン

対象: `scripts/fetch_match_events.py`(新規)、`scripts/team_matching.py`(関数流用のみ、変更なし)、
`.github/workflows/`(試合日の頻度アップ用に1本追加)、`index.html`(ドリルダウンUI)

前提ドキュメント: `docs/handoff-jleague-dashboard.md`、`docs/prompt-jleague-impl-10.md`(オンデマンド更新)

---

## 1. 背景と、遠回りした理由

もともと TheSportsDB の `lookuptimeline.php` / `lookuplineup.php` で得点者・スタメンが取れないか
実測したが、**J1・J2問わず全部 `null`**だった(主要カードのJ1ダービーで試しても同じ)。ここは
あきらめる。

次に jleague.jp 公式サイト(選手データで既に使っている Next.js サイト)の個別試合ページを実測。
得点者・カード・交代は**取れることを確認済み**(4章で詳述)。ただし**CORSが許可されていない**こと
をブラウザの実機コンソールで確認済み(`Access-Control-Allow-Origin` ヘッダ無し)。

**したがって、この機能は前回(第10弾オンデマンド)のようなクライアント直取得では作れない。**
`scripts/fetch_official.py`(選手データ)と同じ、**サーバー側(GitHub Actions)でスクレイピングして
静的JSONとして配信する**方式一択。

この結果、副作用として**更新頻度の問題**が生まれる。既存の Actions は4時間おき
(01:30/05:30/09:30/13:30/17:30/21:30 JST)なので、そのままだと進行中の試合の得点者は
最悪、試合が始まって終わるまで一度も反映されない。→ 6章で試合日だけ頻度を上げる専用workflowを追加する。

---

## 2. 得点者・カード・交代の在り処(実測で確認済み)

### 2-1. 個別試合ページのURL

```
https://www.jleague.jp/match/{competition}/{year}/{6桁コード}/{サブページ}/
```

- `{competition}` = `j1` / `j2` / `j3`(競技ページのURL上のリーグ区分。マスタの`league`と同じ)
- `{6桁コード}` = `MMDD` + その日の中での2桁連番(例: `082208`)。**規則的に計算できない**
  (連番の順序が保証されない)。**必ず3章のスケジュール一覧ページから拾うこと。**
- `{サブページ}` = **`livetxt`固定でよい**。

### 2-2. review/ ではなく livetxt/ を使うこと(重要)

当初は「終了済みの試合は `review/`(結果ページ)、進行中は `livetxt/`(速報ページ)」と
使い分けるつもりだったが、実測の結果 **`review/` にはカード・交代のデータが一切埋め込まれていない**
(得点だけは入っている)。**`livetxt/` は試合が終わったあとも同じ内容が見られ、得点・カード・交代の
3種類とも揃っている。** ライブでも終了後でも `livetxt/` だけを使えばよい。`review/` は今回使わない。

### 2-3. データの埋め込み方式

選手データ(`fetch_official.py`)と同じ **Next.js の RSC ストリーミングペイロード**
(`<script>self.__next_f.push([1,"..."])</script>`)。抽出関数はそのまま流用できる。

```python
NEXT_F_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)
CHUNK_LINE_RE = re.compile(r"^([0-9a-f]+):(.*)$")

def extract_next_chunks(html: str) -> dict[str, str]:
    payloads = NEXT_F_RE.findall(html)
    full = "".join(json.loads(p) for p in payloads)
    chunks: dict[str, str] = {}
    for line in full.split("\n"):
        m = CHUNK_LINE_RE.match(line)
        if m:
            chunks[m.group(1)] = m.group(2)
    return chunks
```

(`fetch_official.py` の同名関数と完全に同じ実装。新規に書かず、共通化するか丸ごとコピーしてよい)

---

## 3. スケジュール一覧ページ(6桁コードの引き方)

```
https://www.jleague.jp/match/{j1|j2|j3}/
```

このページは「今節」の試合一覧を返す(過去の特定日を指定するクエリは不要、常に最新)。
1リーグにつき1回のGETで、その節の全試合の `{6桁コード}` と対戦カード名・キックオフ時刻が拾える。

抽出パターン(実測・検証済み):

```python
CODE_RE = re.compile(r'/match/(j\d)/2026/(\d{6})')
TEAM_RE = re.compile(
    r'm-schedule__team-name","ref":"\$undefined","data-media":"pc","children":"([^"]+)"'
)
KO_RE = re.compile(r'"children":\["(\d{1,2}:\d{2})"," KO"\]')

# chunks(3章の extract_next_chunks の戻り値)を全部なめて、
# CODE_RE にマッチするchunkだけを見る。1試合につき複数chunkがヒットすることがあるので
# コードで重複排除する。TEAM_RE は home/away の順で2件ヒットする(1件目home, 2件目away)。
```

**年は`2026`をハードコードしない。** `master.meta.season`(例: `"2026-2027"`)の開始年を使う。
シーズンをまたぐ場合の考慮は今回は不要(このアプリはシーズン単位で運用しているため)。

自分の `{league}_matches.json` と突き合わせるキーは **日付ではなくチーム名のペア**にすること。
`m-schedule__game-over-text` 等でキックオフ延期の表記が入ることがあり、日付が動くケースが
あるため(第8弾の他会場インパクトと同じ理由で日付を信用しない方針を踏襲する)。

チーム名の照合は `scripts/team_matching.py` の正規化ロジック(全角/半角、クラブ名の表記ゆれ)を
そのまま使うこと。新規に書かないこと。

---

## 4. livetxt/ ページのパース(得点・カード・交代)

以下は `data/tmp/sample_match_livetxt.html`(2026-08-22、札幌vs大宮、実際に4-1で終了した試合)と
`data/tmp/sample_match_review.html`(過去の別の札幌vs大宮戦、得点5、既知の正解と突き合わせ済み)の
**2つの実サンプルに対して実際に動かし、正解(得点者・分・スコア推移)と完全一致することを検証済み**の
正規表現。そのまま使ってよい。

```python
GOAL_RE = re.compile(
    r'"div","(?P<minute>[0-9+]+)",\{.*?"\$L\w+",null,\{'
    r'"club":\{"name":"(?P<club>[^"]+)".*?'
    r'"player":\{"name":"(?P<player>[^"]+)","position":"(?P<position>[^"]+)"'
    r'.*?"children":\["GOAL!"," "\]\}\]'
    r'(?:,\["\$","span",null,\{[^}]*"children":"(?P<score>\d+-\d+)")?',
    re.S,
)

CARD_RE = re.compile(
    r'"cardType":"(?P<type>yellow|red)","playerName":"(?P<player>[^"]+)"'
    r',"playerPosition":"(?P<position>[^"]+)".*?"teamName":"(?P<club>[^"]+)"'
)
CARD_MINUTE_RE = re.compile(r"widget-container-(?P<minute>[0-9+]+)'")

SUB_BLOCK_RE = re.compile(r'"\$1","substitution-\d+-(?P<club>[^"]+)"')
SUB_MINUTE_RE = re.compile(r"widget-container-(?P<minute>[0-9+]+)'")
SUB_ITEM_RE = re.compile(
    r'"variant":"(?P<variant>in|out)".*?"children":\["(?P<pos1>[A-Z]+)"," ","(?P<pos2>\d+)"\]\}\],'
    r'\["\$","p",null,\{"className":"[^"]*item-details--name"[^}]*"children":"(?P<name>[^"]+)"',
    re.S,
)
```

抽出の手順(`chunks = extract_next_chunks(html)` の後):

```python
def find_goals(chunks):
    out = []
    for cid, v in chunks.items():
        if "GOAL!" not in v:
            continue
        for m in GOAL_RE.finditer(v):
            out.append({"minute": m["minute"], "club": m["club"], "player": m["player"],
                        "position": m["position"], "scoreAfter": m["score"]})  # scoreはNoneのことがある
    return out

def find_cards(chunks):
    out = []
    for cid, v in chunks.items():
        m_min = CARD_MINUTE_RE.search(v)
        for m in CARD_RE.finditer(v):
            out.append({"minute": m_min["minute"] if m_min else None, "type": m["type"],
                        "player": m["player"], "position": m["position"], "club": m["club"]})
    return out

def find_subs(chunks):
    out = []
    for cid, v in chunks.items():
        if "選手交代" not in v:
            continue
        items = SUB_ITEM_RE.findall(v)
        if not items:
            continue  # 注意: プレビュー記事本文にも「選手交代」という日本語が出てくることがある。
                       # 実データ(IN/OUT)が取れなかったchunkは無視すること(誤検出防止)。
        m_club = SUB_BLOCK_RE.search(v)
        m_min = SUB_MINUTE_RE.search(v)
        out.append({"minute": m_min["minute"] if m_min else None,
                    "club": m_club["club"] if m_club else None,
                    "items": [{"variant": it[0], "position": it[1] + " " + it[2], "name": it[3]} for it in items]})
    return out
```

### 4-1. 分の表記について

- `minute` は文字列(`"78"`, `"90+5"` など)。**第12弾のライブスコアと同じ方針で、数値パースせず
  そのまま表示に使う。**
- goalのdiv keyは通常は分と一致するが、**一致しない(reactのリスト内インデックスがたまたま入る)
  ケースが確認できている**(実測で `"95"` というキーが付いていたが、これは前半・後半セクション内での
  通し番号で、実際の得点シーンの時間は本文中の別要素にある)。**分の値がずれることがある前提で扱うこと。**
  致命的ではない(得点者名・チーム・スコア推移のほうが主情報)。ずれが気になる場合は将来の改善課題とし、
  今回は「分」を参考情報として扱う(見出しに小さく出す程度でよい)。
- `scoreAfter` は取れないことがある(実測で1件、`None`になるケースを確認済み)。**Noneなら
  「そのゴールの得点後スコア」を表示しない**(既存の試合結果のスコアで足りる)。

### 4-2. club名の表記

`"北海道コンサドーレ札幌"` `"ＲＢ大宮アルディージャ"` のように**フルネームの日本語**(全角)。
自分の `master.teams[].ja` と表記が一致するとは限らない(全角/半角、法人格の有無など)。
`team_matching.py` の正規化・突き合わせロジックを再利用すること。**新規の突き合わせロジックを
書かないこと**(選手データの突き合わせで既にハマりどころを潰してあるはず)。

home/awayの判定は、club名をidTeamに変換したうえで、自分の `matches.json` の該当試合の
`home.idTeam` / `away.idTeam` と比較して決めること(club名の文字列だけでhome/awayを判定しない)。

---

## 5. 出力形式とマージ方針

`data/processed/{league}_match_events.json` を新規に作る(既存ファイルは一切変更しない)。

```json
{
  "meta": { "generatedAtJst": "2026-08-22 21:15 JST", "league": "j2" },
  "matches": {
    "2551727": {
      "code": "082208",
      "goals": [
        { "minute": "9", "idTeam": "139893", "player": "カルリーニョス ジュニオ", "position": "FW 9", "scoreAfter": "0-1" }
      ],
      "cards": [
        { "minute": "48", "type": "yellow", "idTeam": "139893", "player": "豊川 雄太", "position": "FW 10" }
      ],
      "subs": [
        { "minute": "56", "idTeam": "137706",
          "in": { "name": "大森 真吾", "position": "FW 23" },
          "out": { "name": "白井 陽斗", "position": "FW 71" } }
      ]
    }
  }
}
```

キーは `idEvent`(TheSportsDBのものと同じ文字列。既存の`matches.json`とそのまま突き合わせられる)。
`idTeam` は club名文字列をteam_matching経由で変換した自分のidTeam。**変換できなかった場合は
その1件(goal/card/subの1要素)だけをスキップし、試合全体は捨てない**(部分的に不明な選手がいても
残りは出す)。

### 5-1. 取得対象の絞り込み(全試合を舐めない)

第10弾のオンデマンド取得(`pickTargetRounds`)と同じ考え方。**キックオフ時刻が現在時刻の前後36時間
以内の試合だけ**を対象にする。それ以外(遠い未来・遠い過去)はそもそも`livetxt/`を叩かない。

```python
EVENTS_WINDOW_HOURS = 36
```

一致した試合が0件のリーグはスケジュール一覧ページ自体を叩く必要はあるが(1リクエストなので軽い)、
`livetxt/`への個別アクセスは発生しない。

### 5-2. 安全側のマージ(既存データを消さない)

その試合の `livetxt/` 取得が失敗(HTTPエラー・chunk抽出失敗)した場合、**その試合のキーを
出力から削除しない**(前回成功時の内容をそのまま引き継ぐ)。第11弾の増分マージ事故の教訓を踏まえ、
**「今回取れなかった」と「そもそも試合が無い」を区別すること。**

```
1. 前回の {league}_match_events.json を読む(無ければ空)
2. 対象試合(5-1のウィンドウ内)についてだけ livetxt/ を取得・パースする
3. 取得できた試合は上書き、失敗した試合は前回の値をそのまま残す
4. ウィンドウ外に出た試合(36時間より前)のキーは、次回以降そのまま放置してよい
   (削除しない。ファイルは徐々に大きくなるが、1シーズン分でも数百件程度なので問題にならない)
```

激減ガードのような仕組みは不要(この出力はマージ元がそもそも「今回分だけ上書き」ではなく
「対象分だけ差分更新」なので、構造的に丸ごと上書き事故が起きない)。

---

## 6. GitHub Actions: 試合日は頻度を上げる

既存の `update.yml`(4時間おき、フルパイプライン)は変更しない。**この機能専用の軽量workflowを
新規に追加する**(`fetch_match_events.py` だけを実行する。他のスクリプトは呼ばない)。

```yaml
# .github/workflows/match_events.yml (新規)
name: Match events (frequent, matchdays only)
on:
  schedule:
    # JST 13:00-22:00 の土日、20分おき。UTCではJST-9h: 04:00-13:00 の土日。
    - cron: "*/20 4-12 * * 6,0"
  workflow_dispatch: {}

jobs:
  fetch-events:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python scripts/fetch_match_events.py
      - run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/processed/*_match_events.json
          git diff --staged --quiet || git commit -m "auto: 試合詳細データ更新 $(date '+%Y-%m-%d %H:%M JST' -d '+9 hours')"
          git push
```

平日夜の試合(ナイトゲーム・カップ戦の振替等)はこの専用workflowの対象外になる。土日だけに絞ったのは
無料枠(月2000分)への配慮という判断による(第11弾の見積りは既存の4時間おきで約1120分/月)。
平日の試合は次の4時間おきの`update.yml`実行を待つ形になる(得点者情報の反映が遅れるだけで、
アプリ自体は壊れない)。

**Actions minutesの検算**: 土日各9時間 × 20分おき = 1日27回 × 2日 = 週54回 × 約4.3週 ≈ 232回/月。
このworkflowは軽量(該当試合が無い時間帯はスケジュール一覧の3リクエストだけで即終了)なので
1回あたり1分以内に収まる想定。既存の約1120分と合わせても2000分の枠内に収まる。

---

## 7. フロントエンド: ドリルダウンUI

### 7-1. 置き場所

- クラブモードの「日程」タブ: 各試合行をタップすると詳細が開く(既存の行タップの挙動は現状どうなって
  いるか確認し、無ければ新規に追加する)。
- 全体モード日程タブも同様。
- ホームタブの「試合中」カード(第12弾)・「直近の結果」もタップで開けるとよい。

専用の別画面は作らず、**折りたたみ(アコーディオン)かモーダルで既存の行の下/上に展開する**方式を
推奨(第9弾の予想順位タブで採用した「帯+タップ」のUIパターンを踏襲できる)。

### 7-2. データの読み込み

`data/processed/{league}_match_events.json` を、日程タブを開くタイミングで遅延ロードする
(常時ロードしない。既存の `ensureXxx()` 関数群と同じパターンで `ensureMatchEvents(league)` を作る)。

該当試合が `matches` オブジェクトに無い場合(取得対象外だった、まだ試合前、等)は「詳細情報は
まだありません」のような表示にする(空エラーを出さない)。

### 7-3. 表示内容

- 得点: `分′ プレイヤー名(スコア推移があれば併記)` を時系列(分の昇順、`90+5`のような表記は
  数値化せず文字列比較でよい程度のソートで十分)。自クラブの得点かどうかで色分け・強調してもよい
  (既存の`.me`パターンを踏襲)。
- カード: `分′ 🟨/🟥 プレイヤー名`。
- 交代: `分′ IN プレイヤー名 / OUT プレイヤー名`。

### 7-4. 注意

- **これは進行中スコア(第12弾のlivescore)や順位計算とは完全に別データ**。混ぜないこと。
- データの鮮度(取得時刻)は `meta.generatedAtJst` を出す(既存の「データの基準」ポップオーバーに
  行を1つ足す形でよい)。
- 分の表記ずれ(4-1章)がある前提で、「大まかな時系列」として見せる。分単位の厳密な正確性を
  売りにしない。

---

## 8. テスト方針

`scripts/test_fetch_match_events.py` を新規作成する。

- `data/tmp/sample_match_livetxt.html` を読み込み、`find_goals`/`find_cards`/`find_subs` が
  それぞれ既知の正解件数(得点5・カード3・交代10 ※実際に取得した最新のサンプルの件数で確認すること。
  本書作成時点のサンプルでは終了間際の追加点を含め得点5だった)と一致することを確認する。
  **サンプルHTMLはリポジトリにコミットしない**(`data/tmp/`は`.gitignore`対象、個人利用の生データを
  リポジトリに含めない方針を踏襲)。テストは「サンプルが存在すればそれを使う、無ければスキップする」
  という作りにすること(CI環境にサンプルが無くて落ちるのを防ぐ)。
- スケジュール一覧ページのコード抽出について、`sample_match_schedule.html` を使って同様に検証する。
- マージ方針(5-2章)の単体テスト: 前回データがあり今回一部の試合の取得が失敗したとき、失敗した試合の
  キーが前回の値のまま残ることを確認する。

---

## 9. 確認手順

1. `python scripts/fetch_match_events.py` を手元で1回実行し、`data/processed/j2_match_events.json`
   に今日の試合(得点者含む)が入ることを確認する。
2. 得点者・カード・交代の人数/件数が、実際のjleague.jpの速報ページの表示と一致することを目視確認する。
3. アプリの日程タブで対象試合をタップし、詳細が展開されること。
4. 対象外の試合(36時間ウィンドウの外)をタップしたとき、エラーにならず「まだありません」等の表示に
   なること。
5. 平日夜の試合(専用workflow対象外の時間帯)で、次の4時間おき実行まで反映されない挙動を許容できるか
   最終確認する(気になるようならcron範囲を広げる相談をすること)。
