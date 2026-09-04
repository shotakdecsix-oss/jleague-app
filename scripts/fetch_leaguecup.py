"""
ルヴァンカップ(JリーグYBCルヴァンカップ)の日程・結果を取得する。第36弾。

天皇杯(fetch_emperors_cup.py)との違い:
    天皇杯はJFA主催で、大会全試合が1本のJSON API
    (https://www.jfa.jp/match/emperorscup_{year}/match/schedule.json)にまとまっていた。
    ルヴァンはJリーグ主催でそのAPIが無く、日程ページのNext.jsペイロードから読む。
    TheSportsDBにもルヴァンのリーグは無い(2026-09-01に search_all_leagues.php で確認)。

    そして日程ページ https://www.jleague.jp/match/leaguecup/ は「いま表示すべきラウンド」しか
    出さない。全ラウンドをまとめて取る入口(日付範囲やラウンド指定のパラメータ)は見つからなかった。
    そのため毎回の実行で見えたぶんを既存ファイルにマージして貯める方式にしている。
    4時間おきに走るので、ラウンドが進むたびに自然に全部揃っていく。
    2026-27シーズンは9/2の1回戦が初戦なので、遡る対象がそもそも無い。

    一方で個別試合ページのURLはJ1〜J3とまったく同じ形
    (https://www.jleague.jp/match/leaguecup/2026/090201/livetxt/)なので、得点者・カード・交代・
    出場メンバー・ハイライト動画は fetch_match_events.py の仕組みがそのまま使える
    (match_events_parser.SCHEDULE_TOKEN_RE に leaguecup を足してある)。

日程ページの読み方(2026-09-01に実物で確認):
    1試合が "className":"m-schedule__link" を持つリンクで始まり、その中に出現順で
      1つ目の data-media="pc" -> ホームのクラブ名(フルネーム)
      m-schedule__match-info の children -> "18:30"(未消化なら時刻)
      2つ目の data-media="pc" -> アウェイのクラブ名
      m-schedule__info-stadium の pc -> 会場のフルネーム
    が並ぶ。ラウンド名("１回戦")と日付("2026/9/2 (水)")はグループの見出しとして
    試合の手前に1回だけ出るので、各試合の直前にある見出しを引き当てる。

    m-schedule__match-info は消化後にスコア表示へ変わると見られるが、
    ルヴァンには消化済みの試合がまだ1つも無いため実物で確認できていない。
    そこで中身を timeText としてそのまま保存し、"HH:MM" なら未消化、"N-N" ならスコアと
    解釈する。想定外の表記が来ても落とさず、生の文字列を残して人が気づけるようにする。

出力: data/processed/leaguecup.json (既存とマージ。試合コードで一意)

CLI:
    python scripts/fetch_leaguecup.py
    python scripts/fetch_leaguecup.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402
from fetch_official import extract_next_chunks  # noqa: E402
from fetch_news import MASTER_FILES  # noqa: E402

OUT_PATH = BASE_DIR / "data" / "processed" / "leaguecup.json"
INDEX_URL = "https://www.jleague.jp/match/leaguecup/"
COMPETITION_NAME = "JリーグYBCルヴァンカップ"
HEADERS = {"User-Agent": "jleague-app news fetcher (personal use)"}
TIMEOUT = 20.0

_PRIMARY_LINK_RE = re.compile(
    r'"href":"/match/leaguecup/(?P<year>\d{4})/(?P<code>\d{6})","locale"[^}]*?"className":"m-schedule__link'
)
_PC_NAME_RE = re.compile(r'"data-media":"pc","children":"([^"]+)"')
_MATCH_INFO_RE = re.compile(r'm-schedule__match-info"[^}]*?"children":"([^"]{1,16})"')
_STADIUM_RE = re.compile(r'm-schedule__info-stadium"[^}]*?"data-media":"pc","children":"([^"]+)"')
_ROUND_RE = re.compile(r'"children":"([^"]{0,10}(?:回戦|決勝|準決勝|準々決勝|プレーオフ)[^"]{0,6})"')
_DATE_RE = re.compile(r'"children":"(\d{4})/(\d{1,2})/(\d{1,2})\s*\([日月火水木金土]\)"')
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_SCORE_RE = re.compile(r"^(\d{1,2})\s*[-−]\s*(\d{1,2})$")
# 試合コードは MMDDNN(例 090201 = 9月2日の1試合目)。日付の第一の根拠にする。
_CODE_DATE_RE = re.compile(r"^(\d{2})(\d{2})\d{2}$")
# 得点イベントの scoreAfter("0-2")を読むため
_SCORE_AFTER_RE = re.compile(r"^\s*(\d{1,2})\s*[-−]\s*(\d{1,2})\s*$")
EVENTS_PATH = BASE_DIR / "data" / "processed" / "leaguecup_match_events.json"


def _date_from_code(year: str, code: str) -> str | None:
    """試合コード(MMDDNN)から日付を作る。

    なぜ見出しではなくコードを使うか:
        日程ページの日付見出しは、消化済みのグループで取りこぼすことがある。
        2026-09-04 に実際に起きた: 9/2 に終わった15試合が全部 9/9 として保存され、
        アプリ上で「6日後にキックオフ」に見えていた。
        _last_before() は「手前の最後の見出し」を返すので、見出しを1つ取りこぼすと
        黙って隣の日付を引き当ててしまう。コードは試合ごとに必ず付いているので、
        こちらの方が壊れにくい。
    """
    m = _CODE_DATE_RE.match(code or "")
    if not m:
        return None
    try:
        return date(int(year), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return None  # 13月など、コードがMMDDではなかった場合


def _final_score_from_goals(goals: list[dict]) -> dict | None:
    """得点イベントから最終スコアを作る。

    scoreAfter は「その得点の直後のスコア」で、得点は単調に増える。
    なので home/away それぞれの最大値がそのまま最終スコアになる
    (minute の"90+3"のような表記を解釈せずに済む)。
    PK戦は含まない。
    """
    best_h = best_a = None
    for g in goals or []:
        m = _SCORE_AFTER_RE.match(str(g.get("scoreAfter") or ""))
        if not m:
            continue
        h, a = int(m.group(1)), int(m.group(2))
        best_h = h if best_h is None else max(best_h, h)
        best_a = a if best_a is None else max(best_a, a)
    if best_h is None:
        return None
    return {"home": best_h, "away": best_a}


def fill_scores_from_events(matches: list[dict]) -> int:
    """日程ページからスコアを取れなかった消化済み試合に、個別試合ページの得点から補う。

    これは応急処置。2026-09-04 時点で、日程ページの消化済み試合は
    「時刻でも N-N でもない表記」になっていて _MATCH_INFO_RE で拾えていない。
    一方で個別試合ページ側(leaguecup_match_events.json)は正常に取れているので、
    そちらから埋める。日程ページから正規に取れるようになったら、そちらが優先される
    (score が入っている試合には触れない)。

    今日の試合には触れない。試合中の途中経過を「終了」として保存してしまうため。
    """
    if not EVENTS_PATH.exists():
        return 0
    try:
        events = (json.loads(EVENTS_PATH.read_text(encoding="utf-8")) or {}).get("events") or {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] {EVENTS_PATH.name} を読めなかった: {e}", file=sys.stderr)
        return 0

    today = datetime.now(JST).date().isoformat()
    filled = 0
    for m in matches:
        if m.get("score") or not m.get("date") or m["date"] >= today:
            continue
        ev = events.get(m.get("code"))
        if ev is None or ev.get("goals") is None:
            continue
        goals = ev["goals"]
        sc = _final_score_from_goals(goals)
        if sc is None:
            if goals:
                # 得点はあるのに scoreAfter が読めない = 想定外。推測で埋めない
                print(f"[warn] {m['code']}: 得点{len(goals)}件あるが scoreAfter を読めなかった",
                      file=sys.stderr)
                continue
            sc = {"home": 0, "away": 0}  # イベント取得済みで得点なし = 0-0
        m["score"] = sc
        m["finished"] = True
        m["scoreSource"] = "events"  # 日程ページ由来ではない印
        filled += 1
    return filled


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).replace(" ", "").replace("　", "").strip()


def load_master_index() -> dict[str, dict]:
    """クラブ名(正規化済み) -> {idTeam, league, ja} 。ja と short の両方を引けるようにする。"""
    out: dict[str, dict] = {}
    for league, path in MASTER_FILES.items():
        raw = json.loads(path.read_text(encoding="utf-8"))
        teams = raw["teams"] if isinstance(raw, dict) and "teams" in raw else raw
        for t in teams:
            info = {"idTeam": t["idTeam"], "league": league, "ja": t.get("ja")}
            for key in (t.get("ja"), t.get("short")):
                if key:
                    out.setdefault(norm(key), info)
    return out


def _last_before(matches: list[re.Match], pos: int):
    """pos より手前にある最後の見出しを返す(グループ見出しを各試合に引き当てる)。"""
    found = None
    for m in matches:
        if m.start() < pos:
            found = m
        else:
            break
    return found


def parse_index(text: str, master: dict[str, dict]) -> list[dict]:
    """日程ページの結合済みペイロードから試合の配列を作る。"""
    rounds = list(_ROUND_RE.finditer(text))
    dates = list(_DATE_RE.finditer(text))
    links = list(_PRIMARY_LINK_RE.finditer(text))

    out: list[dict] = []
    mismatches: list[tuple[str, str, str]] = []
    for i, link in enumerate(links):
        end = links[i + 1].start() if i + 1 < len(links) else link.start() + 6000
        blk = text[link.start():end]

        names = _PC_NAME_RE.findall(blk)
        if len(names) < 2:
            continue  # 想定外の並び。落とさずスキップする
        info = _MATCH_INFO_RE.search(blk)
        time_text = info.group(1).strip() if info else ""
        stadium = _STADIUM_RE.search(blk)

        rnd = _last_before(rounds, link.start())
        dt = _last_before(dates, link.start())
        heading_date = (f"{dt.group(1)}-{int(dt.group(2)):02d}-{int(dt.group(3)):02d}"
                        if dt else None)
        code_date = _date_from_code(link.group("year"), link.group("code"))
        date_str = code_date or heading_date
        if code_date and heading_date and code_date != heading_date:
            mismatches.append((link.group("code"), code_date, heading_date))

        kickoff = None
        score = None
        finished = False
        tm = _TIME_RE.match(time_text)
        sc = _SCORE_RE.match(time_text)
        if tm and date_str:
            kickoff = f"{date_str}T{int(tm.group(1)):02d}:{tm.group(2)}:00+09:00"
        elif sc:
            score = {"home": int(sc.group(1)), "away": int(sc.group(2))}
            finished = True

        def side(name: str) -> dict:
            hit = master.get(norm(name))
            return {"name": name,
                    "idTeam": hit["idTeam"] if hit else None,
                    "league": hit["league"] if hit else None}

        out.append({
            "code": link.group("code"),
            "year": link.group("year"),
            "round": rnd.group(1) if rnd else None,
            "date": date_str,
            "kickoffJst": kickoff,
            # 想定外の表記でも捨てずに残す。時刻でもスコアでもない値が入っていたら
            # ページの作りが変わった合図になる
            "timeText": time_text,
            "venue": stadium.group(1) if stadium else None,
            "home": side(names[0]),
            "away": side(names[1]),
            "score": score,
            "finished": finished,
            "matchPageUrl": f"https://www.jleague.jp/match/leaguecup/{link.group('year')}/{link.group('code')}/",
        })
    if mismatches:
        # コードを採用するが、見出しとずれたこと自体は見えるようにしておく
        print(f"[warn] 日付見出しと試合コードが食い違った{len(mismatches)}件"
              f"(コードを採用): {mismatches[:3]}", file=sys.stderr)
    return out


def _rounds_in_order(matches: list[dict]) -> list[str]:
    """出てきたラウンド名を、日付順(=大会の進行順)で重複なく並べる。"""
    out: list[str] = []
    for m in matches:
        r = m.get("round")
        if r and r not in out:
            out.append(r)
    return out


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """試合コードで一意にして、今回見えたものを優先する(ラウンドが進んでも過去を失わない)。"""
    by_code = {m["code"]: m for m in existing}
    for m in fresh:
        by_code[m["code"]] = m
    return sorted(by_code.values(), key=lambda m: (m.get("date") or "9999", m.get("code") or ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    resp = requests.get(INDEX_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    text = "".join(extract_next_chunks(resp.text).values())

    fresh = parse_index(text, load_master_index())
    print(f"[info] 日程ページから {len(fresh)}試合")
    if not fresh:
        print("[error] 1試合も拾えなかった。ページの作りが変わった可能性がある", file=sys.stderr)
        sys.exit(1)

    existing = []
    if OUT_PATH.exists():
        try:
            existing = (json.loads(OUT_PATH.read_text(encoding="utf-8")) or {}).get("matches") or []
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] 既存の{OUT_PATH.name}を読めなかった。新規として扱う: {e}", file=sys.stderr)

    merged = merge(existing, fresh)
    filled = fill_scores_from_events(merged)
    if filled:
        print(f"[info] 日程ページからスコアを取れなかった{filled}試合に、"
              f"得点イベントからスコアを補った")
    odd = [m["timeText"] for m in fresh
           if m["timeText"] and not (_TIME_RE.match(m["timeText"]) or _SCORE_RE.match(m["timeText"]))]
    if odd:
        print(f"[warn] 時刻でもスコアでもない表記があった(ページの作りが変わったかも): {sorted(set(odd))[:5]}",
              file=sys.stderr)

    out = {
        "meta": {
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "competitionName": COMPETITION_NAME,
            "sourceUrl": INDEX_URL,
            "source": "Jリーグ公式",
            "matchCount": len(merged),
            "finishedCount": sum(1 for m in merged if m.get("finished")),
            "clubMatchedCount": sum(1 for m in merged
                                    if m["home"]["idTeam"] and m["away"]["idTeam"]),
        },
        # 天皇杯(emperors_cup.json)と同じ形にしておく。アプリ側のラウンド切替タブが共用できる。
        # 日付順に初めて出てきた順で並べる(大会の進行順になる)。
        "rounds": _rounds_in_order(merged),
        "matches": merged,
    }
    print(f"[info] 累計 {len(merged)}試合 (今回 新規/更新 {len(fresh)}件) "
          f"ラウンド: {out['rounds']}")
    if args.dry_run:
        print("[info] --dry-run なので書き込まない")
        return
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[info] {OUT_PATH} を更新した")


if __name__ == "__main__":
    main()
