"""
scripts/rank_history.py のオフラインテスト。
python scripts/test_rank_history.py で実行する(pytest不使用、標準ライブラリのみ)。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rank_history  # noqa: E402
from standings import build_standings_table  # noqa: E402


def _team(id_team: str, ja: str) -> dict:
    return {"idTeam": id_team, "ja": ja, "short": ja[:2]}


def _match(id_event: str, round_no: int, home: str, away: str, hs=None, aws=None) -> dict:
    finished = hs is not None and aws is not None
    return {
        "idEvent": id_event,
        "round": round_no,
        "kickoffJst": f"2026-08-{(round_no % 28) + 1:02d}T14:00:00+09:00",
        "finished": finished,
        "home": {"idTeam": home, "score": hs},
        "away": {"idTeam": away, "score": aws},
    }


TEAMS = [_team("100", "湘南"), _team("200", "千葉"), _team("300", "町田")]

# 3節ぶん、毎節100が勝ち続けるシナリオ(順位が動くことを確認できる展開にする)。
MATCHES = [
    _match("r1a", 1, "100", "200", 2, 0),  # 100 win
    _match("r1b", 1, "300", "200", 1, 1),  # 引き分け相手違い(300は無関係の一戦にしたいので後で使わない)
    _match("r2a", 2, "300", "100", 0, 1),  # 100 win
    _match("r2b", 2, "200", "300", 1, 1),
    _match("r3a", 3, "200", "300", 0, 2),  # 300 win
    _match("r3b", 3, "100", "300", 1, 1),  # 100 draw
]


def _patch_data(monkeypatch_matches, monkeypatch_teams):
    rank_history.load_matches = monkeypatch_matches
    rank_history.load_master_teams = monkeypatch_teams


def test_compute_rank_history_no_finished_matches_returns_empty():
    _patch_data(lambda league: [_match("x", 1, "100", "200")], lambda league: TEAMS)
    result = rank_history.compute_rank_history("j2")
    assert result["rounds"] == []
    assert result["ranks"] == {}
    assert result["meta"]["maxRound"] == 0


def test_compute_rank_history_tracks_progression_across_rounds():
    _patch_data(lambda league: MATCHES, lambda league: TEAMS)
    result = rank_history.compute_rank_history("j2")
    assert result["rounds"] == [1, 2, 3]
    assert set(result["ranks"].keys()) == {"100", "200", "300"}
    # 全チームぶん、節数と同じ長さの配列になっていること
    for tid, arr in result["ranks"].items():
        assert len(arr) == 3, f"{tid}: {arr}"
    # 各節、1位〜3位が過不足なく登場すること(3チームしかいないので同着以外は1..3が揃うはず)
    for i in range(3):
        ranks_at_round = sorted(result["ranks"][tid][i] for tid in result["ranks"])
        assert ranks_at_round[0] == 1, f"round {i+1}: {ranks_at_round}"


def test_compute_rank_history_last_round_matches_build_standings_table():
    """最終節時点の順位は、全試合をそのままbuild_standings_table()に渡した結果と一致すること
    (順位決定ロジックを二重実装していないことの確認)。"""
    _patch_data(lambda league: MATCHES, lambda league: TEAMS)
    result = rank_history.compute_rank_history("j2")
    expected_table, _aux = build_standings_table(MATCHES, TEAMS)
    expected_rank = {row["idTeam"]: row["rank"] for row in expected_table}

    last_idx = len(result["rounds"]) - 1
    for tid in expected_rank:
        assert result["ranks"][tid][last_idx] == expected_rank[tid], tid


def test_compute_rank_history_unplayed_team_still_listed():
    """一度も試合をしていないクラブ(延期などで消化0)も、順位はNoneでも配列には含まれること。"""
    teams = TEAMS + [_team("400", "控えクラブ")]
    _patch_data(lambda league: MATCHES, lambda league: teams)
    result = rank_history.compute_rank_history("j2")
    assert "400" in result["ranks"]
    assert len(result["ranks"]["400"]) == 3


def test_write_league_output_missing_matches_file_skips_without_raising():
    with tempfile.TemporaryDirectory() as tmp:
        original_processed = rank_history.PROCESSED_DIR
        original_history = rank_history.HISTORY_DIR
        try:
            rank_history.PROCESSED_DIR = Path(tmp) / "processed"  # 存在しないディレクトリ
            rank_history.HISTORY_DIR = Path(tmp) / "history"
            rank_history.write_league_output("j2")  # 例外を投げずに終わること
            assert not (rank_history.HISTORY_DIR / "j2_rank_history.json").exists()
        finally:
            rank_history.PROCESSED_DIR = original_processed
            rank_history.HISTORY_DIR = original_history


def main() -> None:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"OK   {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"ERROR {name}: {type(e).__name__}: {e}")

    print()
    if failed:
        print(f"{len(failed)}/{len(tests)}件失敗: {failed}")
        sys.exit(1)
    print(f"全{len(tests)}件OK")


if __name__ == "__main__":
    main()
