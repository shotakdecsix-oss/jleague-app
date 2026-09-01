"""
jleague.jp の試合ページから得点・カード・交代、および試合日程一覧ページから
「対戦カード -> 6桁コード」の対応表を抜き出す純粋なパーサ(ネットワークアクセスなし)。

scripts/fetch_match_events.py から使う。data/tmp/sample_match_*.html を使った
手動検証で、以下を確認済み(2026-08-22時点):
  - sample_match_livetxt.html : 得点5(延長込み終スコア1-4と一致)/カード3(クラブ帰属あり)/交代10(クラブ+IN/OUT+選手名+ポジションあり)
  - sample_match_review.html  : 得点5/5が既知の正解と一致。カード0/交代0(review/ページには構造的に交代ウィジェットが無い。正しい)
  - sample_match_schedule.html: J2のその節10試合すべてのコード/対戦カードを正しい対応で抽出
    (082217 = テゲバジャーロ宮崎 vs 湘南ベルマーレ、など)
  - 出場メンバー(find_formations): livetxt/review両ページとも home/away各11人・背番号・
    フォーメーション("4-4-2"等)を正しく抽出(2026-08-22追加検証)
  - 控えメンバー(find_lineup_members): 個別試合ページのトップ(sample_match_base.html)から
    home/away各20名前後(スタメン11+控え9前後)を正しく抽出(2026-08-22追加検証)。
    formationsのスタメンidと突き合わせて差分を控えメンバーとして扱う。
  - ハイライト動画(find_highlight_video_id): 試合結果ページ(review/)にのみ、YouTube動画IDが
    "videoSrc"として埋め込まれている(sample_match_review.htmlで確認済み、2026-08-22)。
    livetxt/やbaseUrl(#lineup)側には無い。

抽出手法の注記:
  - 得点/カード/交代は、Next.jsのRSCストリーミングチャンク(extract_next_chunksでチャンクID->
    生JSON文字列に分解したもの)ごとに、目印となるリテラル文字列("GOAL!"/"cardType"/"選手交代")で
    絞り込んでから正規表現で抜く。RSCの"$L<id>"参照を辿って完全な木を再構成するのではなく、
    目的のデータ(選手名・分・スコア)がチャンク内にローカルなJSON部分文字列としてそのまま
    埋め込まれている性質を利用している(チャンク境界の厳密な一致は不要)。
  - 日程一覧ページの「対戦カード -> コード」対応は、上記と違って複数試合ぶんの情報が同一チャンクに
    連続して出現するため、チャンクをまたいで結合した1本のテキストを「href(コード) / チーム名」の
    トークン列として順に読み、「新しいコードのhrefが出たら次に来る2つのチーム名(pc表記)をhome/awayとして
    確定させる」という単純な状態機械で対応付ける(第13弾実装時に実データで検証済み)。
"""

from __future__ import annotations

import json
import re

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

def _extract_balanced(s: str, start: int) -> int:
    """
    s[start]が'['または'{'である前提で、対応する閉じ括弧のインデックスを返す(文字列リテラル内は無視する)。
    見つからなければ-1。find_formations()で"formations":の値部分(配列)を安全に切り出すのに使う。
    """
    open_ch = s[start]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    i = start
    in_str = False
    esc = False
    while i < len(s):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def find_formations(chunks: dict[str, str]) -> list[dict] | None:
    """
    出場メンバー(スタメン)。"formations":[...] の値はチャンク内にそのまま整形済みJSONとして
    埋め込まれているので、正規表現による抜き出しではなく開始位置から括弧の対応を辿って
    切り出し、json.loadsでそのままパースする(得点/カード/交代のような文字列パターン抽出より
    はるかに壊れにくい)。matchTimeごとに1件(基本は"前半0分"の1件のみ)、
    homeTeam/awayTeamそれぞれに{teamName,formation,players:[{id,name,playerNumber,positionX,positionY}]}。
    positionXは守備的(小さい)ほどゴールに近く、攻撃的(大きい)ほど前線に近い
    (昇順ソートでGK→DF→MF→FWの並びになる)。livetxt/review両方のページで確認済み。
    見つからなければNone。
    """
    marker = '"formations":['
    for v in chunks.values():
        idx = v.find(marker)
        if idx == -1:
            continue
        start = idx + len('"formations":')
        end = _extract_balanced(v, start)
        if end == -1:
            continue
        try:
            return json.loads(v[start:end + 1])
        except json.JSONDecodeError:
            continue
    return None


