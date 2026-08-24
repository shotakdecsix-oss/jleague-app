"""
第16弾: 順位表の近くに表示する「第1節からの順位推移」グラフ用データを作る。

data/processed/{league}_matches.json を読み、各節終了時点(その節以下の試合結果のみ)での
順位表を standings.py の build_standings_table() で再計算する。standings.py 側の順位決定
ロジック(勝点→得失点差→総得点→当該チーム間比較)をそのまま再利用するので、二重実装による
ズレは起きない。ネットワークアクセスなし。

出力: data/history/{league}_rank_history.json
  {
    "meta": {"league": "j1", "generatedAtJst": "...", "maxRound": 12},
    "rounds": [1, 2, ..., 12],
    "ranks": {idTeam: [第1節終了時点の順位, 第2節終了時点の順位, ...], ...}
  }
  試合が1つも消化されていないリーグは rounds:[] ranks:{} を書き出す(アプリ側はグラフ非表示にする)。

CLI:
    python scripts/rank_history.py --league j2
    python scripts/rank_history.py --league all
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402
from standings import build_standings_table, load_master_teams  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
HISTORY_DIR = BASE_DIR / "data" / "history"

LEAGUES = ["j1", "j2", "j3"]


def load_matches(league: str) -> list[dict]:
    path = PROCESSED_DIR / f"{league}_matches.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["matches"]


def compute_rank_history(league: str) -> dict:
    matches = load_matches(league)
    master_teams = load_master_teams(league)

    finished_rounds = sorted({m["round"] for m in matches if m.get("finished") and m.get("round")})
    if not finished_rounds:
        return {
            "meta": {"league": league, "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"), "maxRound": 0},
            "rounds": [],
            "ranks": {},
        }

    last_round = max(finished_rounds)
    rounds = list(range(1, last_round + 1))
    ranks: dict[str, list[int | None]] = {t["idTeam"]: [] for t in master_teams}

    for r in rounds:
        subset = [m for m in matches if (m.get("round") or 0) <= r]
        table, _aux = build_standings_table(subset, master_teams)
        rank_by_team = {row["idTeam"]: row["rank"] for row in table}
        for tid in ranks:
            ranks[tid].append(rank_by_team.get(tid))

    return {
        "meta": {
            "league": league,
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "maxRound": last_round,
        },
        "rounds": rounds,
        "ranks": ranks,
    }


def write_league_output(league: str) -> None:
    matches_path = PROCESSED_DIR / f"{league}_matches.json"
    if not matches_path.exists():
        print(f"[error] {matches_path} が無い。先に fetch_batch.py --league {league} を実行すること", file=sys.stderr)
        return

    data = compute_rank_history(league)
    out_path = HISTORY_DIR / f"{league}_rank_history.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[info] {out_path} に第{len(data['rounds'])}節ぶんの順位推移を書き出し")


def main() -> None:
    parser = argparse.ArgumentParser(description="第1節からの順位推移データを作る(ネットワークアクセスなし)")
    parser.add_argument("--league", choices=[*LEAGUES, "all"], default="all")
    args = parser.parse_args()

    target_leagues = LEAGUES if args.league == "all" else [args.league]
    for league in target_leagues:
        write_league_output(league)


if __name__ == "__main__":
    main()
