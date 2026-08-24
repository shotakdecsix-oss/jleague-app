"""
第14弾: DAZN JapanのYouTubeチャンネルから、該当試合のハイライト動画を検索する。

jleague.jp側(match_events_parser.find_highlight_video_id)にも試合結果ページに
「【公式】ハイライト」動画が埋め込まれていることがあるが、ユーザーからのフィードバックで
DAZN Japanチャンネル(https://www.youtube.com/@daznjapan)の方が同じ試合でも尺が長く
内容も充実していることが分かった。そちらはjleague.jp側には埋め込まれておらず、
YouTube上で別途検索する必要があるため、YouTube Data API v3を使う
(スクレイピングではなく公式APIを使う方針。robots.txtでクロールを断っているRSSフィード等を
使うのは避けた)。

セットアップ(無料枠で足りる):
    1. https://console.cloud.google.com/ で新しいプロジェクトを作成
    2. 「APIとサービス」→「ライブラリ」で "YouTube Data API v3" を検索して有効化
    3. 「APIとサービス」→「認証情報」→「認証情報を作成」→「APIキー」でキーを発行
       (制限をかける場合は「YouTube Data API v3」のみに絞ってOK)
    4. 発行したキーをこのリポジトリのルートに youtube_api_key.txt という名前で保存する
       (1行だけキーを書く。.gitignore対象なので誤ってコミットされる心配はない)。
       もしくは環境変数 YOUTUBE_API_KEY を設定してもよい(そちらを優先する)。
    5. GitHub Actionsで動かす場合は、リポジトリの Settings > Secrets and variables >
       Actions で YOUTUBE_API_KEY という名前のSecretを登録し、
       .github/workflows/match_events.yml側のfetch_match_eventsステップに
       env: YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }} を渡す。

無料枠は1日10,000ユニットで、search.list(このモジュールが使うAPI)は1回100ユニット
消費する(=1日100回まで無料)。このモジュール自体はレート制御をしない
(1回の呼び出し=検索1回)。呼び出し側(fetch_match_events.py)で試合ごとの
クールダウン・試行回数の上限を設けて、枠を使い切らないようにしている。

APIキーが無い(未設定)場合は、この機能全体を静かにスキップする(Noneを返すだけで、
他のデータ取得は一切妨げない)。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_KEY_FILE = BASE_DIR / "youtube_api_key.txt"

# 第21弾: 検索自体はYouTube全体を対象にしており(下記search_dazn_highlight参照)、この定数は
# もう検索クエリの絞り込みには使っていない。DAZN Japan本チャンネルの参考情報として残している。
DAZN_JAPAN_CHANNEL_ID = "UCoFLB_Gw_AoxUuuzKjXrc_Q"
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
TIMEOUT = 15


def load_api_key() -> str | None:
    """環境変数 YOUTUBE_API_KEY を優先し、無ければリポジトリ直下の youtube_api_key.txt を見る。"""
    env_key = os.environ.get("YOUTUBE_API_KEY")
    if env_key:
        return env_key.strip()
    if LOCAL_KEY_FILE.exists():
        text = LOCAL_KEY_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def search_dazn_highlight(
    home_ja: str,
    away_ja: str,
    api_key: str | None = None,
    published_after_jst: str | None = None,
) -> str | None:
    """
    home_ja × away_ja のハイライト動画をYouTube全体から検索し、videoIdを返す。
    DAZN Japanチャンネルのタイトルは「【<ホーム>×<アウェイ>｜ハイライト】<大会名>第<節>節｜
    <シーズン>シーズン｜Jリーグ」という形式で、home/awayの順番はjleague.jp側
    (=matches.jsonのhome/away)と一致することを実データで確認済み。念のためタイトルに
    両チーム名が含まれる動画だけを採用する(検索結果の1件目が別カードの可能性もゼロではないため)。
    APIキーが無い/リクエスト失敗/該当なしの場合はNoneを返す(例外は投げない)。

    第21弾・その1: channelId=DAZN_JAPAN_CHANNEL_ID指定を外し、YouTube全体を検索するようにした。
    J3(SC相模原 vs ツエーゲン金沢など)で調査したところ、下位カテゴリの試合はDAZN Japan
    チャンネルではなく各クラブの公式チャンネルに「【クラブ名】DAZNハイライト(日付vs対戦相手)」
    という別形式で投稿されており、DAZN Japanチャンネルには存在しないため、channelId指定の
    ままだと何回検索しても永久に見つからないことが判明した(2026-08-24)。

    第21弾・その2(本命の修正): 実際のActionsログ(2026-08-23 23:03 JST台の実行)を確認したところ、
    channelId制限をかけていた時点でも、検索結果の大半が2021〜2023シーズンの別カードや、
    Jリーグと無関係な他競技(WEリーグ・UEFA・MMA・バスケ等)の動画で埋まっており、対象試合の
    動画が10件のうちどこにも入ってこないことが判明した。DAZN Japanチャンネルには何年ぶんもの
    過去ハイライトが大量にあるため、relevance順(デフォルト)で「{home_ja} {away_ja} ハイライト」
    程度の短いクエリを投げても、直近アップロードの1本を安定して上位に出すのは無理があった
    (タイトルの両チーム名一致チェックは、そもそも候補に挙がらなければ意味を持たない)。
    そこで publishedAfter パラメータでキックオフ時刻以降に絞り込むようにした。ハイライト動画は
    定義上キックオフより後にしか存在しないので、これだけで過去シーズンの誤検出はほぼ根絶できる。
    kickoff_jst(呼び出し側=fetch_match_events.pyのresolved["kickoffJst"])が渡された場合のみ
    適用し、渡されなかった場合(テスト等)は付けない。

    order=dateではなくrelevance(デフォルト、orderパラメータ省略)を使う。publishedAfterで
    母集団自体を絞り込んだ後なので、relevance順でも問題は起きにくい。ただしYouTube Data
    API(検索)自体のインデックス反映が本サイトの検索より遅れることがある
    (アップロード直後は数十分〜数時間、公式ドキュメントでも言及あり)ため、
    それでも見つからない場合は呼び出し側(fetch_match_events.py)のクールダウン・
    リトライで時間を置いて再検索する前提。
    """
    key = api_key or load_api_key()
    if not key:
        return None

    import sys

    import requests

    params = {
        "key": key,
        "q": f"{home_ja} {away_ja} ハイライト",
        "type": "video",
        "maxResults": 10,
        "part": "snippet",
    }
    if published_after_jst:
        try:
            kickoff_dt = datetime.fromisoformat(published_after_jst)
            published_after_utc = kickoff_dt.astimezone(timezone.utc)
            params["publishedAfter"] = published_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass  # 形式不正なら付けずに検索は続行する(過去との互換・テスト用の安全弁)
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        # APIキーが無効/未有効化/クォータ超過などの実際の理由がここに落ちてくる。
        # GitHub Actionsのログで原因が追えるよう、標準エラー出力にだけ残す
        # (呼び出し側の処理は止めない。動画が見つからなかった場合と同じくNoneを返すだけ)。
        body = getattr(getattr(exc, "response", None), "text", "")
        print(f"[youtube_highlights] search_dazn_highlight failed: {exc} {body}".strip(), file=sys.stderr)
        return None

    for item in data.get("items", []):
        title = (item.get("snippet") or {}).get("title", "")
        if home_ja in title and away_ja in title:
            video_id = (item.get("id") or {}).get("videoId")
            if video_id:
                return video_id

    if data.get("items"):
        titles = [
            (item.get("snippet") or {}).get("title", "") for item in data.get("items", [])
        ]
        print(
            f"[youtube_highlights] {home_ja}×{away_ja}: 検索結果はあったがタイトルに両チーム名が"
            f"揃う動画が無かった(候補: {titles})",
            file=sys.stderr,
        )
    return None
