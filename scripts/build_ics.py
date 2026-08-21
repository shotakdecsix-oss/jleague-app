"""
全60クラブぶんのカレンダー(.ics)を dist/ics/{idTeam}.ics に直接生成する。

data/processed/{league}_matches.json をソースツリーに置いたまま、dist/ics/ の60ファイルは
ソースツリーにコミットしない(build_dist.pyのCOPY_DIRSにではなく、build_dist.pyからこのスクリプトを
呼んでdist直下に直接生成する形)。update_all.pyの手順にも追加する。

対象試合: そのクラブがホームまたはアウェイで出場する全試合({league}_matches.json全件、finishedを問わない)。

DTSTARTはUTC(Zサフィックス)で出す。kickoffJst(strTimestamp由来、既存方針)からUTCに変換するだけで
VTIMEZONEブロックが不要になる。dateEvent/dateEventLocal/strTimeLocalは一切参照しない。

SEQUENCEの永続化(最重要): data/history/ics_state.json にイベントごとの状態を永続化する。
dtstart/summary/statusのいずれかが変わったらSEQUENCEを+1し、据え置くと購読済みカレンダー側が
更新を無視し続けて古い日程が表示され続ける(延期試合は実在する。handoffのJ3琉球vs北九州の例)。
ics_state.jsonはリポジトリにコミットする(Renderのディスクは揮発するため、確率推移履歴と同じ理由)。
ドキュメント上のサンプルには無いが、CANCELLED後もどのクラブの試合か分かるように homeIdTeam/awayIdTeam、
再描画に必要な dtend/allDay/location/description/hasAlarm も併せて永続化する
(stateだけで各VEVENTを完全に再構築できるようにするため)。

消えた試合(前回のics_state.jsonに存在するが今回のmatchesに存在しないidEvent)は、VEVENTを削除せず
STATUS:CANCELLEDにして残す(同時にSEQUENCE+1)。一度CANCELLEDにしたら、以後の生成でもCANCELLEDのまま
出し続ける(シーズン終了まで)。

会場名(LOCATION)の解決はリーグでマスタ構造が違うため分岐する:
  J2: 試合のidVenue -> マスタのvenues[idVenue].ja
  J1/J3: venuesセクションが無いので、ホームチームのhomeVenueJaを使う(ただし味の素スタジアム等の
         共用/中立地開催で食い違うことがあるため、DESCRIPTION側に確認を促す一文を必ず添える)
  どちらも解決できない場合はLOCATION行ごと省略する(空文字を出さない)。

CLI:
    python scripts/build_ics.py
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MASTERS_DIR = BASE_DIR / "data" / "masters"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
DIST_DIR = BASE_DIR / "dist"
ICS_STATE_PATH = BASE_DIR / "data" / "history" / "ics_state.json"

MASTER_FILES = {
    "j1": MASTERS_DIR / "j1_teams_2026-27.json",
    "j2": MASTERS_DIR / "j2_master_2026-27.json",
    "j3": MASTERS_DIR / "j3_teams_2026-27.json",
}

ALARM_TRIGGER = "-PT1H"  # キックオフ1時間前に通知
FOLD_LIMIT_FIRST = 75    # RFC5545: 1行あたり75オクテット(CRLFを含まない)
FOLD_LIMIT_CONT = 74     # 継続行は先頭の1スペース分を差し引く


def season_short(season: str) -> str:
    """'2026-2027' -> '2026-27'。想定外の形式ならそのまま返す。"""
    parts = season.split("-")
    if len(parts) == 2 and len(parts[1]) == 4:
        return f"{parts[0]}-{parts[1][-2:]}"
    return season


def ics_escape(text: str) -> str:
    """RFC5545のTEXT値エスケープ(バックスラッシュ・カンマ・セミコロン・改行)。"""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> str:
    """
    RFC5545の行折り返し(75オクテット、継続行は先頭に1スペース)。
    UTF-8のマルチバイト境界を割らないよう、バイト単位で切ってから継続バイトなら後退させる。
    """
    data = line.encode("utf-8")
    if len(data) <= FOLD_LIMIT_FIRST:
        return line

    chunks: list[bytes] = []
    start = 0
    limit = FOLD_LIMIT_FIRST
    while start < len(data):
        end = min(start + limit, len(data))
        while end < len(data) and (data[end] & 0xC0) == 0x80:  # UTF-8継続バイトなら後退
            end -= 1
        if end <= start:  # 極端に短い制限で1文字も入らない場合の保険(実運用では起きない)
            end = start + 1
        chunks.append(data[start:end])
        start = end
        limit = FOLD_LIMIT_CONT

    out_lines = [chunks[0].decode("utf-8")]
    for c in chunks[1:]:
        out_lines.append(" " + c.decode("utf-8"))
    return "\r\n".join(out_lines)


def render_ics(lines: list[str]) -> str:
    """折り返し済みのCRLF区切りテキストを組み立てる(末尾もCRLFで終える)。"""
    return "\r\n".join(fold_line(l) for l in lines) + "\r\n"


def resolve_venue(league: str, match: dict, home_team: dict, master: dict) -> tuple[str | None, bool]:
    """
    (LOCATION文字列 or None, フォールバック由来で不確実か) を返す。
    J2はvenues辞書で正規に引けるのでuncertain=False。J1/J3はhomeVenueJaのフォールバックなのでTrue。
    """
    if league == "j2":
        venues = master.get("venues", {})
        v = venues.get(match.get("idVenue"))
        if v and v.get("ja"):
            return v["ja"], False
        return None, False
    venue = home_team.get("homeVenueJa")
    if venue:
        return venue, True
    return None, False


def resolve_event(league: str, match: dict, home_team: dict, master: dict) -> dict:
    """1試合ぶんの「解決済みイベント」を作る(ics_stateへ永続化する値そのもの)。"""
    home, away = match["home"], match["away"]
    round_no = match.get("round", "?")
    league_label = league.upper()
    finished = bool(match.get("finished"))
    all_day = bool(match.get("kickoffTbd"))

    if all_day:
        d = date.fromisoformat(match["kickoffDate"])
        dtstart = d.strftime("%Y%m%d")
        dtend = (d + timedelta(days=1)).strftime("%Y%m%d")
        summary = f"{home['short']} vs {away['short']}（{league_label} 第{round_no}節・時刻未定）"
        description = ""
        has_alarm = False
    else:
        dt = datetime.fromisoformat(match["kickoffJst"])
        utc = dt.astimezone(timezone.utc)
        dtstart = utc.strftime("%Y%m%dT%H%M%SZ")
        dtend = (utc + timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")
        description = f"キックオフ {dt.strftime('%H:%M')}"
        has_alarm = True
        if finished and home.get("score") is not None and away.get("score") is not None:
            summary = f"{home['short']} {home['score']}-{away['score']} {away['short']}（{league_label} 第{round_no}節）"
        else:
            summary = f"{home['short']} vs {away['short']}（{league_label} 第{round_no}節）"

    location, venue_uncertain = resolve_venue(league, match, home_team, master)
    if venue_uncertain:
        slug = home_team.get("jleagueSlug", "")
        note = "会場は公式サイトで確認してください: https://www.jleague.jp/club/" + slug + "/"
        description = (description + "\n" + note).strip() if description else note

    return {
        "dtstart": dtstart,
        "dtend": dtend,
        "allDay": all_day,
        "summary": summary,
        "location": location,
        "description": description,
        "hasAlarm": has_alarm,
        "homeIdTeam": home["idTeam"],
        "awayIdTeam": away["idTeam"],
    }


def update_state_entry(state: dict, id_event: str, resolved: dict) -> None:
    """
    stateを直接書き換える(戻り値なし)。dtstart/summary/statusのいずれかが変わったらseqを+1する。
    新規イベントはseq:0で登録する。
    """
    new_status = "CONFIRMED"
    prev = state.get(id_event)
    if prev is None:
        state[id_event] = {
            "seq": 0,
            "status": new_status,
            **{k: v for k, v in resolved.items()},
        }
        return

    changed = (
        prev.get("dtstart") != resolved["dtstart"]
        or prev.get("summary") != resolved["summary"]
        or prev.get("status") != new_status
    )
    if changed:
        prev["seq"] = prev.get("seq", 0) + 1
    prev["status"] = new_status
    for k, v in resolved.items():
        prev[k] = v


def build_vevent(id_event: str, entry: dict, now_stamp: str) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{id_event}@jleague-app", f"DTSTAMP:{now_stamp}"]
    if entry.get("allDay"):
        lines.append(f"DTSTART;VALUE=DATE:{entry['dtstart']}")
        lines.append(f"DTEND;VALUE=DATE:{entry['dtend']}")
    else:
        lines.append(f"DTSTART:{entry['dtstart']}")
        lines.append(f"DTEND:{entry['dtend']}")
    lines.append(f"SEQUENCE:{entry.get('seq', 0)}")
    lines.append(f"SUMMARY:{ics_escape(entry['summary'])}")
    if entry.get("location"):
        lines.append(f"LOCATION:{ics_escape(entry['location'])}")
    if entry.get("description"):
        lines.append(f"DESCRIPTION:{ics_escape(entry['description'])}")
    lines.append(f"STATUS:{entry.get('status', 'CONFIRMED')}")
    # 終日イベント(時刻未定)には前日通知が意味を成さないので付けない。CANCELLEDにも付けない。
    if entry.get("hasAlarm") and not entry.get("allDay") and entry.get("status") != "CANCELLED":
        lines.append("BEGIN:VALARM")
        lines.append(f"TRIGGER:{ALARM_TRIGGER}")
        lines.append("ACTION:DISPLAY")
        lines.append(f"DESCRIPTION:{ics_escape(entry['summary'])}")
        lines.append("END:VALARM")
    lines.append("END:VEVENT")
    return lines


def build_calendar_lines(club_name_ja: str, season: str, vevent_groups: list[list[str]]) -> list[str]:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//jleague-app//JP",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(club_name_ja)} {season_short(season)}",
        "X-WR-TIMEZONE:Asia/Tokyo",
        "X-PUBLISHED-TTL:PT12H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    ]
    for group in vevent_groups:
        lines.extend(group)
    lines.append("END:VCALENDAR")
    return lines


def load_master(league: str) -> dict:
    path = MASTER_FILES[league]
    if not path.exists():
        return {"teams": [], "meta": {}, "venues": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def load_matches(league: str) -> list[dict]:
    path = PROCESSED_DIR / f"{league}_matches.json"
    if not path.exists():
        print(f"[warn] {path} が無い。{league}のicsはこの回スキップ", file=sys.stderr)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("matches", [])


def load_ics_state() -> dict:
    if not ICS_STATE_PATH.exists():
        return {}
    return json.loads(ICS_STATE_PATH.read_text(encoding="utf-8"))


def save_ics_state(state: dict) -> None:
    ICS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ICS_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_club_ics(dist_ics_dir: Path, team: dict, season: str, state: dict, now_stamp: str) -> None:
    tid = team["idTeam"]
    entries = [
        (id_event, e) for id_event, e in state.items()
        if e.get("homeIdTeam") == tid or e.get("awayIdTeam") == tid
    ]
    entries.sort(key=lambda kv: kv[1]["dtstart"])
    vevent_groups = [build_vevent(id_event, e, now_stamp) for id_event, e in entries]
    lines = build_calendar_lines(team.get("ja", ""), season, vevent_groups)
    text = render_ics(lines)
    (dist_ics_dir / f"{tid}.ics").write_text(text, encoding="utf-8", newline="")


def build_all(dist_dir: Path = DIST_DIR) -> tuple[int, int]:
    """
    全リーグを処理し、ics_state.jsonを更新して dist_dir/ics/ に全クラブぶんのicsを書く。
    戻り値は (生成したクラブ数, 今回新たにCANCELLEDにしたイベント数)。テストでも直接呼べるように分離。
    """
    all_masters = {lg: load_master(lg) for lg in MASTER_FILES}
    all_matches = {lg: load_matches(lg) for lg in MASTER_FILES}

    state = load_ics_state()
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    current_event_ids: set[str] = set()

    for lg, matches in all_matches.items():
        master = all_masters[lg]
        team_lookup = {t["idTeam"]: t for t in master.get("teams", [])}
        for m in matches:
            id_event = m.get("idEvent")
            if not id_event:
                continue
            current_event_ids.add(id_event)
            home_team = team_lookup.get(m["home"]["idTeam"], {})
            resolved = resolve_event(lg, m, home_team, master)
            update_state_entry(state, id_event, resolved)

    cancelled_count = 0
    for id_event, entry in state.items():
        if id_event in current_event_ids:
            continue
        if entry.get("status") != "CANCELLED":
            entry["status"] = "CANCELLED"
            entry["seq"] = entry.get("seq", 0) + 1
            cancelled_count += 1

    save_ics_state(state)

    dist_ics_dir = dist_dir / "ics"
    if dist_ics_dir.exists():
        shutil.rmtree(dist_ics_dir)
    dist_ics_dir.mkdir(parents=True, exist_ok=True)

    club_count = 0
    for lg, master in all_masters.items():
        season = master.get("meta", {}).get("season", "2026-2027")
        for team in master.get("teams", []):
            write_club_ics(dist_ics_dir, team, season, state, now_stamp)
            club_count += 1

    return club_count, cancelled_count


def main() -> None:
    club_count, cancelled_count = build_all()
    print(
        f"[info] {DIST_DIR / 'ics'} に{club_count}クラブぶんのicsを生成 "
        f"(今回新たにCANCELLEDにしたイベント: {cancelled_count})"
    )


if __name__ == "__main__":
    main()
