"""
3リーグの試合データを、日付軸ビュー専用の軽量ファイル1つに集約する。

data/processed/{league}_matches.json を3つともフロントで読む実装にはしない
(合計660KB あり、日付軸に混ぜて並べるためだけに全件パースすることになる)。
ここでキーを短縮した1ファイル data/processed/calendar.json を生成する
(1140試合で raw 約160KB、gzip後 約12KB。Render は gzip 配信なので実用上問題にならない)。

順位・予想はこのファイルでは扱わない(既存の {league}_standings.json / {league}_simulation.json が
それぞれ7KB/17KB程度と既に十分小さく、集約ファイルを別途作る必要が無いため)。

出力は昇順ソート済み(kickoffJst昇順、同時刻はj1→j2→j3、日程未定(kickoffJstがnull)の試合は末尾)。
フロント側で並べ替えさせない。

json.dumps(..., separators=(",", ":")) で出力する。このファイルだけは indent を付けない
(可読性より転送量を優先する)。

CLI:
    python scripts/build_calendar.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

LEAGUES = ["j1", "j2", "j3"]
LEAGUE_ORDER = {lg: i for i, lg in enumerate(LEAGUES)}  # 同時刻タイブレーク: j1 -> j2 -> j3

DEFAULT_SEASON = "2026-2027"


def load_matches(league: str) -> dict:
    path = PROCESSED_DIR / f"{league}_matches.json"
    if not path.exists():
        print(f"[warn] {path} が無い。{league}はcalendar.jsonに含めない", file=sys.stderr)
        return {"matches": [], "meta": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def shrink_match(m: dict, league: str) -> dict:
    home, away = m["home"], m["away"]
    return {
        "e": m.get("idEvent"),
        "l": league,
        "r": m.get("round"),
        "k": m.get("kickoffJst"),
        "b": bool(m.get("kickoffTbd")),
        "s": m.get("status"),
        "h": [home.get("idTeam"), home.get("short") or home.get("ja", ""), home.get("score")],
        "a": [away.get("idTeam"), away.get("short") or away.get("ja", ""), away.get("score")],
    }


def sort_key(m: dict) -> tuple:
    """kickoffJst昇順。nullは末尾にまとめる。同時刻はj1->j2->j3。"""
    has_date = m["k"] is not None
    return (0 if has_date else 1, m["k"] or "", LEAGUE_ORDER.get(m["l"], 99))


def build_calendar() -> dict:
    all_matches: list[dict] = []
    counts: dict[str, int] = {}
    season = DEFAULT_SEASON

    for league in LEAGUES:
        data = load_matches(league)
        matches = data.get("matches", [])
        counts[league] = len(matches)
        meta = data.get("meta") or {}
        if meta.get("season"):
            season = meta["season"]
        for m in matches:
            all_matches.append(shrink_match(m, league))

    all_matches.sort(key=sort_key)

    return {
        "meta": {
            "season": season,
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "leagues": LEAGUES,
            "counts": counts,
        },
        "matches": all_matches,
    }


def main() -> None:
    out = build_calendar()
    out_path = PROCESSED_DIR / "calendar.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    total = sum(out["meta"]["counts"].values())
    size_kb = out_path.stat().st_size / 1024
    print(f"[info] {out_path} に書き出し (試合数合計{total}件, {size_kb:.0f}KB)")


if __name__ == "__main__":
    main()
