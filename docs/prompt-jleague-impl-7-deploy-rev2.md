# 実装指示：第7弾 改訂2（デプロイ／全データを配信する）

**改訂1の「0. 2段階に分ける」「1. `build_public.py` でデータを絞る」「2. 公式データ無しでの確認」を破棄します。**
`scripts/build_public.py` は作りません。データのフィルタも入れません。**全データをそのまま配信します。**

個人利用のダッシュボードであり、URLを共有せず、検索にも載せない前提です。

---

## 1. `.gitignore` から除外指定を外す

これまで「非公開前提」として外していたものを、**配信対象に戻します。**

```
# 以下の3行は削除する
data/processed/club_extra.json
data/processed/live.json
data/tmp/
```

**ただし `data/tmp/` だけは `.gitignore` に残してください。** 規約の話ではなく、
**公式ページの生HTMLが1ファイル1MBあり、リポジトリを無駄に太らせるだけ**だからです。デバッグ用の一時ファイルで、
アプリの動作には使いません。

```
data/tmp/
__pycache__/
```

## 2. `dist/` の作り方

フィルタが不要になったので、ビルドは**必要なファイルをコピーするだけ**です。
`scripts/build_dist.py` として作ってください（`build_public.py` ではなく）。

```
dist/
  index.html
  manifest.webmanifest
  robots.txt
  data/masters/*.json
  data/processed/*.json      ← club_extra.json と live.json も含む
```

`scripts/` `docs/` `data/config/` `data/fixtures/` `data/tmp/` はコピー不要です（配信に不要なだけ）。

`update_all.py` の最後に `build_dist.py` を呼ぶようにしておくと、更新が1コマンドで済みます。

## 3. 検索避けだけ入れる

URLが偶然人目に触れる経路は、実質的に検索エンジン経由だけです。ここだけ塞いでおきます。

**`dist/robots.txt`**

```
User-agent: *
Disallow: /
```

**`index.html` の `<head>`**

```html
<meta name="robots" content="noindex, nofollow">
```

これで十分です。パスワードや暗号化は入れません。

## 4. GitHubへpush（リポジトリは private）

```powershell
cd C:\Users\Shoichi\Desktop\projects\jleague-app; git init; git add .; git commit -m "jleague dashboard init"; git branch -M main; git remote add origin https://github.com/shotakdecsix-oss/jleague-app.git; git push -u origin main
```

`dist/` はコミットします（Renderがビルドせずそのまま配信するため）。

## 5. Renderで公開

`wrapper-portal/SETUP.md` と同じ手順です。

1. Renderダッシュボード → **New** → **Static Site**
2. 上のGitHubリポジトリを選択
3. **Build Command**: 空欄
4. **Publish Directory**: `dist`
5. Deploy

## 6. 更新の流れ

```powershell
cd C:\Users\Shoichi\Desktop\projects\jleague-app; python scripts/update_all.py; git add .; git commit -m "data update"; git push
```

pushを検知してRenderが自動で再デプロイします。

## 7. サイズの確認だけしておく

`club_extra.json` が1.34MBあります。**`dist/` 全体のサイズをビルド時に表示してください。**
スマホの通信量として無視できない大きさになったら、そのとき削る相談をします（`seasonalPerformances` の
35年ぶんなど、画面で使っていないものが候補です）。**いまは削りません。**

## 8. PWA化（改訂1のまま）

- `manifest.webmanifest`（`start_url: "."` / `display: "standalone"` / 既定色は湘南の `#82c039`）
- `<link rel="manifest">` と `<link rel="apple-touch-icon">`
- アイコンはクラブカラーの円に文字を置いた自前生成SVGでよい
- Service Worker は入れない（古い順位表を最新だと誤認させる事故を避けるため）
