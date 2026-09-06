"""
data/history/toto/*.json(指定試合表)に予想を当てて、次の2つを作る。

  data/processed/toto.json          … 販売中の回の予想(アプリが表示する)
  data/processed/toto_backtest.json … 「もし買っていたら」の遡及集計

遡及集計で最も大事なのは、購入時点で分かっていた情報しか使わないこと。
販売締切はその回の最初の試合のキックオフ前なので、レーティングは
「その回の最も早い開催日より前に終わった試合」だけで組み直している。
全試合消化後のレーティングで過去を予想すると的中率が不当に高く出る。

当せん金は扱っていない。金額は回ごとの売上と当せん口数で決まり、
その実績値はこのアプリのデータには無いため。出しているのは的中数だけ。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from toto_model import (  # noqa: E402
    load_context,
    outcome_of,
    predict,
    ratings_as_of,
    resolve_match,
)

BASE_DIR = Path(__file__).resolve().parent.parent
HIST_DIR = BASE_DIR / "data" / "history" / "toto"
PROCESSED = BASE_DIR / "data" / "processed"
JST = timezone(timedelta(hours=9))

KINDS = ("toto", "minitotoA", "minitotoB", "totoGOAL3")
LIVE_KEEP = 4          # アプリに載せる販売中の回の上限
PAST_KEEP = 8          # 答え合わせ用に残す終了済みの回の数
UI_KINDS = ("toto", "minitotoA", "minitotoB")   # 1/0/2を選ぶくじ。GOAL3はスコア予想なので別扱い
CAL_BINS = [(0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01)]


def assign_years(parsed_list: list[dict], today) -> dict[int, int]:
    """各回の開催年を決める。指定試合表のPDFには年が書かれていないため。

    回号は時系列に並ぶので、回号の降順に見ていって月日が「増えた」ら年をまたいでいる
    (例: 1607回=2/6 の次に古い 1606回=1/31 は同じ年、さらに古い 1604回=1/24 も同じ年。
     逆に 1600回=12/20 のように月日が増えたら、そこから前年)。
    起点となる最新回の年は、その月日が今日から半年以内に収まる年として決める。
    """
    order = sorted(parsed_list, key=lambda p: -p["kai"])
    if not order:
        return {}
    def first_md(p):
        return min((int(m["date"].split("/")[0]), int(m["date"].split("/")[1])) for m in p["matches"])
    mm, dd = first_md(order[0])
    year = today.year
    for cand in (today.year - 1, today.year, today.year + 1):
        try:
            if abs((datetime(cand, mm, dd).date() - today).days) < 183:
                year = cand
                break
        except ValueError:      # 2/29
            continue
    out, prev = {}, None
    for p in order:
        md = first_md(p)
        if prev is not None and md > prev:
            year -= 1
        out[p["kai"]] = year
        prev = md
    return out


def _mark_key(v) -> int:
    return v if isinstance(v, int) else v[0]


def picks_of(rnd: dict, kind: str) -> list[dict]:
    sel = [m for m in rnd["matches"] if kind in m["marks"]]
    sel.sort(key=lambda m: _mark_key(m["marks"][kind]))
    return sel


def evaluate_round(ctx: dict, parsed: dict, cache: dict, year: int) -> dict | None:
    resolved = []
    for m in parsed["matches"]:
        mm, _, dd = m["date"].partition("/")
        iso = "%04d-%02d-%02d" % (year, int(mm), int(dd))
        resolved.append((m, *resolve_match(ctx, iso, m["home"], m["away"])))
    dates = [mt["kickoffDate"] for _, lg, mt in resolved if mt]
    if not dates:
        return None
    cutoff = min(dates)

    rows = []
    for m, lg, mt in resolved:
        row: dict = {"no": m["no"], "marks": m["marks"],
                     "homeRaw": m["home"], "awayRaw": m["away"], "dateRaw": m["date"]}
        if not mt:
            row["status"] = "outofscope"   # 天皇杯・ルヴァン等、リーグ戦以外
            rows.append(row)
            continue
        key = (lg, cutoff)
        if key not in cache:
            cache[key] = ratings_as_of(ctx, lg, cutoff)
        rt = cache[key]
        p = predict(mt, rt) if rt else None
        # 参考予想が出せない(開幕直後で消化数が足りない)場合でも、試合そのものと
        # 結果は入れる。自分で予想して答え合わせをするには結果さえあればよいので、
        # ここで試合ごと落とすとその回が丸ごと使えなくなる。
        row.update({
            "status": "ok" if p else "nohint",
            "league": lg,
            "date": mt["kickoffDate"],
            "kickoffJst": mt.get("kickoffJst"),
            "round": mt.get("round"),
            "home": {k: mt["home"].get(k) for k in ("idTeam", "ja", "short")},
            "away": {k: mt["away"].get(k) for k in ("idTeam", "ja", "short")},
        })
        if p:
            row.update(p)
        act = outcome_of(mt)
        if act:
            row["actual"] = act
            row["score"] = [mt["home"].get("score"), mt["away"].get("score")]
        rows.append(row)

    pending = any(r["status"] in ("ok", "nohint") and "actual" not in r for r in rows)
    return {"kai": parsed["kai"], "cutoff": cutoff, "note": parsed.get("note", ""),
            "matches": rows, "pending": pending}


def summarize(rounds: list[dict], kind: str) -> dict | None:
    hist: dict[int, int] = {}
    cal_hits = [[0, 0.0, 0] for _ in CAL_BINS]   # [n, 予想確率の合計, 的中数]
    detail = []
    exp_total = act_total = home_total = n_total = 0.0
    for r in rounds:
        sel = picks_of(r, kind)
        if not sel:
            continue
        if any(m["status"] != "ok" or "actual" not in m for m in sel):
            continue   # 1試合でも予想不能/結果不明ならその回は集計しない
        hits = sum(1 for m in sel if m["pick"] == m["actual"])
        exp = sum(m["confidence"] for m in sel)
        home = sum(1 for m in sel if m["actual"] == "1")
        hist[hits] = hist.get(hits, 0) + 1
        for m in sel:
            for i, (lo, hi) in enumerate(CAL_BINS):
                if lo <= m["confidence"] < hi:
                    cal_hits[i][0] += 1
                    cal_hits[i][1] += m["confidence"]
                    cal_hits[i][2] += 1 if m["pick"] == m["actual"] else 0
                    break
        detail.append({"kai": r["kai"], "date": r["cutoff"], "hits": hits,
                       "n": len(sel), "expected": round(exp, 2), "homeOnly": home})
        exp_total += exp
        act_total += hits
        home_total += home
        n_total += len(sel)
    if not detail:
        return None
    size = round(n_total / len(detail))
    return {
        "kind": kind,
        "size": size,
        "rounds": len(detail),
        "hitHistogram": {str(k): hist[k] for k in sorted(hist)},
        "meanHits": round(act_total / len(detail), 2),
        "expectedHits": round(exp_total / len(detail), 2),      # モデル自身の見込み
        "randomHits": round(size / 3, 2),                       # でたらめに1/0/2を選んだ場合
        "homeOnlyHits": round(home_total / len(detail), 2),     # 全部ホーム(1)で買った場合
        "calibration": [
            {"bin": f"{int(lo*100)}-{int(hi*100) if hi <= 1 else 100}%",
             "n": c[0], "predicted": round(c[1] / c[0], 4), "actual": round(c[2] / c[0], 4)}
            for (lo, hi), c in zip(CAL_BINS, cal_hits) if c[0]
        ],
        "rounds_detail": detail,
    }


def pack_round(rnd: dict, status: str) -> dict:
    """アプリが読む形にまとめる。試合はフラットに1回だけ持ち、
    くじ種別はそのインデックスの並びで持つ(同じ試合が種別をまたいで重複するため)。"""
    keep = []
    index_of = {}
    kinds: dict[str, list[int]] = {}
    for kind in UI_KINDS:
        sel = picks_of(rnd, kind)
        if not sel:
            continue
        idxs = []
        for m in sel:
            if m["no"] not in index_of:
                index_of[m["no"]] = len(keep)
                keep.append(m)
            idxs.append(index_of[m["no"]])
        kinds[kind] = idxs
    if not kinds:
        return {"kai": rnd["kai"], "status": status, "cutoff": rnd["cutoff"], "kinds": {}, "matches": []}

    matches = []
    for m in keep:
        row = {"no": m["no"], "status": m["status"]}
        if m["status"] not in ("ok", "nohint"):
            row["home"] = m["homeRaw"]
            row["away"] = m["awayRaw"]
            row["date"] = m["dateRaw"]
            matches.append(row)
            continue
        row.update({
            "date": m["date"],
            "league": m["league"],
            "home": m["home"]["short"] or m["home"]["ja"],
            "away": m["away"]["short"] or m["away"]["ja"],
            "homeId": m["home"]["idTeam"],
            "awayId": m["away"]["idTeam"],
        })
        if "probs" in m:
            row["probs"] = m["probs"]
            row["hint"] = m["pick"]
        if "actual" in m:
            row["actual"] = m["actual"]
            row["score"] = m["score"]
        matches.append(row)

    resolved = sum(1 for m in matches if m.get("actual"))
    # 締切の目安。実際の発売締切は最初のキックオフより前(例: 12:00締切で13:00開始)なので、
    # ここで出すのは「これ以降に記入したら後出し」という下限。アプリ側はこれを過ぎたら
    # 記入を止める。正確な販売期間はPDFの本文にあるが、時刻の書き方が
    # 「お昼１２時」「朝８時」など揺れるのでパースしていない。
    kickoffs = [m.get("kickoffJst") for m in keep if m.get("kickoffJst")]
    return {
        "kai": rnd["kai"],
        "status": status,
        "cutoff": rnd["cutoff"],
        "deadline": min(kickoffs) if kickoffs else None,
        "dates": sorted({m["date"] for m in matches if m.get("league")}),
        "settled": resolved,
        "total": len(matches),
        "kinds": kinds,
        "matches": matches,
    }


def league_backtest(ctx: dict) -> dict:
    """今季の消化済みリーグ戦すべてを「その日より前のデータだけ」で予想し直して当たり具合を測る。

    totoの指定試合は今季まだ数回分しか無い。モデルそのものの精度と較正は
    こちらのほうがサンプルが多く、信頼できる。
    予想に使うのは kickoffDate より前に終わった試合だけなので、
    「結果を見てから予想する」ことにはなっていない。
    """
    from toto_model import LEAGUES
    rows = []
    for lg in LEAGUES:
        finished = [m for m in ctx[lg]["matches"] if m.get("finished")]
        for d in sorted({m["kickoffDate"] for m in finished}):
            rt = ratings_as_of(ctx, lg, d)
            if not rt:
                continue
            for m in finished:
                if m["kickoffDate"] != d:
                    continue
                p = predict(m, rt)
                act = outcome_of(m)
                if p and act:
                    rows.append((lg, p, act))
    if not rows:
        return {}

    n = len(rows)
    hit = sum(1 for _, p, a in rows if p["pick"] == a)
    home_hit = sum(1 for _, _, a in rows if a == "1")
    draw_actual = sum(1 for _, _, a in rows if a == "0")
    draw_picked = sum(1 for _, p, _ in rows if p["pick"] == "0")
    draw_pred = sum(p["probs"]["0"] for _, p, _ in rows)

    cal = [[0, 0.0, 0] for _ in CAL_BINS]
    for _, p, a in rows:
        for i, (lo, hi) in enumerate(CAL_BINS):
            if lo <= p["confidence"] < hi:
                cal[i][0] += 1
                cal[i][1] += p["confidence"]
                cal[i][2] += 1 if p["pick"] == a else 0
                break

    by_league = {}
    for lg in LEAGUES:
        sub = [(p, a) for l, p, a in rows if l == lg]
        if sub:
            by_league[lg] = {"n": len(sub),
                             "hitRate": round(sum(1 for p, a in sub if p["pick"] == a) / len(sub), 4)}

    return {
        "matches": n,
        "hitRate": round(hit / n, 4),
        "expectedHitRate": round(sum(p["confidence"] for _, p, _ in rows) / n, 4),
        "randomHitRate": round(1 / 3, 4),
        "homeOnlyHitRate": round(home_hit / n, 4),
        "drawActualRate": round(draw_actual / n, 4),      # 実際の引き分け率
        "drawPredictedRate": round(draw_pred / n, 4),     # モデルが見込んだ引き分け率
        "drawPickedRate": round(draw_picked / n, 4),      # 引き分けを本命にした割合
        "calibration": [
            {"bin": f"{int(lo*100)}-{int(hi*100) if hi <= 1 else 100}%", "n": c[0],
             "predicted": round(c[1] / c[0], 4), "actual": round(c[2] / c[0], 4)}
            for (lo, hi), c in zip(CAL_BINS, cal) if c[0]
        ],
        "byLeague": by_league,
    }


def main() -> int:
    ctx = load_context()
    cache: dict = {}
    files = sorted(HIST_DIR.glob("*.json"), key=lambda p: int(p.stem))
    if not files:
        print("data/history/toto/*.json がありません。先に fetch_toto.py を実行してください。")
        return 1

    all_parsed = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    years = assign_years(all_parsed, datetime.now(JST).date())

    live, past, skipped = [], [], 0
    for parsed in all_parsed:
        res = evaluate_round(ctx, parsed, cache, years[parsed["kai"]])
        if res is None:
            skipped += 1
            continue
        (live if res["pending"] else past).append(res)

    live.sort(key=lambda r: r["kai"])
    past.sort(key=lambda r: r["kai"])
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    rounds = [pack_round(r, "live") for r in live] + \
             [pack_round(r, "done") for r in past[-PAST_KEEP:]]
    rounds.sort(key=lambda r: -r["kai"])
    out_live = {
        "meta": {
            "generatedAtJst": now,
            "source": "日本スポーツ振興センター くじ指定試合等の公示",
            "hintModel": "参考予想はアプリ内のポアソンモデル。当たりを保証するものではない",
            "disclaimer": "くじの購入は19歳以上。ここでの予想は購入の代わりにはなりません。",
        },
        "rounds": rounds,
    }
    (PROCESSED / "toto.json").write_text(
        json.dumps(out_live, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    summaries = {k: summarize(past, k) for k in KINDS}
    league = league_backtest(ctx)
    out_bt = {
        "meta": {"generatedAtJst": now, "roundsAnalyzed": len(past),
                 "note": "レーティングは各回の締切前までの試合だけで再計算している"},
        "summaries": {k: v for k, v in summaries.items() if v},
        "leagueBacktest": league,
    }
    (PROCESSED / "toto_backtest.json").write_text(
        json.dumps(out_bt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    print(f"販売中 {len(live)}回 / 過去 {len(past)}回 / 対象外 {skipped}回")
    if league:
        print(f"  リーグ戦全体: {league['matches']}試合 的中{league['hitRate']*100:.1f}% "
              f"(モデル見込み{league['expectedHitRate']*100:.1f}% でたらめ33.3% "
              f"全部ホーム{league['homeOnlyHitRate']*100:.1f}%)")
        print(f"    引き分け: 実際{league['drawActualRate']*100:.1f}% "
              f"モデル見込み{league['drawPredictedRate']*100:.1f}% "
              f"本命にした割合{league['drawPickedRate']*100:.1f}%")
        for c in league["calibration"]:
            print(f"    {c['bin']:>8}: n={c['n']:>3} 予想{c['predicted']*100:.1f}% 実際{c['actual']*100:.1f}%")
    for k, v in summaries.items():
        if v:
            print(f"  {k}: {v['rounds']}回 平均{v['meanHits']}/{v['size']}的中 "
                  f"(モデル見込み{v['expectedHits']} でたらめ{v['randomHits']} 全部ホーム{v['homeOnlyHits']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
