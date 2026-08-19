"""
得点をポアソン分布とみなす簡易モデル。simulate.py(モンテカルロ)とstats.py(期待勝点等)の
両方から使う共通部分をここに切り出す。二重実装によるロジックのズレを避けるため。

モデルの限界(必ず理解した上で使うこと):
  得点は独立ポアソン分布と仮定している。実際のサッカーでは0-0や1-1のような
  低スコアの引き分けは独立仮定より起きやすい(Dixon-Coles補正なし)。
  ここではスコアだけしか無いデータでできる範囲の近似にとどめている。
"""

from __future__ import annotations

import math
import random

from standings import TeamRecord

SHRINK_K = 6.0                  # 縮約の強さ。消化6試合で「自チーム実績:リーグ平均 = 1:1」
DEFAULT_LEAGUE_AVG_GOALS = 1.35  # 消化0試合のときのフォールバック(Jリーグの目安値)
HFA_DEFAULT = 1.20              # 消化10試合未満のときのホームアドバンテージ既定値
HFA_MIN, HFA_MAX = 1.0, 1.5
LAMBDA_MIN, LAMBDA_MAX = 0.05, 5.0


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def poisson(lam: float, rnd: random.Random) -> int:
    """Knuthの方法によるポアソン乱数(標準ライブラリのみ、numpy不使用)。モンテカルロ用。"""
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rnd.random()
        if p <= L:
            return k
        k += 1


def poisson_pmf(k: int, lam: float) -> float:
    """ポアソン分布の確率質量関数(解析的な値。期待勝点の計算に使う)。"""
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def compute_league_stats(finished_matches: list[dict]) -> tuple[float, float]:
    """(league_avg_goals, home_field_advantage) を計算する。"""
    n = len(finished_matches)
    if n == 0:
        return DEFAULT_LEAGUE_AVG_GOALS, HFA_DEFAULT

    total_goals = sum(m["home"]["score"] + m["away"]["score"] for m in finished_matches)
    league_avg_goals = total_goals / (n * 2)

    if n >= 10:
        home_avg = sum(m["home"]["score"] for m in finished_matches) / n
        away_avg = sum(m["away"]["score"] for m in finished_matches) / n
        hfa = (home_avg / away_avg) if away_avg > 0 else HFA_DEFAULT
        hfa = clamp(hfa, HFA_MIN, HFA_MAX)
    else:
        hfa = HFA_DEFAULT

    return league_avg_goals, hfa


def compute_ratings(records: dict[str, TeamRecord], league_avg_goals: float) -> dict[str, tuple[float, float]]:
    """
    クラブごとの(攻撃力, 守備力)を返す。1.0が平均。
    リーグ平均への縮約(shrinkage)込み。n_i==0のクラブは(1.0, 1.0)。
    """
    ratings: dict[str, tuple[float, float]] = {}
    for tid, rec in records.items():
        n = rec.played
        if n == 0:
            ratings[tid] = (1.0, 1.0)
            continue
        w = n / (n + SHRINK_K)
        atk = w * (rec.gf / n) / league_avg_goals + (1 - w) * 1.0
        de = w * (rec.ga / n) / league_avg_goals + (1 - w) * 1.0
        ratings[tid] = (atk, de)
    return ratings


def expected_goals(
    atk_home: float, def_away: float, atk_away: float, def_home: float,
    league_avg_goals: float, hfa: float,
) -> tuple[float, float]:
    lam_home = league_avg_goals * atk_home * def_away * hfa
    lam_away = league_avg_goals * atk_away * def_home / hfa
    return clamp(lam_home, LAMBDA_MIN, LAMBDA_MAX), clamp(lam_away, LAMBDA_MIN, LAMBDA_MAX)


def seed_all_teams(records: dict[str, TeamRecord], master_teams: list[dict]) -> dict[str, TeamRecord]:
    """マスタの全クラブがrecordsに(0試合でも)存在することを保証する。"""
    for t in master_teams:
        records.setdefault(t["idTeam"], TeamRecord(idTeam=t["idTeam"]))
    return records


def match_outcome_probs(lam_home: float, lam_away: float, max_goals: int = 10) -> tuple[float, float, float]:
    """
    独立ポアソンの仮定のもとで、(ホーム勝ち, 引き分け, アウェイ勝ち)の確率を解析的に計算する。
    サンプリングではなく、0..max_goals の得点グリッドを総当たりして厳密に積み上げる
    (期待勝点の計算はモンテカルロにする必要が無く、こちらのほうが安定する)。
    """
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    home_pmf = [poisson_pmf(k, lam_home) for k in range(max_goals + 1)]
    away_pmf = [poisson_pmf(k, lam_away) for k in range(max_goals + 1)]
    for hs in range(max_goals + 1):
        for as_ in range(max_goals + 1):
            p = home_pmf[hs] * away_pmf[as_]
            if hs > as_:
                p_home += p
            elif hs == as_:
                p_draw += p
            else:
                p_away += p
    # max_goalsで打ち切った分の残余確率は、得点差なし(引き分け方向)に寄せず、
    # そのまま無視する(残余は現実的にはごく僅か。lambda<=5・max_goals=10で十分小さい)。
    total = p_home + p_draw + p_away
    if total > 0:
        p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total
    return p_home, p_draw, p_away