LINEUP_MEMBER_RE = re.compile(
    r'"legacyPlayerPhotoLookup":\{"seasonYear":\d+,"teamNameKey":"(?P<teamSlug>[a-z0-9]+)","playerId":"(?P<pid>\d+)"[^}]*\}\}\],'
    r'\["\$","div",null,\{"className":"m-lineup-list-item__content","children":\['
    r'\["\$","p",null,\{"className":"[^"]*m-lineup-list-item__position","ref":"\$undefined","children":"(?P<posnum>[^"]+)"\}\],'
    r'\["\$","div",null,\{"className":"m-lineup-list-item__main-content","children":\['
    r'\["\$","p",null,\{"className":"[^"]*m-lineup-list-item__name","ref":"\$undefined","children":"(?P<name>[^"]+)"'
)


def find_lineup_members(chunks: dict[str, str]) -> dict[str, list[dict]]:
    """
    第14弾: 控えメンバー(ベンチ)用。個別試合ページのトップ
    (https://www.jleague.jp/match/{league}/{year}/{code}/、livetxt/やreview/ではない)には、
    スタメン+控えメンバー(合計40名前後、チームごとに20名前後)が同じ"m-lineup-list-item"
    コンポーネントで一括描画されている。formations(スタメンのピッチ座標)とは別の埋め込み
    (同じ選手が重複して載っている)。teamNameKeyはクラブのjleagueSlug(masters側の
    jleagueSlugフィールド)と一致するので、chunks単体では所属チームまでしか分からないが、
    呼び出し側でjleagueSlugと突き合わせてidTeamに解決する。
    戻り値: {teamSlug: [{"id","number","position","name"}, ...チーム内の出現順], ...}。
    出現順は「スタメン11人 -> 控えメンバー」の順(サッカーのルール上スタメンは必ず11人という
    前提で、呼び出し側はformationsのスタメンidと突き合わせて控え分だけを取り出す)。
    見つからなければ空dict。
    """
    text = "".join(chunks.values())
    by_team: dict[str, list[dict]] = {}
    for m in LINEUP_MEMBER_RE.finditer(text):
        posnum = m.group("posnum")
        parts = posnum.split(" ", 1)
        position = parts[0] if parts else None
        number = parts[1] if len(parts) > 1 else None
        by_team.setdefault(m.group("teamSlug"), []).append({
            "id": m.group("pid"),
            "number": number,
            "position": position,
            "name": m.group("name"),
        })
    return by_team


HIGHLIGHT_VIDEO_RE = re.compile(r'"videoSrc":"(?P<url>https://www\.youtube\.com/watch\?v=(?P<vid>[A-Za-z0-9_-]+))"')


def find_highlight_video_id(chunks: dict[str, str]) -> str | None:
    """
    第14弾: ハイライト動画(YouTube)のvideoId。試合結果ページ(review/、livetxt/やbaseUrlでは
    なく試合終了後の"試合結果・データ"ページ)にのみ埋め込まれている
    ("videoSrc":"https://www.youtube.com/watch?v=<ID>")。Jリーグ公式が動画を用意した
    タイミングでのみ埋め込まれるため、試合終了直後はまだ無くて当然(次回以降のバッチ実行で
    拾えればよいという前提。#formations等と同じ「無ければNone、既存値があれば呼び出し側で
    維持する」安全弁の対象)。見つからなければNone。
    """
    text = "".join(chunks.values())
    m = HIGHLIGHT_VIDEO_RE.search(text)
    return m.group("vid") if m else None


