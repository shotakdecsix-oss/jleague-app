"""
3リーグ(J1/J2/J3)のマスタを統合したときに、正規化後の完全一致キーが
複数クラブにまたがっていないかをチェックする。

想定するリスク（部分一致ではなく、短縮alias同士が完全一致してしまうケース）:
  - FC東京の alias "Tokyo" と Tokyo Verdy
  - FC大阪(J3)の alias "Osaka" と Gamba Osaka / Cerezo Osaka(J1)
  - 栃木SC(J3)の alias "Tochigi" と Tochigi City(J2)

実行方法:
    python scripts/check_key_collisions.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_matching import check_key_collisions, load_master_teams  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS = {
    "J1": BASE_DIR / "data" / "masters" / "j1_teams_2026-27.json",
    "J2": BASE_DIR / "data" / "masters" / "j2_master_2026-27.json",
    "J3": BASE_DIR / "data" / "masters" / "j3_teams_2026-27.json",
}


def main() -> None:
    all_teams: list[dict] = []
    for league, path in MASTERS.items():
        raw = json.loads(path.read_text(encoding="utf-8"))
        teams = load_master_teams(league, raw)
        all_teams.extend(teams)
        print(f"[info] {league}: {len(teams)}クラブ読み込み ({path.name})")

    print(f"[info] 合計 {len(all_teams)}クラブ / en+aliasesの総キー数を突合中...")

    collisions = check_key_collisions(all_teams)

    if not collisions:
        print("\n衝突なし: 正規化キーの完全一致による誤爆リスクは検出されませんでした。")
        return

    print(f"\n=== 衝突検出: {len(collisions)}件 ===")
    for key, owners in sorted(collisions.items()):
        print(f"  KEY={key!r}")
        for o in owners:
            print(f"    - {o}")
    sys.exit(1)


if __name__ == "__main__":
    main()
