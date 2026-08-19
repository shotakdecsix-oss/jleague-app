"""
[一度きりの構造移行スクリプト] J2マスタを J1/J3 と同じ list形式 + aliases付きに揃える。

移行前: "teams" が {idTeam: {en, ja, short, idVenue, isFavorite?}} のdict、aliasesフィールド無し
移行後: "teams" が [{idTeam, en, aliases, ja, short, idVenue, isFavorite?}] のlist

idTeamは既に実値が入っているので照合(match_teams)は不要。aliasesは他リーグと同じ粒度
（クラブ名の短縮形・表記ゆれ）で新規に付与する。栃木シティに "Tochigi" 単体のaliasは
付けない(J3の栃木SCと紛らわしいため、意図的に除外)。

venues セクション・promotionRules・meta はそのまま保持する(今回のスコープ外)。

実行方法:
    python scripts/migrate_j2_structure.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MASTER_PATH = BASE_DIR / "data" / "masters" / "j2_master_2026-27.json"

# 他リーグ(J1/J3)と同じ粒度のalias付与。
# 意図的に付けないもの: 栃木シティへの "Tochigi"(J3栃木SCと紛らわしいため)
ALIASES_BY_EN: dict[str, list[str]] = {
    "Shonan Bellmare": ["Shonan"],
    "Hokkaido Consadole Sapporo": ["Consadole Sapporo", "Consadole", "Sapporo"],
    "Oita Trinita": ["Oita"],
    "Sagan Tosu": ["Tosu"],
    "Vegalta Sendai": ["Sendai"],
    "Yokohama FC": ["Yokohama"],
    "Albirex Niigata": ["Niigata"],
    "Jubilo Iwata": ["Jubilo", "Iwata"],
    "Montedio Yamagata": ["Montedio", "Yamagata"],
    "RB Omiya Ardija": ["Omiya Ardija", "Omiya"],  # 旧名"Omiya Ardija"がAPI側に残っている可能性への対応
    "Tokushima Vortis": ["Tokushima"],
    "Ventforet Kofu": ["Ventforet", "Kofu"],
    "Blaublitz Akita": ["Blaublitz", "Akita"],
    "Fujieda MYFC": ["Fujieda"],
    "FC Imabari": ["Imabari"],
    "Kataller Toyama": ["Toyama"],
    "Tegevajaro Miyazaki": ["Tegevajaro", "Miyazaki"],
    "Vanraure Hachinohe": ["Vanraure", "Hachinohe"],
    "Iwaki FC": ["Iwaki"],
    "Tochigi City": ["Tochigi City FC"],
}


def main() -> None:
    master = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
    teams_dict = master["teams"]

    if isinstance(teams_dict, list):
        print("[info] 既にlist形式です。移行不要。")
        return

    new_teams: list[dict] = []
    missing_alias_defs: list[str] = []

    for id_team, t in teams_dict.items():
        en = t["en"]
        if en not in ALIASES_BY_EN:
            missing_alias_defs.append(en)
        team_obj = {
            "idTeam": id_team,
            "en": en,
            "aliases": ALIASES_BY_EN.get(en, []),
            "ja": t["ja"],
            "short": t.get("short"),
            "idVenue": t.get("idVenue"),
        }
        if "isFavorite" in t:
            team_obj["isFavorite"] = t["isFavorite"]
        new_teams.append(team_obj)

    if missing_alias_defs:
        print(f"[warn] alias未定義のクラブ ({len(missing_alias_defs)}): {missing_alias_defs}", file=sys.stderr)
        print("[warn] 未定義のまま aliases=[] で移行します。手動追加してください。", file=sys.stderr)

    # 表示順を維持するため、元のdict順（Python3.7+は挿入順維持）のままlist化している
    master["teams"] = new_teams
    master.setdefault("meta", {})["structureNote"] = (
        "2026-08-12: teamsをdict(idTeamキー)からlist形式+aliasesに移行(J1/J3と統一)。"
        "venuesセクションは従来通りidVenueキーのdictのまま維持。"
    )

    MASTER_PATH.write_text(json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[info] 移行完了: {len(new_teams)}クラブ -> {MASTER_PATH}")


if __name__ == "__main__":
    main()
