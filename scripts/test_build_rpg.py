"""build_rpg の計算部分のテスト。

素材が4ファイル(stats/simulation/standings/club_extra)にまたがるので、
build_league() は一時ディレクトリに最小限のデータを置いて通す。
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_rpg as mod  # noqa: E402
from build_rpg import (  # noqa: E402
    blend,
    hp_state,
    ranks_desc,
    recent_points,
    squad_profile,
    to_stat,
    zscores,
)


def test_zscores() -> None:
    assert zscores([5, 5, 5, 5]) == [0.0, 0.0, 0.0, 0.0], "全部同じ値なら差が無い"
    assert zscores([]) == []
    assert zscores([3]) == [0.0], "1件では標準偏差が出せない"
    z = zscores([1, 2, 3, 4, 5])
    assert z[0] < 0 < z[4] and abs(z[2]) < 1e-9, z
    z2 = zscores([1, None, 3])
    assert z2[1] == 0.0, "値が無いクラブは平均(=0)として扱う"
    print("OK: zスコアは同値・欠損・1件でも壊れない")


def test_to_stat_clips() -> None:
    assert to_stat(0) == mod.CENTER
    assert to_stat(1) == mod.CENTER + mod.SPREAD
    assert to_stat(-99) == mod.VMIN, "下にはみ出しても1未満にはしない"
    assert to_stat(99) == mod.VMAX
    print("OK: パラメータは1〜255に収まる")


def test_ranks_desc() -> None:
    # 大きいほど1位。同値は同順位、次は飛ぶ(順位表と同じ流儀)
    assert ranks_desc([200, 150, 150, 100]) == [1, 2, 2, 4]
    assert ranks_desc([100, 100, 100]) == [1, 1, 1]
    assert ranks_desc([1, 2, 3]) == [3, 2, 1]
    print("OK: リーグ内順位は同値同順位で次が飛ぶ")


def test_blend_renormalizes() -> None:
    """素材が欠けているリーグでは、残った重みの合計で割り直す。"""
    a = [1.0, -1.0]
    b = [1.0, -1.0]
    # 重みの合計が1でなくても、平均としての大きさは保たれる
    assert abs(blend([(a, 0.7, False), (b, 0.3, False)], 0) - 1.0) < 1e-9
    assert abs(blend([(a, 0.6, False)], 0) - 1.0) < 1e-9, "1つだけでも 1.0 になるはず"
    assert abs(blend([(a, 1.0, True)], 0) + 1.0) < 1e-9, "反転すると符号が変わる"
    assert blend([], 0) == 0.0
    print("OK: 素材が欠けても重みが割り直される")


def test_recent_points_and_hp_state() -> None:
    assert recent_points(["W", "W", "W", "W", "W"]) == (15, 15)
    assert recent_points(["L", "L", "L", "L", "L"]) == (0, 15)
    assert recent_points(["W", "D", "L"]) == (4, 9), "まだ5試合消化していない場合は満点も減る"
    assert recent_points([]) == (0, 0)
    assert hp_state(100) == "絶好調"
    assert hp_state(80) == "絶好調"
    assert hp_state(79) == "元気"
    assert hp_state(25) == "手負い"
    assert hp_state(24) == "瀕死"
    assert hp_state(1) == "瀕死"
    print("OK: 直近の戦績から勝点と状態が出る")


def test_squad_profile_needs_enough_players() -> None:
    few = {"players": [{"height": 180, "weight": 75, "birthday": "2000-01-01T00:00:00.000Z"}] * 9}
    p = squad_profile(few, date(2026, 9, 4))
    assert p["height"] is None and p["age"] is None, "9人では平均として信用しない"
    many = {"players": [{"height": 180, "weight": 75, "birthday": "2000-01-01T00:00:00.000Z"}] * 20}
    p2 = squad_profile(many, date(2026, 9, 4))
    assert p2["height"] == 180 and abs(p2["age"] - 26.7) < 0.2, p2
    assert squad_profile(None, date(2026, 9, 4))["height"] is None
    # 壊れた生年月日で例外にならない
    bad = {"players": [{"birthday": "2000-99-99T00:00:00.000Z"}] * 20}
    assert squad_profile(bad, date(2026, 9, 4))["age"] is None
    print("OK: 選手プロフィールは人数が足りないときNoneになる")


def _write_league(root: Path, league: str, official: dict[str, list]) -> None:
    """テスト用の最小データ一式を書く。official は指標キー -> 20クラブぶんの値。"""
    ids = [str(100 + i) for i in range(20)]
    teams, sim_teams, table, clubs = [], [], [], {}
    for i, idt in enumerate(ids):
        values = {
            "points": 30 - i, "played": 10,
            "attackRating": 1.5 - i * 0.05, "defenseRating": 0.5 + i * 0.05,
            "scorePg": 2.0 - i * 0.05, "cleanSheet": 5 - i // 4,
            "ballRate": 60 - i, "passCountPg": 600 - i * 10,
            "pointsOverX": 5 - i * 0.5,
        }
        for k, col in official.items():
            values[k] = col[i]
        teams.append({"idTeam": idt, "ja": f"クラブ{i}", "short": f"C{i}", "values": values})
        sim_teams.append({"idTeam": idt, "currentRank": i + 1, "currentPoints": 30 - i,
                          "expectedPoints": 60 - i, "expectedRank": i + 1.5})
        # 先頭クラブは5連勝、最後のクラブは5連敗にする
        recent = ["W"] * 5 if i == 0 else (["L"] * 5 if i == 19 else ["W", "D", "L", "W", "D"])
        table.append({"idTeam": idt, "rank": i + 1, "points": 30 - i, "played": 10,
                      "recent5": recent})
        clubs[idt] = {"players": [{"height": 180 + (i % 5), "weight": 72 + (i % 4),
                                   "birthday": f"{1995 + (i % 8)}-04-01T00:00:00.000Z"}] * 25}
    p = root / "data" / "processed"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{league}_stats.json").write_text(json.dumps(
        {"meta": {"season": "2026-2027", "basedOnMatches": 100, "clubCount": 20},
         "teams": teams}, ensure_ascii=False), encoding="utf-8")
    (p / f"{league}_simulation.json").write_text(json.dumps({"teams": sim_teams}), encoding="utf-8")
    (p / f"{league}_standings.json").write_text(json.dumps({"table": table}), encoding="utf-8")
    (p / "club_extra.json").write_text(json.dumps({"clubs": clubs}), encoding="utf-8")
    m = root / "data" / "masters"
    m.mkdir(parents=True, exist_ok=True)
    (m / f"{league}_teams.json").write_text(json.dumps(
        {"promotionRules": {"totalRounds": 38}}), encoding="utf-8")


def _with_temp_data(official: dict[str, list], fn) -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_league(root, "j2", official)
        orig_p, orig_m = mod.PROCESSED_DIR, dict(mod.MASTER_FILES)
        mod.PROCESSED_DIR = root / "data" / "processed"
        mod.MASTER_FILES = {"j2": root / "data" / "masters" / "j2_teams.json"}
        try:
            fn(mod.build_league("j2", date(2026, 9, 4)))
        finally:
            mod.PROCESSED_DIR, mod.MASTER_FILES = orig_p, orig_m


def test_end_to_end_j2_style() -> None:
    """空中戦・インターセプトがあるリーグ(J2/J3)。"""
    def check(data):
        assert data["meta"]["powerSource"] == "airBattle"
        assert data["meta"]["speedSource"] == "intercept"
        ts = data["teams"]
        assert len(ts) == 20
        assert ts[0]["level"] == 30, "Lvは勝点そのもの"
        assert ts[0]["hp"]["now"] == 100 and ts[0]["hp"]["state"] == "絶好調", "5連勝は満タン"
        assert ts[19]["hp"]["now"] == 1 and ts[19]["hp"]["state"] == "瀕死", "5連敗はHP1"
        assert ts[0]["mp"]["max"] == (38 - 10) * 3, "最大MPは残り試合×3"
        assert ts[0]["mp"]["now"] == 30, "期待勝点60 − 現在30"
        for t in ts:
            for k, p in t["params"].items():
                assert 1 <= p["value"] <= 255, (k, p)
                assert 1 <= p["rank"] <= 20
            assert t["title"] in mod.TITLES.values()
            assert "luck" not in t["topParams"], "称号にうんのよさは入らない"
        # 攻撃系の素材を単調に並べてあるので、先頭クラブのこうげきは1位になるはず
        assert ts[0]["params"]["attack"]["rank"] == 1
        ranks = sorted(t["params"]["attack"]["rank"] for t in ts)
        assert ranks[0] == 1 and len(set(ranks)) > 1
    _with_temp_data({"airBattleWinCountPg": [20 - i for i in range(20)],
                     "interceptCount": [30 - i for i in range(20)]}, check)
    print("OK: J2/J3(空中戦・インターセプトあり)で一式が組み上がる")


def test_end_to_end_j1_style() -> None:
    """走行距離・スプリントしか無いリーグ(J1)でも、意味を保ったまま作れる。"""
    def check(data):
        assert data["meta"]["powerSource"] == "physique", "空中戦が無いので体格から作る"
        assert data["meta"]["speedSource"] == "sprint"
        for t in data["teams"]:
            assert 1 <= t["params"]["power"]["value"] <= 255
            assert 1 <= t["params"]["speed"]["value"] <= 255
    _with_temp_data({"sprint": [130 - i for i in range(20)],
                     "distance": [118 - i * 0.2 for i in range(20)]}, check)
    print("OK: J1(走行距離・スプリントのみ)でも作れる")


def test_no_official_stats_at_all() -> None:
    """公式スタッツが1つも無くても、例外にせず作り切る(club_extra.jsonが無い場合など)。"""
    def check(data):
        assert data["meta"]["speedSource"] == "age", "最後の手段は平均年齢の若さ"
        assert all(1 <= t["params"]["speed"]["value"] <= 255 for t in data["teams"])
    _with_temp_data({}, check)
    print("OK: 公式スタッツが無くても落ちない")


def main() -> None:
    tests = [
        test_zscores,
        test_to_stat_clips,
        test_ranks_desc,
        test_blend_renormalizes,
        test_recent_points_and_hp_state,
        test_squad_profile_needs_enough_players,
        test_end_to_end_j2_style,
        test_end_to_end_j1_style,
        test_no_official_stats_at_all,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
