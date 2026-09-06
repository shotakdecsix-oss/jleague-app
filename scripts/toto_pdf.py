"""
スポーツくじ(toto/BIG)の「くじ指定試合表」PDFを構造化する。

出典: 日本スポーツ振興センター(JSC) くじ指定試合等の公示
  一覧: https://www.jpnsport.go.jp/sinko/tabid/71/Default.aspx
  PDF : https://www.jpnsport.go.jp/sinko/Portals/0/sinko/toto/pdf/shiteijiai_<回号>.pdf

robots.txt(2026-09-06 実機確認): User-Agent:* に対する Disallow は
  /jiss/ /ntc/ /nishigaoka/ の3つのみ。/sinko/ は許可されている。

PDFの構造(実測・2026-09-06に第1650/1651/1653回で確認):
  タイトル : 「第１６５３回【指定試合表】」(全角数字)
  ヘッダ行 : No / 開催日 / ホーム / VS / アウェイ / くじ種別が横に並ぶ。
             くじ種別は BIG, MEGA BIG, 100円BIG, BIG1000, mini BIG,
             toto, mini toto A, mini toto B, toto GOAL3。
             見出しは2〜3段に折り返されている(例: 'mini'/'toto'/'A')。
  本文     : 1行1試合。くじ種別の列に丸数字(①②③…)が入っていれば
             「その種別の第n試合」の意味。空欄はその種別の対象外。

  ★列はx座標で割り当てること。pdftotext -layout の空白区切りでは列を
    取り違える。第1650回の第6試合は mini toto A だけが空欄で、空白区切り
    だと mini toto B の値が A の位置にずれて読めてしまう(実際に誤読した)。

  ★GOAL3だけはデータ列が2つ(ホーム得点・アウェイ得点のマーク番号)で、
    ヘッダのクラスタは1つ。x0の小さい方をホームとして [n, n+1] で持つ。

  ★回によって列構成が変わる。第1651回はBIG系が丸ごと無い。
    「≪Ｊリーグの試合が対象となります≫」の文言は天皇杯が対象の回にも
    書かれているので、対象大会の判定には使えない(第1651回が実例:
    レイラック滋賀FC vs 東京ヴェルディ 等は天皇杯)。
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
ZEN_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

ROW_TOL = 8.0      # 同じ行とみなすtopの差(実測: 行間17pt、行内のブレは3pt)
CLUSTER_GAP = 8.0  # 同じ列見出しとみなすx方向の隙間(実測: 列間13pt以上、段組みは重なる)


def circled_to_int(text: str) -> int | None:
    idx = CIRCLED.find(text)
    return idx + 1 if idx >= 0 else None


def _group_rows(words: list[dict]) -> list[list[dict]]:
    """単語をtopでまとめて行にする。"""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= ROW_TOL:
            rows[-1].append(w)
        else:
            rows.append([w])
    for r in rows:
        r.sort(key=lambda w: w["x0"])
    return rows


def _cluster_columns(words: list[dict]) -> list[dict]:
    """ヘッダ領域の単語をx方向にまとめて列見出しにする。
    見出しは段に分かれている('mini'/'toto'/'A')ので、上の段から順に連結する。"""
    cols: list[dict] = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if cols and w["x0"] - cols[-1]["x1"] <= CLUSTER_GAP:
            c = cols[-1]
            c["words"].append(w)
            c["x0"] = min(c["x0"], w["x0"])
            c["x1"] = max(c["x1"], w["x1"])
        else:
            cols.append({"x0": w["x0"], "x1": w["x1"], "words": [w]})
    for c in cols:
        c["words"].sort(key=lambda w: (w["top"], w["x0"]))
        c["label"] = " ".join(w["text"] for w in c["words"])
        c["key"] = re.sub(r"\s+", "", c["label"])
        c["center"] = (c["x0"] + c["x1"]) / 2
        del c["words"]
    return cols


def parse_shiteijiai(path: str | Path) -> dict:
    """指定試合表PDFを辞書にする。

    戻り値:
      {"kai": 1653, "note": "≪Ｊリーグの試合…≫", "columns": ["BIG", …],
       "matches": [{"no":1, "date":"9/12", "home":"水戸ホーリーホック",
                    "away":"川崎フロンターレ", "marks":{"toto":1, "totoGOAL3":[1,2]}}, …]}
    """
    with pdfplumber.open(str(path)) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()

    rows = _group_rows(words)

    kai = None
    note = ""
    header_idx = None
    for i, row in enumerate(rows):
        text = "".join(w["text"] for w in row)
        if kai is None:
            m = re.search(r"第([０-９0-9]+)回", text)
            if m:
                kai = int(m.group(1).translate(ZEN_DIGITS))
        if not note and "対象となります" in text:
            note = text
        if header_idx is None and any(w["text"] == "No" for w in row) and "開催日" in text:
            header_idx = i
    if header_idx is None:
        raise ValueError(f"ヘッダ行(No/開催日)が見つからない: {path}")

    # ヘッダは複数段にまたがる。'No'の行を基準に上下25ptを一体で見る。
    base_top = rows[header_idx][0]["top"]
    head_words = [w for w in words if base_top - 30 <= w["top"] <= base_top + 20]

    fixed = {}
    for w in head_words:
        if w["text"] in ("No", "開催日", "ホーム", "VS", "アウェイ"):
            fixed.setdefault(w["text"], w)
    if "アウェイ" not in fixed:
        raise ValueError(f"ヘッダの『アウェイ』が見つからない: {path}")

    kuji_words = [w for w in head_words if w["x0"] > fixed["アウェイ"]["x1"] + 5]
    columns = _cluster_columns(kuji_words)
    if not columns:
        raise ValueError(f"くじ種別の列が見つからない: {path}")
    kuji_left = min(c["x0"] for c in columns) - 12

    date_left = (fixed["開催日"]["x0"] + fixed["No"]["x1"]) / 2
    home_left = (fixed["開催日"]["x1"] + fixed["ホーム"]["x0"]) / 2
    vs_left = fixed["VS"]["x0"] - 3
    away_left = fixed["VS"]["x1"] + 3

    matches = []
    for row in rows[header_idx + 1:]:
        cells: dict[str, list[str]] = {k: [] for k in ("no", "date", "home", "away")}
        marks_raw: list[tuple[float, str]] = []
        for w in row:
            x = w["x0"]
            if x >= kuji_left:
                for ch in w["text"]:
                    if ch in CIRCLED:
                        marks_raw.append((w["x0"], ch))
            elif x >= away_left:
                cells["away"].append(w["text"])
            elif x >= vs_left:
                pass  # "VS"
            elif x >= home_left:
                cells["home"].append(w["text"])
            elif x >= date_left:
                cells["date"].append(w["text"])
            else:
                cells["no"].append(w["text"])
        no_text = "".join(cells["no"]).translate(ZEN_DIGITS)
        date_text = "".join(cells["date"]).translate(ZEN_DIGITS)
        if not re.fullmatch(r"\d{1,2}", no_text) or not re.fullmatch(r"\d{1,2}/\d{1,2}", date_text):
            continue
        home = "".join(cells["home"])
        away = "".join(cells["away"])
        if not home or not away:
            continue

        marks: dict[str, object] = {}
        for x0, ch in marks_raw:
            col = min(columns, key=lambda c: abs((x0 + 6) - c["center"]))
            n = circled_to_int(ch)
            key = col["key"]
            if key in marks:
                cur = marks[key]
                marks[key] = (cur if isinstance(cur, list) else [cur]) + [n]
            else:
                marks[key] = n

        matches.append({
            "no": int(no_text),
            "date": date_text,
            "home": home,
            "away": away,
            "marks": marks,
        })

    return {
        "kai": kai,
        "note": note,
        "columns": [c["key"] for c in columns],
        "matches": matches,
    }


def picks_for(parsed: dict, kuji_key: str) -> list[dict]:
    """指定のくじ種別の対象試合を、マークシートの番号順に返す。"""
    out = []
    for m in parsed["matches"]:
        n = m["marks"].get(kuji_key)
        if n is None:
            continue
        out.append({**{k: m[k] for k in ("date", "home", "away")},
                    "mark": n, "no": m["no"]})
    out.sort(key=lambda x: x["mark"] if isinstance(x["mark"], int) else x["mark"][0])
    return out
