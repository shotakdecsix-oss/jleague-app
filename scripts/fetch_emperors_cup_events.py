"""
天皇杯の試合詳細(出場メンバー/控え/監督/選手交代/警告・退場/JFATVの動画)を取得する。
第23弾フェーズ2。フェーズ1(scripts/fetch_emperors_cup.py)の後に実行すること。

情報源はJFAの個別試合ページ:
    https://www.jfa.jp/match/emperorscup_{year}/match_page/m{matchNumber}.html
matchNumberはフェーズ1で data/processed/emperors_cup.json の各試合に入れてあるので、
「どの番号か探す」処理は不要。

■ ページ構造(2026年m56/m1、2025年m66/m87の4サンプルで確認。4つとも完全に同一構造)
`<table class="match-result">` が1つだけあり、その中が行のクラスでセクション分けされている:

    <td class="header" colspan="3">ホーム名</td><td class="header" colspan="3">アウェイ名</td>
    (以降スタメン: 1行6セル = ポジション/背番号/氏名 を左右2チームぶん)
    <td class="separate" colspan="6">
    <td class="header" colspan="6">控え選手</td>   (以降は控え。形は同じ)
    <td class="separate" colspan="6">
    <td class="number" colspan="2">監督</td><td>氏名</td> ... (左右2チームぶん)
    <td class="separate" colspan="6">
    <td class="header" colspan="6">選手交代</td>   (1行4セル: 背番号 / 氏名▼73分 OUT)
    <td class="separate" colspan="6">
    <td class="header" colspan="6">警告・退場</td> (1行4セル: 背番号 / 氏名<span class="card"><img></span>49分)

交代はOUTの行とINの行が交互に並ぶ(左右で本数が違うと片側のセルが空になる)ので、
チームごとに出現順で拾ってからOUT->INの順に組にする。

動画は `<h4 id="jfa-tv">JFATV</h4><h5>タイトル</h5>` に続く
`<iframe src="//www.youtube.com/embed/{videoId}?rel=0">`。全試合にあるわけではない
(2026年2回戦には無く、2025年3回戦にはあった)ので、無くても異常ではない。
**ハイライトとは限らない**点に注意: 2026年1回戦m1は「【ライブ配信】…」のフル配信アーカイブだった。
そのためタイトル(videoTitle)も一緒に保存し、表示側では「ハイライト」と決め打ちせずタイトルを出す。

■ 注意点(実測)
- **文字コード**: JFAはレスポンスヘッダにcharsetを付けてこないため、requestsが
  ISO-8859-1と誤推定して resp.text が文字化けする。必ず resp.encoding = "utf-8" を
  明示すること(HTML内の<meta charset="utf-8">が正)。
- **選手IDが無い**: jleague.jpと違いJFA側は選手ページを持たないので、氏名の文字列だけ。
  選手リンクは作れない。
- **警告と退場の区別**: tim_mem_ico_02=警告(黄)、tim_mem_ico_01=退場(赤)。根拠は
  CARD_ICON_TYPESのコメント参照。未知のアイコンが出たら type="unknown" で保存して
  警告ログを出す(黄や赤と嘘をつかない)。アイコン名は常に icon フィールドに残すので、
  CARD_ICON_TYPESに1行足して再実行すれば、ページを取り直さずに分類だけ直る
  (reclassify_cards)。

■ 取得の作法
- 消化済み(finished)の試合だけ取りに行く。
- 既に取れている試合は取り直さない。ただしハイライト動画がまだ無い試合だけは、
  後から公開されることがあるので RETRY_COOLDOWN_HOURS 間隔・MAX_ATTEMPTS回まで再取得する。
- 既存データとは必ずマージする(部分取得で全体を上書きしない)。取得に失敗した試合は
  前回分をそのまま残す。

出力: data/processed/emperors_cup_events.json

CLI:
    python scripts/fetch_emperors_cup_events.py
    python scripts/fetch_emperors_cup_events.py --limit 10
    python scripts/fetch_emperors_cup_events.py --force        # 全消化済み試合を取り直す
    python scripts/fetch_emperors_cup_events.py --only 56 66   # 指定した試合番号だけ
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
CUP_PATH = PROCESSED_DIR / "emperors_cup.json"
OUT_PATH = PROCESSED_DIR / "emperors_cup_events.json"

TIMEOUT = 20.0
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jleague-app personal use"}

SLEEP_BETWEEN_REQUESTS = 1.5
RETRY_COOLDOWN_HOURS = 12      # ハイライト動画待ちの再取得間隔
MAX_ATTEMPTS = 4               # 1試合あたりの取得試行上限(動画が付かないまま無限に叩かない)
DEFAULT_LIMIT = 90             # 1回の実行で取りに行く上限(全87試合ぶん初回で回りきる想定)

# JFAのカードアイコン。未知のアイコンは嘘をつかず"unknown"にする(黄や赤と決めつけない)。
# tim_mem_ico_01 が退場である根拠(2026年大会の実データ56試合で確認):
#   m7  高 昇辰        73分に02 -> 74分に02 -> 同じ74分に01   (2枚目の警告による退場)
#   m27 マルコ・ローレンス 55分に02 -> 90+5分に02 -> 同じ90+5分に01 (同上)
#   m16 渥美 慶大 / m53 柳 世根  02が無くいきなり01           (一発退場)
# 2枚目の警告と同時刻に必ず付く、という並びは退場以外にありえない。
# なお2枚目の警告のときは警告(02)と退場(01)が両方記録される(片方に丸めず両方そのまま出す)。
CARD_ICON_TYPES = {
    "tim_mem_ico_02": "yellow",
    "tim_mem_ico_01": "red",
}

TABLE_RE = re.compile(r'<table class="match-result">(.*?)</table>', re.S)
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td([^>]*)>(.*?)</td>", re.S)
CLASS_RE = re.compile(r'class="([^"]*)"')
COLSPAN_RE = re.compile(r'colspan="(\d+)"')
CARD_IMG_RE = re.compile(r'<span class="card"><img src="[^"]*?([A-Za-z0-9_]+)\.gif"')
VIDEO_ID_RE = re.compile(r'youtube\.com/embed/([A-Za-z0-9_-]{6,})')
VIDEO_TITLE_RE = re.compile(r'<h4 id="jfa-tv">.*?</h4>\s*<h5>(.*?)</h5>', re.S)
OFFICIAL_PDF_RE = re.compile(r'<div class="official-record"><a href="([^"]+)"')
MINUTE_RE = re.compile(r"(\d+(?:\+\d+)?)\s*分")
CAPTAIN_RE = re.compile(r"[（(]\s*Cap\.?\s*[)）]", re.I)


def page_url(year: int, number: str) -> str:
    return f"https://www.jfa.jp/match/emperorscup_{year}/match_page/m{number}.html"


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def normalize_minute(text: str) -> str:
    """'73分'->'73' / 'ＨＴ'->'HT' / '90+2分'->'90+2'。数字が無ければNFKCしただけの文字列。"""
    m = MINUTE_RE.search(text or "")
    if m:
        return m.group(1)
    # 「分」が付かない表記(ＨＴ 等)。矢印とOUT/INの語を落としてから全角->半角にする。
    rest = re.sub(r"[▼▲]|OUT|IN", " ", text or "")
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", rest))


def split_name(raw: str) -> tuple[str, bool]:
    """'池田 昌生 (Cap.)' -> ('池田 昌生', True)。"""
    captain = bool(CAPTAIN_RE.search(raw))
    name = CAPTAIN_RE.sub("", raw).strip()
    return name, captain


def parse_rows(table_html: str) -> list[list[dict]]:
    """<tr>ごとに [{cls, colspan, html, text}, ...] のセル配列にする。"""
    rows = []
    for row_html in ROW_RE.findall(table_html):
        cells = []
        for attr, inner in CELL_RE.findall(row_html):
            cls_m = CLASS_RE.search(attr)
            cs_m = COLSPAN_RE.search(attr)
            cells.append(
                {
                    "cls": cls_m.group(1) if cls_m else "",
                    "colspan": int(cs_m.group(1)) if cs_m else 1,
                    "html": inner,
                    "text": strip_tags(inner),
                }
            )
        rows.append(cells)
    return rows


def _player_from_cells(cells: list[dict], base: int) -> dict | None:
    """スタメン/控えの1チームぶん3セル(ポジション/背番号/氏名)を選手にする。"""
    if len(cells) < base + 3:
        return None
    pos = cells[base]["text"]
    number = cells[base + 1]["text"]
    name, captain = split_name(cells[base + 2]["text"])
    if not name:
        return None
    player = {"no": number or None, "pos": pos or None, "name": name}
    if captain:
        player["captain"] = True
    return player


def _sub_entry_from_cells(cells: list[dict], base: int) -> dict | None:
    """選手交代の1チームぶん2セル(背番号 / '氏名▼73分 OUT')を1件にする。"""
    if len(cells) < base + 2:
        return None
    number = cells[base]["text"]
    body = cells[base + 1]["text"]
    if not body:
        return None
    variant = "out" if ("OUT" in body or "▼" in body) else ("in" if ("IN" in body or "▲" in body) else None)
    if not variant:
        return None
    name = body.split("▼")[0].split("▲")[0].strip()
    name, _ = split_name(name)
    tail = body[len(body.split("▼")[0].split("▲")[0]):]
    return {"no": number or None, "name": name, "variant": variant, "minute": normalize_minute(tail)}


def _card_from_cells(cells: list[dict], base: int) -> dict | None:
    """警告・退場の1チームぶん2セル(背番号 / '氏名<span class=card><img></span>49分')を1件にする。"""
    if len(cells) < base + 2:
        return None
    number = cells[base]["text"]
    inner = cells[base + 1]["html"]
    if not strip_tags(inner):
        return None
    icon_m = CARD_IMG_RE.search(inner)
    icon = icon_m.group(1) if icon_m else None
    before_icon = inner.split('<span class="card">')[0]
    name, _ = split_name(strip_tags(before_icon))
    if not name:
        return None
    return {
        "no": number or None,
        "name": name,
        "type": CARD_ICON_TYPES.get(icon or "", "unknown"),
        "icon": icon,
        "minute": normalize_minute(strip_tags(inner)),
    }


def pair_subs(entries: list[dict], side: str) -> list[dict]:
    """
    出現順のOUT/INを組にする。OUTの次がINなら1件の交代にまとめ、
    そうでなければ組にせず単独で残す(ページの並びが崩れても情報を落とさないため)。
    """
    out: list[dict] = []
    i = 0
    while i < len(entries):
        cur = entries[i]
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        if cur["variant"] == "out" and nxt and nxt["variant"] == "in":
            out.append({
                "side": side,
                "minute": cur["minute"] or nxt["minute"],
                "out": {"no": cur["no"], "name": cur["name"]},
                "in": {"no": nxt["no"], "name": nxt["name"]},
            })
            i += 2
            continue
        out.append({
            "side": side,
            "minute": cur["minute"],
            "out": {"no": cur["no"], "name": cur["name"]} if cur["variant"] == "out" else None,
            "in": {"no": cur["no"], "name": cur["name"]} if cur["variant"] == "in" else None,
        })
        i += 1
    return out


def parse_match_page(page_html: str) -> dict:
    """
    個別試合ページのHTMLから出場メンバー・交代・カード・動画を抜き出す。
    ネットワークもファイルI/Oもしない(テスト用に分離)。
    表が見つからなければ ValueError。
    """
    tables = TABLE_RE.findall(page_html)
    if not tables:
        raise ValueError('table.match-result が見つからない(ページ構造が変わった可能性)')

    rows = parse_rows(tables[0])

    home_name = away_name = ""
    starters = {"home": [], "away": []}
    subs_members = {"home": [], "away": []}
    coaches = {"home": None, "away": None}
    sub_entries = {"home": [], "away": []}
    cards: list[dict] = []

    section = "starters"
    for cells in rows:
        if not cells:
            continue
        first = cells[0]
        if "separate" in first["cls"]:
            continue
        if "header" in first["cls"] and first["colspan"] == 3:
            home_name = first["text"]
            away_name = cells[1]["text"] if len(cells) > 1 else ""
            section = "starters"
            continue
        if "header" in first["cls"] and first["colspan"] >= 6:
            label = first["text"]
            section = {"控え選手": "subs", "選手交代": "changes", "警告・退場": "cards"}.get(label, "other")
            continue

        if section in ("starters", "subs"):
            # 監督行(「監督」がcolspan=2のセルに入る)はメンバー表とは別扱い
            if first["colspan"] == 2 and "監督" in first["text"]:
                coaches["home"] = cells[1]["text"] if len(cells) > 1 else None
                coaches["away"] = cells[3]["text"] if len(cells) > 3 else None
                continue
            target = starters if section == "starters" else subs_members
            for side, base in (("home", 0), ("away", 3)):
                p = _player_from_cells(cells, base)
                if p:
                    target[side].append(p)
        elif section == "changes":
            for side, base in (("home", 0), ("away", 2)):
                e = _sub_entry_from_cells(cells, base)
                if e:
                    sub_entries[side].append(e)
        elif section == "cards":
            for side, base in (("home", 0), ("away", 2)):
                c = _card_from_cells(cells, base)
                if c:
                    c["side"] = side
                    cards.append(c)

    subs = pair_subs(sub_entries["home"], "home") + pair_subs(sub_entries["away"], "away")

    video_m = VIDEO_ID_RE.search(page_html)
    title_m = VIDEO_TITLE_RE.search(page_html)
    pdf_m = OFFICIAL_PDF_RE.search(page_html)

    return {
        "lineups": {
            "home": {"teamName": home_name, "starters": starters["home"], "subs": subs_members["home"], "coach": coaches["home"]},
            "away": {"teamName": away_name, "starters": starters["away"], "subs": subs_members["away"], "coach": coaches["away"]},
        },
        "subs": subs,
        "cards": cards,
        "videoId": video_m.group(1) if video_m else None,
        "videoTitle": strip_tags(title_m.group(1)) if title_m else None,
        "officialReportPath": pdf_m.group(1) if pdf_m else None,
    }


def fetch_state(entry: dict | None) -> dict:
    """
    取得の記録(最終取得時刻・試行回数)。
    これらは中身が変わらなくても実行のたびに動くので、fetchStateという1つのキーに
    まとめてある(git_diff_ignoring_timestamps.py の VOLATILE_KEYS で丸ごと無視させ、
    「実質的な変更が無いのにコミット+デプロイ」が起きないようにするため)。
    古い形式(トップレベルに置いていたもの)も読めるようにフォールバックする。
    """
    if not entry:
        return {}
    st = entry.get("fetchState")
    if isinstance(st, dict):
        return st
    return {"lastFetchedAtJst": entry.get("lastFetchedAtJst"), "attempts": entry.get("attempts", 0)}


def migrate_fetch_state(events: dict) -> int:
    """古い形式(lastFetchedAtJst/attemptsがトップレベル)を fetchState に移す。移した件数を返す。"""
    moved = 0
    for entry in events.values():
        if not isinstance(entry, dict) or isinstance(entry.get("fetchState"), dict):
            continue
        if "lastFetchedAtJst" in entry or "attempts" in entry:
            entry["fetchState"] = {
                "lastFetchedAtJst": entry.pop("lastFetchedAtJst", None),
                "attempts": entry.pop("attempts", 0),
            }
            moved += 1
    return moved


def reclassify_cards(events: dict) -> int:
    """
    保存済みのカードを、今のCARD_ICON_TYPESで分類し直す。
    アイコン名(icon)を必ず残してあるので、マッピングを更新したときに
    56ページを取り直さなくても種別だけ直せる。変更した件数を返す。
    """
    changed = 0
    for entry in events.values():
        for card in entry.get("cards") or []:
            icon = card.get("icon")
            if not icon:
                continue
            want = CARD_ICON_TYPES.get(icon, "unknown")
            if card.get("type") != want:
                card["type"] = want
                changed += 1
    return changed


def has_lineups(entry: dict | None) -> bool:
    if not entry:
        return False
    lu = entry.get("lineups") or {}
    return bool((lu.get("home") or {}).get("starters"))


def should_fetch(entry: dict | None, now: datetime, force: bool = False) -> bool:
    """
    取りに行くべきか。
    - 未取得 -> 取る
    - メンバーが取れていない -> 試行上限まで取る
    - メンバーはあるが動画がまだ -> クールダウンを空けて試行上限まで取る(後から公開されるため)
    - 揃っている -> 取らない
    """
    if force:
        return True
    if not entry:
        return True
    if fetch_state(entry).get("attempts", 0) >= MAX_ATTEMPTS:
        return False
    if not has_lineups(entry):
        return True
    if entry.get("videoId"):
        return False
    last = fetch_state(entry).get("lastFetchedAtJst")
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(last)
    except ValueError:
        return True
    return now - prev >= timedelta(hours=RETRY_COOLDOWN_HOURS)


def pick_targets(cup: dict, existing: dict, now: datetime, force: bool = False,
                 only: list[str] | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """消化済みの試合のうち、取りに行くべきものを選ぶ。"""
    events = existing.get("events") or {}
    picked = []
    for m in cup.get("matches", []):
        number = m.get("matchNumber")
        if not number:
            continue
        if only is not None:
            if number in only:
                picked.append(m)
            continue
        if not m.get("finished"):
            continue
        if should_fetch(events.get(number), now, force):
            picked.append(m)
        if len(picked) >= limit:
            break
    return picked


def fetch_match_page(year: int, number: str) -> str:
    import requests

    resp = requests.get(page_url(year, number), headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    # JFAはcharsetを返さないのでrequestsがISO-8859-1と誤推定する。明示しないと文字化けする。
    resp.encoding = "utf-8"
    return resp.text


def build_entry(match: dict, parsed: dict, year: int, now: datetime, prev: dict | None) -> dict:
    number = match["matchNumber"]
    pdf = parsed.get("officialReportPath")
    entry = {
        "matchNumber": number,
        "round": match.get("round"),
        "url": page_url(year, number),
        "fetchState": {
            "lastFetchedAtJst": now.isoformat(timespec="seconds"),
            "attempts": fetch_state(prev).get("attempts", 0) + 1,
        },
        "lineups": parsed["lineups"],
        "subs": parsed["subs"],
        "cards": parsed["cards"],
        "videoId": parsed["videoId"],
        "videoTitle": parsed["videoTitle"],
        "officialReportUrl": (
            f"https://www.jfa.jp/match/emperorscup_{year}/" + pdf.replace("../", "") if pdf else None
        ),
    }
    # 今回メンバーが取れなかったのに前回は取れていた、という場合は前回分を維持する
    # (部分的な失敗で既存の良いデータを壊さない)。
    if prev and has_lineups(prev) and not has_lineups(entry):
        merged = dict(prev)
        merged.pop("lastFetchedAtJst", None)
        merged.pop("attempts", None)
        merged["fetchState"] = entry["fetchState"]
        if entry["videoId"]:
            merged["videoId"] = entry["videoId"]
            merged["videoTitle"] = entry["videoTitle"]
        return merged
    return entry


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="天皇杯の試合詳細(メンバー/交代/カード/動画)を取得する")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="1回の実行で取りに行く試合数の上限")
    parser.add_argument("--force", action="store_true", help="取得済みでも全部取り直す")
    parser.add_argument("--only", nargs="*", help="この試合番号だけ取る(消化前でも取りに行く)")
    args = parser.parse_args()

    cup = load_json(CUP_PATH)
    if not cup.get("matches"):
        print(f"[error] {CUP_PATH} が無いか空。先に python scripts/fetch_emperors_cup.py を実行すること",
              file=sys.stderr)
        sys.exit(1)

    year = (cup.get("meta") or {}).get("year") or datetime.now(JST).year
    existing = load_json(OUT_PATH)
    events: dict = dict(existing.get("events") or {})   # 既存を必ず引き継ぐ(上書きしない)
    now = datetime.now(JST)

    moved = migrate_fetch_state(events)
    if moved:
        print(f"[info] 取得記録を{moved}件、fetchStateにまとめ直した(無駄なコミットを避けるため)")

    fixed = reclassify_cards(events)
    if fixed:
        print(f"[info] 既存データのカード種別を{fixed}件、現在のアイコン対応表で分類し直した")

    targets = pick_targets(cup, existing, now, force=args.force, only=args.only, limit=args.limit)
    finished = sum(1 for m in cup["matches"] if m.get("finished"))
    print(f"[info] 消化済み{finished}試合中、今回の取得対象は{len(targets)}試合 (既存{len(events)}件)")

    failed: list[str] = []
    unknown_icons: set[str] = set()
    for idx, m in enumerate(targets):
        number = m["matchNumber"]
        try:
            page = fetch_match_page(year, number)
            parsed = parse_match_page(page)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] m{number}({m.get('round')}) の取得に失敗: {e}", file=sys.stderr)
            failed.append(number)
            prev = events.get(number)
            if prev:
                prev["fetchState"] = {
                    "lastFetchedAtJst": now.isoformat(timespec="seconds"),
                    "attempts": fetch_state(prev).get("attempts", 0) + 1,
                }
            if idx < len(targets) - 1:
                time.sleep(SLEEP_BETWEEN_REQUESTS)
            continue

        entry = build_entry(m, parsed, year, now, events.get(number))
        events[number] = entry
        for c in entry.get("cards") or []:
            if c.get("type") == "unknown" and c.get("icon"):
                unknown_icons.add(c["icon"])
        print(
            f"[info] m{number} {m.get('round')}: "
            f"メンバー{len(entry['lineups']['home']['starters'])}+{len(entry['lineups']['home']['subs'])}人 / "
            f"交代{len(entry['subs'])} / カード{len(entry['cards'])} / "
            f"動画{'あり' if entry['videoId'] else 'なし'}"
        )
        if idx < len(targets) - 1:
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    if unknown_icons:
        print(
            "[warn] 未知のカードアイコンを検出: " + ", ".join(sorted(unknown_icons)) +
            " -> CARD_ICON_TYPES に追加すれば黄/赤を正しく出せる(今は type=unknown で保存している)",
            file=sys.stderr,
        )

    if len(events) < len(existing.get("events") or {}):
        print("[error] 件数が既存より減っている。書き出さずに中断する", file=sys.stderr)
        sys.exit(1)

    out = {
        "meta": {
            "generatedAtJst": now.isoformat(timespec="seconds"),
            "year": year,
            "source": "JFA",
            "eventCount": len(events),
            "withVideo": sum(1 for e in events.values() if e.get("videoId")),
            "fetchedThisRun": len(targets) - len(failed),
            "failed": failed,
        },
        "events": events,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[info] {OUT_PATH} に書き出し "
        f"(合計{out['meta']['eventCount']}試合 / 動画{out['meta']['withVideo']} / 失敗{len(failed)})"
    )


if __name__ == "__main__":
    main()
