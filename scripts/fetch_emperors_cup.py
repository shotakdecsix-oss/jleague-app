"""
天皇杯(JFA 全日本サッカー選手権大会)の日程・結果・得点者を取得する。第23弾フェーズ1。

出典はJリーグ公式(jleague.jp)ではなくJFA(www.jfa.jp)。「日程・結果」ページのHTMLには
データが埋め込まれておらず、クライアント側のJSが下のJSON APIを叩いて描画している。
素のGET(cookie等は不要)でそのまま同じJSONが返るので、HTMLの正規表現パースは要らない。

    https://www.jfa.jp/match/emperorscup_{year}/match/schedule.json

1本のJSONに1回戦〜決勝まで大会全試合が入っており、ラウンド名 / 試合番号 / 日時 / 会場 /
ホーム・アウェイのチーム名と所属区分(J1・J2・J3・都道府県代表) / スコア(延長・PK内訳込み) /
得点者("90+2分 渡邊 星来" 形式の文字列)まで構造化データで取れる。

matchNumber は個別試合ページのURLにそのまま対応する:
    https://www.jfa.jp/match/emperorscup_{year}/match_page/m{matchNumber}.html
この個別ページにカード / 交代 / 出場メンバー / ハイライト動画まで載っている(フェーズ2の情報源)。

チームの突き合わせ: JFA側はチーム名の文字列しか持たないので、data/masters/*.json の ja 名
(と short 名)を NFKC 正規化して照合し、一致したものだけ idTeam / league を付ける。
アマチュア(都道府県代表)は当然一致しないので idTeam=null のままにする。これは異常ではない。

安全弁: 取得できた試合数が既存ファイルの半分未満まで激減したら、書き出さずに異常終了する
(部分的・壊れたレスポンスで正常なデータを上書きしないため)。

出力: data/processed/emperors_cup.json

CLI:
    python scripts/fetch_emperors_cup.py
    python scripts/fetch_emperors_cup.py --year 2027
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
OUT_PATH = PROCESSED_DIR / "emperors_cup.json"

MASTER_FILES = {
    "j1": MASTERS_DIR / "j1_teams_2026-27.json",
    "j2": MASTERS_DIR / "j2_master_2026-27.json",
    "j3": MASTERS_DIR / "j3_teams_2026-27.json",
}

TIMEOUT = 20.0
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jleague-app personal use"}

# 「未定」等、値が入っていないことを表す文字列。JFA側は空文字と「未定」の両方を使う。
TBD_TOKENS = {"", "未定", "-", "‐", "―"}

SCORER_RE = re.compile(r"^\s*(\d+(?:\+\d+)?)\s*分\s*(.*)$")


def schedule_url(year: int) -> str:
    return f"https://www.jfa.jp/match/emperorscup_{year}/match/schedule.json"


def match_page_url(year: int, match_number: str) -> str:
    return f"https://www.jfa.jp/match/emperorscup_{year}/match_page/m{match_number}.html"


def norm(s) -> str:
    """全角/半角・記号ゆれを吸収する。照合専用で、表示にはこの値を使わない。"""
    if not isinstance(s, str):
        return ""
    t = unicodedata.normalize("NFKC", s)
    return re.sub(r"[\s　・･.,'’\-−―ー]", "", t).lower()


def is_tbd(v) -> bool:
    return not isinstance(v, str) or v.strip() in TBD_TOKENS


def load_team_index() -> dict[str, dict]:
    """
    正規化チーム名 -> {idTeam, league, ja} の索引を作る。
    ja(正式名)を優先し、short名は既に登録済みのキーを上書きしない(短縮名の衝突を避ける)。
    """
    index: dict[str, dict] = {}
    for league, path in MASTER_FILES.items():
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for t in data.get("teams", []):
            entry = {"idTeam": t.get("idTeam"), "league": league, "ja": t.get("ja")}
            key = norm(t.get("ja"))
            if key:
                index[key] = entry
            for alt in (t.get("short"),):
                k = norm(alt)
                if k and k not in index:
                    index[k] = entry
    return index


def resolve_team(name: str, qualification: str, index: dict[str, dict]) -> dict:
    """JFAのチーム名をJリーグ側のidTeamに突き合わせる。一致しなければidTeam=Noneのまま返す。"""
    hit = index.get(norm(name))
    return {
        "name": name or "",
        "qualification": qualification or "",
        "idTeam": hit["idTeam"] if hit else None,
        "league": hit["league"] if hit else None,
    }


def parse_kickoff(date_text, time_text) -> str | None:
    """'2026/08/19' + '18:30' -> '2026-08-19T18:30:00+09:00'。片方でも未定ならNone。"""
    if is_tbd(date_text) or is_tbd(time_text):
        return None
    m = re.match(r"^\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})", date_text)
    t = re.match(r"^\s*(\d{1,2}):(\d{2})", time_text)
    if not (m and t):
        return None
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(t.group(1)), int(t.group(2)), tzinfo=JST)
    except ValueError:
        return None
    return dt.isoformat()


def parse_date_only(date_text) -> str | None:
    """'2026/08/19' -> '2026-08-19'。時刻が未定でも日付だけは並べ替えに使えるので別に持つ。"""
    if is_tbd(date_text):
        return None
    m = re.match(r"^\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})", date_text)
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _int_or_none(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def parse_score(score) -> dict | None:
    """
    スコア。90分(+延長)の最終スコアを home/away に入れ、PKがあれば pk を付ける。
    まだ数字が入っていない(未消化)なら None を返す。
    """
    if not isinstance(score, dict):
        return None
    home = _int_or_none(score.get("homeScore"))
    away = _int_or_none(score.get("awayScore"))
    if home is None or away is None:
        return None
    out = {"home": home, "away": away, "extra": bool(score.get("exMatch"))}
    pk_home = _int_or_none(score.get("homePKScore"))
    pk_away = _int_or_none(score.get("awayPKScore"))
    if pk_home is not None and pk_away is not None:
        out["pk"] = {"home": pk_home, "away": pk_away}
    return out


def parse_scorer_line(line: str) -> dict:
    """'90+2分 渡邊 星来' -> {'minute': '90+2', 'name': '渡邊 星来', 'text': ...}。形が違えばtextだけ。"""
    text = (line or "").strip()
    m = SCORER_RE.match(text)
    if not m:
        return {"minute": None, "name": text, "text": text}
    return {"minute": m.group(1), "name": m.group(2).strip(), "text": text}


def parse_scorers(scorer) -> dict:
    if not isinstance(scorer, dict):
        return {"home": [], "away": []}
    def conv(key):
        raw = scorer.get(key)
        if not isinstance(raw, list):
            return []
        return [parse_scorer_line(x) for x in raw if isinstance(x, str) and x.strip()]
    return {"home": conv("homeScorer"), "away": conv("awayScorer")}


def convert_match(raw: dict, index: dict[str, dict], year: int) -> dict:
    number = str(raw.get("matchNumber") or "").strip()
    date_text = raw.get("matchDateJpn") or raw.get("matchDate") or ""
    time_text = raw.get("matchTimeJpn") or raw.get("matchTime") or ""
    kickoff = parse_kickoff(date_text, time_text)
    score = parse_score(raw.get("score"))
    venue = raw.get("venue") or ""
    return {
        "matchNumber": number,
        "round": unicodedata.normalize("NFKC", str(raw.get("matchTypeName") or "").strip()),
        "kickoffJst": kickoff,
        "kickoffTbd": kickoff is None,
        "date": parse_date_only(date_text),
        "venue": "" if is_tbd(venue) else venue,
        "venueFull": raw.get("venueFullName") or "",
        "home": resolve_team(raw.get("homeTeamName"), raw.get("homeTeamQualificationDescription"), index),
        "away": resolve_team(raw.get("awayTeamName"), raw.get("awayTeamQualificationDescription"), index),
        "score": score,
        "finished": score is not None,
        "status": raw.get("matchStatus") or "",
        "scorers": parse_scorers(raw.get("scorer")),
        "matchPageUrl": match_page_url(year, number) if number else None,
    }


def build_emperors_cup(payload: dict, index: dict[str, dict], year: int) -> dict:
    """
    APIのJSON全体(=トップレベル)を受け取り、アプリが読む形に変換する。
    ファイルI/Oもネットワークアクセスもしない(テスト用に分離)。
    """
    holder = payload.get("matchScheduleList")
    if not isinstance(holder, dict):
        raise ValueError("matchScheduleList が見つからない(APIのレスポンス形式が変わった可能性)")
    raw_matches = holder.get("matchSchedule")
    if not isinstance(raw_matches, list) or not raw_matches:
        raise ValueError("matchSchedule が空(APIのレスポンス形式が変わった可能性)")

    matches = [convert_match(m, index, year) for m in raw_matches if isinstance(m, dict)]

    # ラウンドは出現順を保つ(「1回戦→…→決勝」の順で並んでいるため、名前でソートしない)
    rounds: list[str] = []
    for m in matches:
        if m["round"] and m["round"] not in rounds:
            rounds.append(m["round"])

    matched = sum(1 for m in matches if m["home"]["idTeam"] or m["away"]["idTeam"])
    return {
        "meta": {
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "competitionName": holder.get("competitionName") or "天皇杯",
            "year": year,
            "sourceUrl": schedule_url(year),
            "source": "JFA",
            "matchCount": len(matches),
            "finishedCount": sum(1 for m in matches if m["finished"]),
            "clubMatchedCount": matched,
        },
        "rounds": rounds,
        "matches": matches,
    }


def fetch_schedule(year: int) -> dict:
    import requests

    resp = requests.get(schedule_url(year), headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def existing_match_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("meta", {}).get("matchCount") or 0)
    except Exception:  # noqa: BLE001
        return 0


def guard_shrink(new_count: int, old_count: int) -> str | None:
    """件数が激減していたら理由の文字列を返す(=書き出しを止める)。問題なければNone。"""
    if old_count >= 8 and new_count < old_count / 2:
        return f"試合数が {old_count} -> {new_count} に激減している"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="天皇杯の日程・結果・得点者を取得する")
    parser.add_argument("--year", type=int, default=datetime.now(JST).year,
                        help="大会年度(既定: 現在のJSTの年)")
    args = parser.parse_args()

    try:
        payload = fetch_schedule(args.year)
    except Exception as e:  # noqa: BLE001
        print(f"[error] 天皇杯の日程JSONの取得に失敗: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        out = build_emperors_cup(payload, load_team_index(), args.year)
    except Exception as e:  # noqa: BLE001
        print(f"[error] 天皇杯の日程JSONの解析に失敗: {e}", file=sys.stderr)
        sys.exit(1)

    reason = guard_shrink(out["meta"]["matchCount"], existing_match_count(OUT_PATH))
    if reason:
        print(f"[error] {reason}ため、既存の {OUT_PATH.name} を上書きせずに中断する", file=sys.stderr)
        sys.exit(1)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = out["meta"]
    print(
        f"[info] {OUT_PATH} に書き出し "
        f"({meta['competitionName']} / 全{meta['matchCount']}試合 / "
        f"消化{meta['finishedCount']} / Jクラブ照合{meta['clubMatchedCount']} / "
        f"ラウンド{len(out['rounds'])}種)"
    )


if __name__ == "__main__":
    main()
