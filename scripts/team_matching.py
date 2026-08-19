"""
クラブ名の正規化・照合ロジック（共通モジュール）。

handoff-jleague-dashboard.md / prompt-jleague-batch-impl.md の定義に従う:
- Unicode NFKD でアクセント記号除去（Jubilo等）
- 小文字化
- ピリオド・ハイフン・中黒(・)・連続空白の除去
- 比較は en を優先、失敗したら aliases を順に試す
- 未一致は例外を投げず、警告として記録し処理は続行する
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


_STRIP_CHARS = re.compile(r"[.\-・]")  # ピリオド・ハイフン・中黒
_MULTI_SPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """比較用にクラブ名を正規化する。"""
    if not name:
        return ""
    # NFKD分解してアクセント記号（結合文字）を除去
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    stripped = _STRIP_CHARS.sub("", lowered)
    collapsed = _MULTI_SPACE.sub(" ", stripped).strip()
    return collapsed


@dataclass
class MatchResult:
    matched: dict[str, dict] = field(default_factory=dict)  # api名 -> masterチームdict
    unmatched: list[str] = field(default_factory=list)       # 未一致だったapi名のリスト
    matched_via_alias: dict[str, str] = field(default_factory=dict)  # api名 -> 一致したalias


def build_lookup(master_teams: list[dict]) -> dict[str, tuple[dict, str]]:
    """
    正規化済み名前 -> (masterチームdict, どのフィールドで一致したか) の辞書を作る。
    en を優先登録し、aliases は en と衝突しない範囲で登録する。
    """
    lookup: dict[str, tuple[dict, str]] = {}
    for team in master_teams:
        key_en = normalize_name(team["en"])
        lookup.setdefault(key_en, (team, "en"))
        for alias in team.get("aliases", []):
            key_alias = normalize_name(alias)
            lookup.setdefault(key_alias, (team, f"alias:{alias}"))
    return lookup


def match_teams(api_team_names: list[str], master_teams: list[dict]) -> MatchResult:
    """API側のチーム名リストをマスタに照合する。未一致は例外を投げず記録する。"""
    lookup = build_lookup(master_teams)
    result = MatchResult()

    for api_name in api_team_names:
        key = normalize_name(api_name)
        hit = lookup.get(key)
        if hit is None:
            result.unmatched.append(api_name)
            continue
        team, via = hit
        result.matched[api_name] = team
        if via != "en":
            result.matched_via_alias[api_name] = via

    return result


def load_master_teams(league: str, raw: dict) -> list[dict]:
    """
    J1/J2/J3でJSONの形が違う（J1・J3はteamsがlist、J2はidTeamキーのdict）ものを
    共通の list[dict(league, idTeam, en, aliases, ja)] 形式に揃える。
    """
    teams_raw = raw["teams"]
    normalized: list[dict] = []

    if isinstance(teams_raw, list):
        # J1 / J2 / J3 形式
        for t in teams_raw:
            normalized.append(
                {
                    "league": league,
                    "idTeam": t.get("idTeam"),
                    "en": t["en"],
                    "aliases": t.get("aliases", []),
                    "aliasesJa": t.get("aliasesJa", []),
                    "ja": t.get("ja"),
                }
            )
    elif isinstance(teams_raw, dict):
        # 旧J2形式: idTeamをキーにしたdict。互換のため残す。
        for id_team, t in teams_raw.items():
            normalized.append(
                {
                    "league": league,
                    "idTeam": id_team,
                    "en": t["en"],
                    "aliases": t.get("aliases", []),
                    "aliasesJa": t.get("aliasesJa", []),
                    "ja": t.get("ja"),
                }
            )
    else:
        raise ValueError(f"未知のteams形式です: league={league}")

    return normalized


def match_teams_in_text(text: str, all_teams: list[dict]) -> list[dict]:
    """
    ニュース記事のタイトル(+概要)に、どのクラブの名前が含まれるかを調べる。

    照合に使うのは ja と aliasesJa(日本語の愛称・略称)のみ。英語の aliases は
    TheSportsDB側のチーム名照合専用なので使わない。short も引き続き使わない。

    「C大阪」が「FC大阪」の部分文字列になってしまう衝突を避けるため、
    長い語から順に照合し、マッチした部分は文字列から取り除いてから次の語を照合する
    (docs/note-aliases-ja.md 参照)。これにより「FC大阪」が先に消費され、
    「C大阪」は残った文字列に現れなくなる。

    1本の記事が複数クラブにヒットしてもよい(移籍記事など)ので、リストで返す。
    未一致は空リスト(例外は投げない)。
    """
    norm = normalize_name(text)
    if not norm:
        return []

    terms: list[tuple[str, dict]] = []
    for team in all_teams:
        candidates = [team.get("ja")] + list(team.get("aliasesJa", []))
        for candidate in candidates:
            if not candidate:
                continue
            key = normalize_name(candidate)
            if key:
                terms.append((key, team))
    terms.sort(key=lambda x: -len(x[0]))  # 長い語から順に

    matched: list[dict] = []
    seen: set[str] = set()
    for key, team in terms:
        if key in norm:
            norm = norm.replace(key, " ")  # マッチした部分を消費し、以降の照合から除外する
            if team["idTeam"] not in seen:
                seen.add(team["idTeam"])
                matched.append(team)
    return matched


def check_key_collisions(all_teams: list[dict]) -> dict[str, list[str]]:
    """
    正規化キーが複数クラブにまたがって存在しないかチェックする。
    3リーグを1つのマスタ/ルックアップに統合したときの誤爆（部分一致ではなく、
    同一の完全一致キーを別クラブが持ってしまうケース）を検出する軽量チェック。

    戻り値: {正規化キー: ["J1:FC Tokyo (en)", "J3:Tochigi SC (alias:Tochigi)", ...]}
    衝突が無ければ空dict。
    """
    key_owners: dict[str, list[str]] = {}

    for team in all_teams:
        label_base = f"{team['league']}:{team['en']}"
        key_en = normalize_name(team["en"])
        key_owners.setdefault(key_en, []).append(f"{label_base} (en)")
        for alias in team.get("aliases", []):
            key_alias = normalize_name(alias)
            key_owners.setdefault(key_alias, []).append(f"{label_base} (alias:{alias})")

    collisions = {
        key: owners
        for key, owners in key_owners.items()
        if len({o.split(" (")[0] for o in owners}) > 1  # 異なるクラブが同じキーを持つ場合のみ
    }
    return collisions
