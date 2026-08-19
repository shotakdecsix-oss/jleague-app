# 実装指示：第7弾（スマホからのアクセス／Renderへのデプロイ）

他プロジェクト（`wrapper-portal` / `shopping-memo`）の前例に合わせ、**GitHub → Render の Static Site** で公開します。
データが要るものは Supabase、という前例も踏襲できる形にしておきます。

---

## 0. 最初に押さえること：**Renderの静的サイトのURLは公開されます**

URLを知らなければ辿り着けないだけで、アクセス制限はありません。**このプロジェクトには
Jリーグ公式由来のデータが入っており、それを公開すると利用規約違反になります。**

そこで**2段階**に分けます。

| | 内容 | 公開してよいか |
|---|---|---|
| **第1段（今回）** | TheSportsDB由来＋RSS由来だけを配信 | **問題なし** |
| **第2段（次回）** | 公式由来データも含む全部入りを、パスワードで暗号化して配信 | 暗号化により非公開扱い |

第1段だけでも、**次の試合・直近の結果・全日程・順位表・昇格確率・自前計算のスタッツ**がスマホで見られます。
公式由来（選手一覧・公式スタッツ・公式ニュース）だけが第2段送りです。

---

## 1. `scripts/build_public.py` を作る（新規）

`dist/` に**公開してよいものだけ**をコピーするビルドスクリプトです。

### 含めるもの

```
index.html
data/masters/*.json                     ← 自前マスタ。公開可
data/processed/j1_matches.json  他      ← TheSportsDB由来。公開可
data/processed/*_standings.json
data/processed/*_simulation.json
data/processed/*_stats.json             ← ただし後述の除外あり
data/processed/news.json                ← ただし後述のフィルタあり
```

### 含めないもの（**絶対に**）

```
data/processed/club_extra.json          ← Jリーグ公式由来
data/processed/live.json                ← 公式由来なら同様
data/tmp/                               ← 公式ページのHTMLそのもの
scripts/ docs/ data/config/ data/fixtures/
```

### 2つのフィルタ

**(a) `news.json` は `sourceType` で絞る**

`sourceType == "official"` の記事（`club_extra.json` 由来＝Jリーグ公式サイトの見出し）を**落として**ください。
ゲキサカ・サッカーキング・Google News は **RSSという配信を前提とした仕組みで公開されているもの**なので、
見出しとリンクを載せることに問題はありません。ここが線引きです。

**(b) `*_stats.json` は `source` で絞る**

`source == "official"` の指標（ボール保持率・パス数・走行距離・スプリント・公式の平均得点/無失点数）を
**落として**ください。`source == "computed"` の指標だけを残します。

- 落とした結果、**レーダーチャートの「公式スタッツ」グループは軸が0本になります。**
  第5弾改訂2の「軸が3本未満のグループは図を出さない」規則がそのまま効くので、フロントの改修は不要のはずです。
  効かない場合はそこを直してください

### 出力時の検証（**必須**）

ビルドの最後に、`dist/` を走査して次を確認し、1つでも見つかったら**エラーで停止**してください。

- `club_extra` / `live.json` / `data/tmp` というパスが存在しない
- `dist/**/*.json` の中に `"sourceType": "official"` と `"source": "official"` が1件も無い

**この検証が、規約を守る最後の砦です。**必ず入れてください。

## 2. フロントを「公式データが無い状態」で確認する

`index.html` は既に `club_extra.json` が無くても落ちない作りになっているはずです（第4弾追補の確認項目）。
`dist/` をローカルで配信して、以下を確認してください。

```powershell
cd C:\Users\Shoichi\Desktop\projects\jleague-app; python scripts/build_public.py; cd dist; python -m http.server 8000
```

- 選手タブが「公式サイトで見る」のリンクだけになっていること（エラーにならないこと）
- スタッツタブが自前計算の指標だけで表示されること
- ニュースタブに記事が出ること（公式分が消えて件数は減ります）
- 他のタブが従来どおり動くこと

## 3. GitHubへpush

**リポジトリは private で作ってください。**（Renderは private リポジトリからでも無料でデプロイできます。
GitHub Pagesと違い、ここは制約になりません）

```powershell
cd C:\Users\Shoichi\Desktop\projects\jleague-app; git init; git add .; git commit -m "jleague dashboard init"; git branch -M main; git remote add origin https://github.com/shotakdecsix-oss/jleague-app.git; git push -u origin main
```

**push前に `.gitignore` を必ず確認してください。**以下が入っていること。

```
data/processed/club_extra.json
data/processed/live.json
data/tmp/
__pycache__/
```

`dist/` は**コミットします**（Renderがビルドせずそのまま配信するため）。

## 4. Renderで公開

`wrapper-portal/SETUP.md` と同じ手順です。

1. Renderダッシュボード → **New** → **Static Site**
2. 上のGitHubリポジトリを選択
3. **Build Command**: 空欄
4. **Publish Directory**: `dist`
5. Deploy

発行されたURLをスマホで開けば完了です。**このURLは他人に共有しないでください**（規約上は問題ない構成ですが、
第2段で公式データを載せたあとに共有すると違反になります）。

## 5. 更新の流れ

```powershell
cd C:\Users\Shoichi\Desktop\projects\jleague-app; python scripts/update_all.py; python scripts/build_public.py; git add .; git commit -m "data update"; git push
```

pushを検知してRenderが自動で再デプロイします。**`update_all.py` の最後に `build_public.py` を呼ぶ**ようにして
おくと、1コマンドで済みます。

## 6. ホーム画面に追加できるようにする（PWA化・軽め）

スマホで「アプリらしく」なるので、ついでに入れてください。

- `manifest.webmanifest` を置く（`name` / `short_name` / `start_url: "."` / `display: "standalone"` /
  `theme_color` と `background_color` は**湘南の色 `#82c039` を既定**にする）
- `<link rel="manifest">` と `<link rel="apple-touch-icon">` を `index.html` に追加
- アイコンは、**クラブカラーの円にリーグ名の文字を置いただけのSVGを自前生成**でよい。
  **クラブのロゴは使わないこと**（商標なので、第2段で非公開にしても持ち込まない方針を維持）
- Service Worker は**今回は入れない**。オフラインキャッシュは、古い順位表を最新だと誤認させる事故のもとです

---

## 次回（第2段）の見通し

公式由来データもスマホで見たい場合の道筋です。**今回は着手不要**ですが、方針だけ残します。

- **PageCrypt** を使う。ビルド時にHTMLをパスワードで暗号化し、ブラウザ側で復号する仕組みで、Renderの
  公式ブログでも紹介されている方法です
- ただし**PageCryptが暗号化できるのは単一のHTMLファイルだけ**で、別ファイルのJSONは保護されません。
  そのため、**全データを `index.html` にインライン展開してから暗号化**する必要があります
- 全部入りで3〜4MB程度になる見込みです。`club_extra.json` の `seasonalPerformances`（35年ぶん×60クラブ）
  など、画面で使っていないものを削れば圧縮できます
- 暗号化には Node.js が要ります（`npx pagecrypt`）。**`node -v` が通るか先に確認してください。**
  入っていなければ、Supabase Auth を使う方式（`wrapper-portal` と同じ手口）に切り替えます