# 日程一覧ページ(/match/{league}/): 「新しいコードのhref」直後に来る2つのチーム名(data-media=pc)を
# home/awayとして拾う。同じコードのhrefが1試合につき複数回(mobile版リンク等で)出現するので、
# 呼び出し側の状態機械でコードが変わった時だけ新しい試合として扱う。
# 第36弾: leaguecup(ルヴァンカップ)を追加。個別試合ページのURLがJ1〜J3とまったく同じ形
# (/match/leaguecup/2026/090201/)で、日程一覧も /match/leaguecup/ にある。
# 得点者・カード・交代・出場メンバー・ハイライト動画のパーサはそのまま使える。
SCHEDULE_TOKEN_RE = re.compile(
    r'"href":"/match/(?P<league>j1|j2|j3|j1j2|j2j3|leaguecup)/(?P<year>\d{4})/(?P<code>\d{6})"'
    r'|m-schedule__team-name","ref":"\$undefined","data-media":"pc","children":"(?P<name>[^"]+)"'
)


def find_goals(chunks: dict[str, str]) -> list[dict]:
    out = []
    for cid, v in chunks.items():
        if "GOAL!" not in v:
            continue
        for m in GOAL_RE.finditer(v):
            out.append({
                "minute": m.group("minute"),
                "club": m.group("club"),
                "player": m.group("player"),
                "position": m.group("position"),
                "scoreAfter": m.group("score"),
            })
    return out


def find_cards(chunks: dict[str, str]) -> list[dict]:
    out = []
    for cid, v in chunks.items():
        m_min = CARD_MINUTE_RE.search(v)
        for m in CARD_RE.finditer(v):
            out.append({
                "minute": m_min.group("minute") if m_min else None,
                "type": m.group("type"),
                "player": m.group("player"),
                "position": m.group("position"),
                "club": m.group("club"),
            })
    return out


def find_subs(chunks: dict[str, str]) -> list[dict]:
    out = []
    for cid, v in chunks.items():
        if "選手交代" not in v:
            continue
        items = SUB_ITEM_RE.findall(v)
        if not items:
            continue  # 記事本文などでの"選手交代"という単語の単純ヒット。実データが無ければ捨てる
        m_club = SUB_BLOCK_RE.search(v)
        m_min = SUB_MINUTE_RE.search(v)
        out.append({
            "minute": m_min.group("minute") if m_min else None,
            "club": m_club.group("club") if m_club else None,
            "items": [
                {"variant": it[0], "position": it[1] + " " + it[2], "name": it[3]}
                for it in items
            ],
        })
    return out


def extract_schedule_index(chunks: dict[str, str]) -> list[dict]:
    """
    日程一覧ページのチャンク群から [{"league","year","code","home","away"}, ...] を作る。
    チャンクを結合した1本のテキストに対して状態機械で読む(コードが複数チャンクに
    またがって分割されることは無い前提。分割された場合はその試合が抜け落ちるだけで
    例外にはならない -> 呼び出し側は「見つからなかった候補」をfailed扱いにして安全に無視できる)。
    """
    text = "".join(chunks.values())
    out: list[dict] = []
    current_code = None
    current_league = None
    current_year = None
    names: list[str] = []

    for m in SCHEDULE_TOKEN_RE.finditer(text):
        if m.group("code"):
            code = m.group("code")
            if code != current_code:
                current_code = code
                current_league = m.group("league")
                current_year = m.group("year")
                names = []
        else:
            if current_code is None or len(names) >= 2:
                continue
            names.append(m.group("name"))
            if len(names) == 2:
                out.append({
                    "league": current_league,
                    "year": current_year,
                    "code": current_code,
                    "home": names[0],
                    "away": names[1],
                })
    return out
