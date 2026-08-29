# worker/ — 現時点では未使用のパーサ移植版

`scripts/match_events_parser.py` を JavaScript に移植したもの。**アプリからも Actions からも
使っていない。** Cloudflare Workers で得点者を5分おきに取得する案(第32弾の検討)の途中まで
作って、採用を見送った時点のコードをそのまま残してある。

## なぜ採用しなかったか

Cloudflare Workers の **Free プランは CPU 時間が 10ms**(HTTP・Cron Triggers とも)。
一方、実測すると1試合ぶんの HTML(1.66MB)のパースに **21.3ms** かかる。

```
extractNextChunks  12.7 ms   ← 支配的。1.66MBを結合して897チャンクに分ける処理そのもの
findGoals           0.7 ms
findCards           2.2 ms
findSubs            1.2 ms
findFormations      1.1 ms
findLineupMembers   3.3 ms
合計               21.3 ms
```

1試合すら Free 枠に収まらず、24試合が同時進行する日は合計500ms必要になる。
最適化しても半減が精一杯で 10ms には届かないため、Workers Paid($5/月)を前提にしない限り
成立しないと判断した。代わりにローカルPCのタスクスケジューラから
`scripts/live_watch.py` を叩く方式を採用している。

## 復活させるとしたら

- Cloudflare Workers Paid にする(Cron の CPU 時間が 30 秒になる)
- CPU 時間の制限が緩い他のサービス(Deno Deploy など)に載せる

どちらの場合も、このパーサはそのまま使える。Worker 本体(fetch / scheduled ハンドラ、
KV への保存、進行中の試合の特定)は未実装なので、そこから書くことになる。

## 検証

`parser.js` の出力が Python 版と一致することを確認できる。

```
# 先に Python 側で期待値を作る(scripts/match_events_parser.py を使う)
python -c "..."          # /tmp/expected.json を生成
node worker/verify_parser.js
```

`data/tmp/sample_match_livetxt.html` に対して、得点5・カード3・交代10・フォーメーション・
両チームのメンバー20名ずつが**並び順まで含めて**Python 版と一致することを確認済み(2026-08-30)。

移植時の注意: JS のプレーンなオブジェクトは `"1"` `"2"` のような整数に見えるキーを数値順に
並べ替えてしまう。チャンクIDは16進数なのでこれを踏み、得点とカードの順序がずれる。
`extractNextChunks` は `Map` を返すこと。

`bench.js` は上記の CPU 時間を測るためのもの。
