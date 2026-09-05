"""クラブの戦力を、昔のRPGのステータス風にパラメータ化する(第40弾)。

なぜバッチで作るか:
    素材が data/processed/*_stats.json、*_simulation.json、*_standings.json、
    それに club_extra.json(1.3MB、選手の身長・体重・生年月日)にまたがっている。
    フロントで組み立てると、スタッツタブを開くだけで club_extra.json を落とすことになる。
    出来上がりは20クラブ×数値なので、ここで作って小さなJSONにする。

出力: data/processed/{league}_rpg.json

パラメータの作り方:
    素材をリーグ内で標準化(zスコア)し、重みを付けて合成してから 1〜255 に写す。
    平均が128、標準偏差1つぶんが40。ファミコンの見た目に寄せた数字の幅にしてある。

    こうげき = 攻撃力(モデル推定) 0.7 + 1試合平均得点数 0.3
    ぼうぎょ = 守備力(モデル推定、低いほど良いので反転) 0.7 + 無失点試合 0.3
    まほう   = 平均ボール保持率 0.5 + 1試合平均パス数 0.5
    ちから   = 空中戦勝利 0.5 + 平均身長 0.3 + 平均体重 0.2
    すばやさ = インターセプト 0.7 + 平均年齢の若さ 0.3
    うんのよさ = 勝点の上振れ(実際の勝点 − 期待勝点)

    ただし公式スタッツの提供指標がリーグで違う:
        J1     … 走行距離・スプリント回数がある。空中戦・インターセプトが無い
        J2/J3  … 空中戦・インターセプトがある。走行距離・スプリントが無い
    そこで
        ちから   … J1は平均身長・体重だけで作る
        すばやさ … J1はスプリント回数を使う(インターセプトの代わり)
    どちらも「速さ・鋭さ」「当たりの強さ」という意味は保たれるが、
    リーグをまたいだ数値の比較はできない(リーグ内の相対値としてだけ意味を持つ)。

Lv / HP / MP:
    Lv = 現在の勝点。そのまま。
    HP = 直近5試合の勝点を100点満点に写したもの。5連敗なら1(瀕死)。
    MP = 残りの力。最大はそのクラブの残り試合×3(全部勝ったときの勝点)、
         現在値はモデルが「このあと取る」と見ている勝点(期待勝点 − 現在の勝点)。
         シーズンが進むほど最大MPが減っていく。

実行方法:
    python scripts/build_rpg.py
    python scripts/build_rpg.py --league j2 --dry-run
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

PROCESSED_DIR = BASE_DIR / "data" / "processed"
CONFIG_DIR = BASE_DIR / "data" / "config"
MASTERS_DIR = BASE_DIR / "data" / "masters"
MASTER_FILES = {
    "j1": MASTERS_DIR / "j1_teams_2026-27.json",
    "j2": MASTERS_DIR / "j2_master_2026-27.json",
    "j3": MASTERS_DIR / "j3_teams_2026-27.json",
}
LEAGUES = ("j1", "j2", "j3")

# 表示の幅。平均128、標準偏差1つぶんが40、1〜255でクリップ
CENTER, SPREAD, VMIN, VMAX = 128, 40, 1, 255
HP_MAX = 100

PARAM_ORDER = ["attack", "defense", "magic", "power", "speed", "luck"]
PARAM_LABELS = {
    "attack": "こうげき", "defense": "ぼうぎょ", "magic": "まほう",
    "power": "ちから", "speed": "すばやさ", "luck": "うんのよさ",
}

# 称号を決める5つ。うんのよさは外してある。
# 外す理由: pointsOverX(勝点の上振れ)は勝っているクラブほど自動的に高く出るので、
# 上位2つに入れると序盤は「こうげき+うん」ばかりになって称号が潰れる(実測で20クラブ中4つが
# 同じ称号になった)。うんのよさは職業ではなく性格なので、パラメータとしては残して
# 称号の判定からだけ外す。
TITLE_PARAMS = ["attack", "defense", "magic", "power", "speed"]

# 上位2つの組み合わせで決まる称号。順序は問わない(frozenset で引く)
TITLES = {
    frozenset(("attack", "defense")): "ゆうしゃ",
    frozenset(("attack", "magic")): "まけんし",
    frozenset(("attack", "power")): "せんし",
    frozenset(("attack", "speed")): "はやてのつるぎ",
    frozenset(("defense", "magic")): "けんじゃ",
    frozenset(("defense", "power")): "せいきし",
    frozenset(("defense", "speed")): "まもりびと",
    frozenset(("magic", "power")): "まどうし",
    frozenset(("magic", "speed")): "まほうつかい",
    frozenset(("power", "speed")): "ぶとうか",
}

# HPの状態。境界値そのものを含む方の並び順で上から判定する。
# 画面はひらがなと数字だけで組む(昔のゲームの見た目に寄せる)ので、ここもひらがなにしてある。
HP_STATES = [(80, "げんき"), (50, "ふつう"), (25, "ておい"), (0, "ひんし")]


def hp_state(hp: int) -> str:
    for threshold, label in HP_STATES:
        if hp >= threshold:
            return label
    return HP_STATES[-1][1]


def zscores(values: list[float | None]) -> list[float]:
    """None を 0(平均)として扱う z スコア。全部同じ値なら全員0を返す。"""
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [0.0] * len(values)
    mu = statistics.mean(present)
    sd = statistics.pstdev(present)
    if not sd:
        return [0.0] * len(values)
    return [0.0 if v is None else (v - mu) / sd for v in values]


def blend(parts: list[tuple[list[float], float, bool]], i: int) -> float:
    """(zスコアの列, 重み, 反転するか) の組を合成する。

    素材が欠けているリーグでは呼び出し側が組そのものを渡さないので、
    ここでは残った重みの合計で割り直す(重みの合計が1でなくてもよい)。
    """
    total = sum(w for _, w, _ in parts)
    if not total:
        return 0.0
    acc = 0.0
    for zs, w, invert in parts:
        acc += (-zs[i] if invert else zs[i]) * w
    return acc / total


def to_stat(z: float) -> int:
    return max(VMIN, min(VMAX, round(CENTER + z * SPREAD)))


def ranks_desc(values: list[int]) -> list[int]:
    """大きいほど1位。同値は同順位、次は飛ぶ(1,2,2,4)。順位表の流儀に合わせる。"""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    out = [0] * len(values)
    prev_val = None
    prev_rank = 0
    for n, i in enumerate(order, start=1):
        if values[i] == prev_val:
            out[i] = prev_rank
        else:
            out[i] = n
            prev_val, prev_rank = values[i], n
    return out


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] {path.name} を読めなかった: {e}", file=sys.stderr)
        return None


def squad_profile(club: dict | None, today: date) -> dict:
    """選手一覧から平均身長・体重・年齢を出す。値の無い選手は黙って飛ばす。"""
    players = (club or {}).get("players") or []
    heights = [p["height"] for p in players if p.get("height")]
    weights = [p["weight"] for p in players if p.get("weight")]
    ages = []
    for p in players:
        b = str(p.get("birthday") or "")
        if len(b) >= 10:
            try:
                born = date(int(b[0:4]), int(b[5:7]), int(b[8:10]))
            except ValueError:
                continue
            ages.append((today - born).days / 365.25)
    # 数人しか取れていないときは平均として信用しない
    return {
        "height": statistics.mean(heights) if len(heights) >= 10 else None,
        "weight": statistics.mean(weights) if len(weights) >= 10 else None,
        "age": statistics.mean(ages) if len(ages) >= 10 else None,
        "squadSize": len(players),
    }


def recent_points(recent5: list[str]) -> tuple[int, int]:
    """直近の戦績から (取った勝点, 満点) を返す。"""
    pts = 0
    for r in recent5 or []:
        if r == "W":
            pts += 3
        elif r == "D":
            pts += 1
    return pts, len(recent5 or []) * 3


def build_league(league: str, today: date) -> dict | None:
    stats = load_json(PROCESSED_DIR / f"{league}_stats.json")
    if not stats or not stats.get("teams"):
        print(f"[warn] {league}: stats が無いのでスキップ", file=sys.stderr)
        return None
    sim = load_json(PROCESSED_DIR / f"{league}_simulation.json") or {}
    # 画面はひらがなと数字で組むので、クラブ名も読みで出す(data/config/club_kana.json)
    kana = (load_json(CONFIG_DIR / "club_kana.json") or {}).get("kana") or {}
    standings = load_json(PROCESSED_DIR / f"{league}_standings.json") or {}
    extra = (load_json(PROCESSED_DIR / "club_extra.json") or {}).get("clubs") or {}
    master = load_json(MASTER_FILES[league]) or {}
    rules = master.get("promotionRules") or {}
    total_rounds = rules.get("totalRounds")

    sim_by_id = {t["idTeam"]: t for t in (sim.get("teams") or [])}
    st_by_id = {r["idTeam"]: r for r in (standings.get("table") or [])}

    teams = stats["teams"]
    vals = [t.get("values") or {} for t in teams]
    squads = [squad_profile(extra.get(t["idTeam"]), today) for t in teams]

    def col(key):
        return [v.get(key) for v in vals]

    def scol(key):
        return [s[key] for s in squads]

    z_atk = zscores(col("attackRating"))
    z_def = zscores(col("defenseRating"))
    z_score = zscores(col("scorePg"))
    z_cs = zscores(col("cleanSheet"))
    z_ball = zscores(col("ballRate"))
    z_pass = zscores(col("passCountPg"))
    z_air = zscores(col("airBattleWinCountPg"))
    z_itc = zscores(col("interceptCount"))
    z_spr = zscores(col("sprint"))
    z_h = zscores(scol("height"))
    z_w = zscores(scol("weight"))
    z_age = zscores(scol("age"))
    z_overx = zscores(col("pointsOverX"))

    # 公式スタッツの提供指標はリーグで違う。実際に値がある方を使う
    has_air = any(v.get("airBattleWinCountPg") is not None for v in vals)
    has_itc = any(v.get("interceptCount") is not None for v in vals)
    has_spr = any(v.get("sprint") is not None for v in vals)
    power_source = "airBattle" if has_air else "physique"
    speed_source = "intercept" if has_itc else ("sprint" if has_spr else "age")

    power_parts = ([(z_air, 0.5, False)] if has_air else []) + \
                  [(z_h, 0.3 if has_air else 0.6, False), (z_w, 0.2 if has_air else 0.4, False)]
    if has_itc:
        speed_parts = [(z_itc, 0.7, False), (z_age, 0.3, True)]
    elif has_spr:
        speed_parts = [(z_spr, 0.7, False), (z_age, 0.3, True)]
    else:
        speed_parts = [(z_age, 1.0, True)]

    raw = {k: [] for k in PARAM_ORDER}
    for i in range(len(teams)):
        raw["attack"].append(to_stat(blend([(z_atk, 0.7, False), (z_score, 0.3, False)], i)))
        # defenseRating は低いほど良いので反転する
        raw["defense"].append(to_stat(blend([(z_def, 0.7, True), (z_cs, 0.3, False)], i)))
        raw["magic"].append(to_stat(blend([(z_ball, 0.5, False), (z_pass, 0.5, False)], i)))
        raw["power"].append(to_stat(blend(power_parts, i)))
        raw["speed"].append(to_stat(blend(speed_parts, i)))
        raw["luck"].append(to_stat(blend([(z_overx, 1.0, False)], i)))
    rank_of = {k: ranks_desc(raw[k]) for k in PARAM_ORDER}

    out_teams = []
    for i, t in enumerate(teams):
        idt = t["idTeam"]
        v = vals[i]
        s = sim_by_id.get(idt) or {}
        row = st_by_id.get(idt) or {}
        played = v.get("played") or row.get("played") or 0
        points = v.get("points")
        if points is None:
            points = row.get("points") or 0

        got, full = recent_points(row.get("recent5") or [])
        hp_now = HP_MAX if not full else max(1, round(got / full * HP_MAX))

        remaining = max(0, (total_rounds - played)) if total_rounds else 0
        mp_max = remaining * 3
        exp_points = s.get("expectedPoints")
        mp_now = max(0, min(mp_max, round(exp_points - points))) if exp_points is not None else 0

        params = {k: {"value": raw[k][i], "rank": rank_of[k][i], "label": PARAM_LABELS[k]}
                  for k in PARAM_ORDER}
        # 同値のときは PARAM_ORDER の並び順で決める(実行のたびに称号が変わらないように)
        top2 = sorted(TITLE_PARAMS, key=lambda k: (-raw[k][i], TITLE_PARAMS.index(k)))[:2]

        out_teams.append({
            "idTeam": idt, "ja": t.get("ja"), "short": t.get("short"),
            # 読みが無いクラブは略称のまま出す(画面が空になるよりは漢字のほうがまし)
            "kana": kana.get(idt) or t.get("short"),
            "level": points,
            "hp": {"now": hp_now, "max": HP_MAX, "recent": row.get("recent5") or [],
                   "state": hp_state(hp_now)},
            "mp": {"now": mp_now, "max": mp_max,
                   "expectedPoints": exp_points,
                   "currentRank": s.get("currentRank") or row.get("rank"),
                   "expectedRank": s.get("expectedRank")},
            "params": params,
            "title": TITLES.get(frozenset(top2), "ぼうけんしゃ"),
            "topParams": top2,
        })

    return {
        "meta": {
            "league": league,
            "season": (stats.get("meta") or {}).get("season"),
            "generatedAtJst": datetime.now(JST).isoformat(timespec="seconds"),
            "basedOnMatches": (stats.get("meta") or {}).get("basedOnMatches"),
            "clubCount": len(teams),
            "powerSource": power_source,
            "speedSource": speed_source,
            "scale": {"center": CENTER, "spread": SPREAD, "min": VMIN, "max": VMAX},
            "note": ("各パラメータはリーグ内で標準化した相対値です(平均128)。"
                     "公式スタッツの提供指標がリーグごとに違うため、リーグをまたいだ比較はできません。"),
        },
        "params": [{"key": k, "label": PARAM_LABELS[k]} for k in PARAM_ORDER],
        "teams": out_teams,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=LEAGUES)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = datetime.now(JST).date()
    targets = [args.league] if args.league else list(LEAGUES)
    wrote = 0
    for lg in targets:
        data = build_league(lg, today)
        if not data:
            continue
        path = PROCESSED_DIR / f"{lg}_rpg.json"
        print(f"[info] {lg}: {len(data['teams'])}クラブ "
              f"(ちから={data['meta']['powerSource']} すばやさ={data['meta']['speedSource']})")
        if args.dry_run:
            continue
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        wrote += 1
    if args.dry_run:
        print("[info] --dry-run なので書き込まない")
    else:
        print(f"[info] {wrote}ファイルを更新した")


if __name__ == "__main__":
    main()
