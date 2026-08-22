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
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_KEY_FILE = BASE_DIR / "youtube_api_key.txt"

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


def search_dazn_highlight(home_ja: str, away_ja: str, api_key: str | None = None) -> str | None:
    """
    DAZN Japanチャンネルから home_ja × away_ja のハイライト動画を検索し、videoIdを返す。
    DAZNのタイトルは「【<ホーム>×<アウェイ>｜ハイライト】<大会名>第<節>節｜<シーズン>シーズン｜Jリーグ」
    という形式で、home/awayの順番はjleague.jp側(=matches.jsonのhome/away)と一致することを
    実データで確認済み。念のためタイトルに両チーム名が含まれる動画だけを採用する
    (検索結果の1件目が別カードの可能性もゼロではないため)。
    APIキーが無い/リクエスト失敗/該当なしの場合はNoneを返す(例外は投げない)。
    """
    key = api_key or load_api_key()
    if not key:
        return None

    import requests

    params = {
        "key": key,
        "channelId": DAZN_JAPAN_CHANNEL_ID,
        "q": f"{home_ja} {away_ja} ハイライト",
        "type": "video",
        "order": "date",
        "maxResults": 10,
        "part": "snippet",
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    for item in data.get("items", []):
        title = (item.get("snippet") or {}).get("title", "")
        if home_ja in title and away_ja in title:
            video_id = (item.get("id") or {}).get("videoId")
            if video_id:
                return video_id
    return None
