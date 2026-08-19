"""
data/processed/{league}_matches.json を読んで順位表を計算する。ネットワークアクセスなし。

CLI:
    python scripts/standings.py --league j2

順位決定基準(この順):
  1. 勝点(勝3・分1・負0)
  2. 得失点差
  3. 総得点
  4. 当該チーム間の勝点
  5. 当該チーム間の得失点差
  6. 当該チーム間の総得点

当該チーム間比較:
  - 3クラブ以上が1〜3で並んだ場合は、並んだクラブ同士だけでミニリーグを構成して比較する
  - クラスタ内の全ペアが最低1試合を消化しているときだけ順序をつける。
    1ペアでも未消化ならミニリーグは成立しない(比較不能) -> クラスタ全員が同順位扱い(tiedWithに互いを入れる)。
    (対戦していないチームに(0,0,0)を割り当てて部分的に順序をつけると、負けたチームより
    未対戦のチームが上に来る捏造が起きるため、全ペア消化を必須条件にしている)

build_records() と rank_teams() はファイルI/O・print・グローバル状態を一切持たない純粋関数。
モンテカルロ・シミュレーション側からも「仮想の試合結果リスト」をmatchesとして渡せば
同じ関数で順位が出せる。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

MASTER_FILES = {
    "j1": MASTERS_DIR / "j1_teams_2026-27.json",
    "j2": MASTERS_DIR / "j2_master_2026-27.json",
    "j3": MASTERS_DIR / "j3_teams_2026-27.json",
}


@dataclass
class TeamRecord:
    idTeam: str
    win: int = 0
    draw: int = 0
    loss: int = 0
    gf: int = 0
    ga: int = 0
    chrono: list[str] = field(default_factory=list)  # 古い順の'W'/'D'/'L'

    @property
    def played(self) -> int:
        return self.win + self.draw + self.loss

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    @property
    def points(self) -> int:
        return self.win * 3 + self.draw

    def recent5(self) -> list[str]:
        return list(reversed(self.chrono[-5:]))


def build_records(matches: list[dict]) -> dict[str, TeamRecord]:
    """finishedな試合からidTeam -> TeamRecordを作る。純粋関数。"""
    finished = [m for m in matches if m.get("finished")]
    # kickoffJstがあれば時系列順に整列(recent5を正しい順序にするため)。無ければ入力順のまま。
    finished_sorted = sorted(finished, key=lambda m: m.get("kickoffJst") or "")

    records: dict[str, TeamRecord] = {}

    def _get(tid: str) -> TeamRecord:
        if tid not in records:
            records[tid] = TeamRecord(idTeam=tid)
        return records[tid]

    for m in finished_sorted:
        home, away = m["home"], m["away"]
        hid, aid = home["idTeam"], away["idTeam"]
        hs, asc = home["score"], away["score"]
        if hs is None or asc is None:
            continue  # finished=Trueなのにscore欠損は不正データ。安全側でスキップ

        hrec, arec = _get(hid), _get(aid)
        hrec.gf += hs
        hrec.ga += asc
        arec.gf += asc
        arec.ga += hs

        if hs > asc:
            hrec.win += 1
            arec.loss += 1
            hrec.chrono.append("W")
            arec.chrono.append("L")
        elif hs < asc:
            hrec.loss += 1
            arec.win += 1
            hrec.chrono.append("L")
            arec.chrono.append("W")
        else:
            hrec.draw += 1
            arec.draw += 1
            hrec.chrono.append("D")
            arec.chrono.append("D")

    return records


def _primary_key(rec: TeamRecord) -> tuple[int, int, int]:
    return (rec.points, rec.gd, rec.gf)


def _head_to_head_matches(cluster: set[str], matches: list[dict]) -> list[dict]:
    return [
        m for m in matches
        if m.get("finished")
        and m["home"]["idTeam"] in cluster
        and m["away"]["idTeam"] in cluster
    ]


def _resolve_tie_cluster(cluster: list[str], matches: list[dict]) -> list[list[str]]:
    cluster_set = set(cluster)
    h2h = _head_to_head_matches(cluster_set, matches)
    if not h2h:
        return [list(cluster)]  # 直接対決0試合 -> 比較不能、全員同順位

    # ミニリーグが成立するのは、クラスタ内の全ペアが最低1試合を消化しているときだけ。
    # 1ペアでも未消化なら「対戦していないチームに(0,0,0)を割り当てて部分順序をつける」ことになり、
    # 負けたチームより未対戦のチームが上位に来る捏造が起きる。その場合は比較不能として全員同順位にする。
    met_pairs = {frozenset((m["home"]["idTeam"], m["away"]["idTeam"])) for m in h2h}
    all_pairs_met = all(frozenset(pair) in met_pairs for pair in combinations(cluster, 2))
    if not all_pairs_met:
        return [list(cluster)]

    mini_records = build_records(h2h)

    def mini_key(tid: str) -> tuple[int, int, int]:
        rec = mini_records.get(tid)
        return _primary_key(rec) if rec else (0, 0, 0)

    ordered = sorted(cluster, key=mini_key, reverse=True)

    groups: list[list[str]] = []
    for tid in ordered:
        if groups and mini_key(groups[-1][0]) == mini_key(tid):
            groups[-1].append(tid)
        else:
            groups.append([tid])
    return groups


def rank_teams(records: dict[str, TeamRecord], matches: list[dict]) -> list[list[str]]:
    """
    順位グループのリストを返す。外側リストが順位順、内側リストが同順位のidTeam。
    例: [["137715"], ["137706", "137708"], ["137711"]] -> 1位/2位タイ/4位
    matchesは当該チーム間比較のために必要(finishedのみ参照する)。純粋関数。
    """
    ids = sorted(records.keys(), key=lambda tid: _primary_key(records[tid]), reverse=True)

    groups: list[list[str]] = []
    for tid in ids:
        if groups and _primary_key(records[groups[-1][0]]) == _primary_key(records[tid]):
            groups[-1].append(tid)
        else:
            groups.append([tid])

    resolved: list[list[str]] = []
    for group in groups:
        if len(group) == 1:
            resolved.append(group)
        else:
            resolved.extend(_resolve_tie_cluster(group, matches))

    return resolved


def compute_played_diff(records: dict[str, TeamRecord]) -> dict[str, int]:
    """リーグ内最多消化数との差を返す(延期試合の可視化用)。純粋関数。"""
    max_played = max((r.played for r in records.values()), default=0)
    return {tid: max_played - r.played for tid, r in records.items()}


def load_master_teams(league: str) -> list[dict]:
    raw = json.loads(MASTER_FILES[league].read_text(encoding="utf-8"))
    return raw["teams"]


def build_standings_table(matches: list[dict], master_teams: list[dict]) -> tuple[list[dict], dict]:
    """順位表の行リストと補助meta(basedOnMatches/maxPlayed)を作る。"""
    records = build_records(matches)
    for t in master_teams:
        records.setdefault(t["idTeam"], TeamRecord(idTeam=t["idTeam"]))  # 未消化クラブもplayed:0で載せる

    finished_matches = [m for m in matches if m.get("finished")]
    groups = rank_teams(records, finished_matches)
    played_diff = compute_played_diff(records)
    team_lookup = {t["idTeam"]: t for t in master_teams}

    table: list[dict] = []
    rank = 1
    for group in groups:
        for tid in group:
            rec = records[tid]
            info = team_lookup.get(tid, {})
            table.append({
                "rank": rank,
                "idTeam": tid,
                "ja": info.get("ja", ""),
                "short": info.get("short", ""),
                "played": rec.played,
                "win": rec.win,
                "draw": rec.draw,
                "loss": rec.loss,
                "gf": rec.gf,
                "ga": rec.ga,
                "gd": rec.gd,
                "points": rec.points,
                "playedDiff": played_diff[tid],
                "recent5": rec.recent5(),
                "tiedWith": [t for t in group if t != tid],
            })
        rank += len(group)

    aux_meta = {
        "basedOnMatches": len(finished_matches),
        "maxPlayed": max((r.played for r in records.values()), default=0),
    }
    return table, aux_meta


def main() -> None:
    parser = argparse.ArgumentParser(description="順位表計算(ネットワークアクセスなし)")
    parser.add_argument("--league", choices=["j1", "j2", "j3"], required=True)
    args = parser.parse_args()

    matches_path = PROCESSED_DIR / f"{args.league}_matches.json"
    if not matches_path.exists():
        print(f"[error] {matches_path} が無い。先に fetch_batch.py --league {args.league} を実行すること", file=sys.stderr)
        sys.exit(1)

    data = json.loads(matches_path.read_text(encoding="utf-8"))
    matches = data["matches"]
    master_teams = load_master_teams(args.league)

    table, aux_meta = build_standings_table(matches, master_teams)

    out = {
        "meta": {
            "league": args.league,
            "season": data.get("meta", {}).get("season", "2026-2027"),
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "basedOnMatches": aux_meta["basedOnMatches"],
            "maxPlayed": aux_meta["maxPlayed"],
        },
        "table": table,
    }

    out_path = PROCESSED_DIR / f"{args.league}_standings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[info] {out_path} に{len(table)}クラブぶんの順位表を書き出し")

    # 検算: 勝点合計(全クラブ) = 消化試合数 x3 - 引き分け数(延べ試合数ベース)
    finished_matches = [m for m in matches if m.get("finished")]
    records = build_records(matches)
    total_points = sum(r.points for r in records.values())
    total_draws_matches = sum(1 for m in finished_matches if m["home"]["score"] == m["away"]["score"])
    expected = len(finished_matches) * 3 - total_draws_matches
    if total_points != expected:
        print(
            f"[warn] 検算不一致: 勝点合計={total_points} 期待値={expected} "
            f"(消化試合数={len(finished_matches)}, 引き分け試合数={total_draws_matches})",
            file=sys.stderr,
        )
    else:
        print(
            f"[info] 検算OK: 勝点合計={total_points} = "
            f"消化試合数{len(finished_matches)}x3 - 引き分け試合数{total_draws_matches}"
        )

    played_diff_nonzero = [row["short"] for row in table if row["playedDiff"] != 0]
    if played_diff_nonzero:
        print(f"[info] playedDiff!=0のクラブ({len(played_diff_nonzero)}): {played_diff_nonzero}")


if __name__ == "__main__":
    main()
