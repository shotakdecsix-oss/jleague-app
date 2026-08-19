"""
検証用スクリプト: 指定リーグ・指定節のみを対象に、idTeam自動補完の照合ロジックを確認する。
（validate_j1_round1.py を一般化したもの。J1/J2/J3どれでも使える）

実行方法（ローカルPC / GitHub Actions 共通、Python 3.11+ + requests）:
    python scripts/validate_round.py --league j1 --round 1
    python scripts/validate_round.py --league j3 --round 1

オフライン確認用（このセッションでの検証時に使用。実運用では不要）:
    python scripts/validate_round.py --league j3 --round 1 --fixture data/fixtures/j3_round1_sample.json

--write を付けると、照合できたidTeamをマスタJSONに書き戻す。
付けない場合は結果表示のみで、マスタファイルは変更しない。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_matching import match_teams  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://www.thesportsdb.com/api/v1/json/123/eventsround.php"
SEASON = "2026-2027"

LEAGUES = {
    "j1": {"idLeague": "4633", "master": BASE_DIR / "data" / "masters" / "j1_teams_2026-27.json"},
    "j2": {"idLeague": "4824", "master": BASE_DIR / "data" / "masters" / "j2_master_2026-27.json"},
    "j3": {"idLeague": "4967", "master": BASE_DIR / "data" / "masters" / "j3_teams_2026-27.json"},
}


def fetch_round_events(id_league: str, round_: str) -> list[dict]:
    import requests

    params = {"id": id_league, "r": round_, "s": SEASON}
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = requests.get(API_URL, params=params, timeout=15)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RuntimeError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            return data.get("events") or []
        except Exception as e:  # noqa: BLE001
            last_err = e
            sleep_s = 2 ** attempt
            print(f"[warn] 取得失敗 (試行{attempt + 1}/4): {e} -> {sleep_s}秒後リトライ", file=sys.stderr)
            time.sleep(sleep_s)
    raise RuntimeError(f"eventsround.php の取得に失敗しました: {last_err}")


def load_fixture_events(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("events") or []


def extract_teams_list(master_json: dict) -> list[dict]:
    """J1/J3(list形式)・J2(idTeamキーのdict形式)どちらも list[dict] に揃えて返す。"""
    teams_raw = master_json["teams"]
    if isinstance(teams_raw, list):
        return teams_raw
    # J2形式: dictをlistに変換（idTeamは既に埋まっている想定）
    out = []
    for id_team, t in teams_raw.items():
        merged = dict(t)
        merged["idTeam"] = id_team
        merged.setdefault("aliases", [])
        out.append(merged)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="指定リーグ・節での idTeam 照合ロジック検証")
    parser.add_argument("--league", choices=sorted(LEAGUES), required=True)
    parser.add_argument("--round", default="1")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="APIを叩かずローカルJSONを使う（オフライン検証用）",
    )
    parser.add_argument("--write", action="store_true", help="照合できたidTeamをマスタJSONに書き戻す")
    args = parser.parse_args()

    league_cfg = LEAGUES[args.league]
    master_path = league_cfg["master"]

    if args.fixture:
        print(f"[info] フィクスチャから読み込み: {args.fixture}")
        events = load_fixture_events(args.fixture)
    else:
        print(f"[info] APIから取得: {API_URL}?id={league_cfg['idLeague']}&r={args.round}&s={SEASON}")
        events = fetch_round_events(league_cfg["idLeague"], args.round)

    filtered = [
        e for e in events
        if e.get("strSeason") == SEASON and e.get("intRound") not in (None, "0")
    ]
    print(f"[info] 取得イベント数: {len(events)} / フィルタ後: {len(filtered)}")

    if not filtered:
        print("[warn] フィルタ後0件。seasonフォールバック(2026)が必要な可能性があります。")
        return

    master = json.loads(master_path.read_text(encoding="utf-8"))
    master_teams = extract_teams_list(master)

    api_name_to_id: dict[str, str] = {}
    for e in filtered:
        home, away = e.get("strHomeTeam"), e.get("strAwayTeam")
        id_home, id_away = e.get("idHomeTeam"), e.get("idAwayTeam")
        if home and id_home:
            api_name_to_id[home] = id_home
        if away and id_away:
            api_name_to_id[away] = id_away

    result = match_teams(list(api_name_to_id.keys()), master_teams)

    print(f"\n=== 照合結果: {len(result.matched)}/{len(api_name_to_id)} 一致 ===")
    for api_name, team in sorted(result.matched.items()):
        via = result.matched_via_alias.get(api_name, "en")
        idteam = api_name_to_id[api_name]
        mark = " (alias経由)" if via != "en" else ""
        print(f"  OK  {api_name!r:30s} -> idTeam={idteam:>7s}  {team['ja']}{mark}")

    if result.unmatched:
        print(f"\n=== 未一致: {len(result.unmatched)}件 ===")
        for name in result.unmatched:
            print(f"  NG  {name!r}  (idTeam={api_name_to_id.get(name)})")
    else:
        print("\n未一致: なし")

    unresolved_in_master = [t["en"] for t in master_teams if not t.get("idTeam")]
    matched_masters_en = {team["en"] for team in result.matched.values()}
    still_missing = [en for en in unresolved_in_master if en not in matched_masters_en]
    if still_missing:
        print(f"\n[info] 今回の節に登場せずidTeam未確定のまま残るクラブ ({len(still_missing)}): {still_missing}")

    if args.write:
        if isinstance(master["teams"], list):
            updated = 0
            for team in master["teams"]:
                for api_name, matched_team in result.matched.items():
                    if matched_team.get("en") == team.get("en") and not team.get("idTeam"):
                        team["idTeam"] = api_name_to_id[api_name]
                        updated += 1
            master_path.write_text(
                json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"\n[info] マスタに書き戻し: {updated}件 -> {master_path}")
        else:
            print("\n[info] J2マスタは既にidTeamが実値のため書き戻し対象外です。")
    else:
        print("\n[info] --write を付けていないためマスタは未変更です。")


if __name__ == "__main__":
    main()
