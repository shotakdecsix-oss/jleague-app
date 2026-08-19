"""
kickoff時刻の導出ルール（確定事項）。

TheSportsDBの `dateEvent` / `dateEventLocal` / `strTimeLocal` は信用しない。
根拠: J3第1節 idEvent=2491617 (FC琉球 vs ギラヴァンツ北九州, strStatus="NS") で
      strTimestamp   = "2026-09-09T10:00:00" (UTC)
      dateEvent      = "2026-09-09"   <- strTimestampと整合
      dateEventLocal = "2026-08-08"   <- 1ヶ月ずれ。延期の日程差し替えが
                                          strTimestampには反映されたが、
                                          dateEventLocalには反映されていない典型例
strTimestampはdateEventと整合していたため、延期後の正しい日程を反映しているのは
strTimestamp側だと判断できる。よって日付・時刻とも strTimestamp からのみ導出する。

これにより:
  - このイベントは実際には2026-09-09に延期されている可能性が高い
  - 延期試合が存在する = 順位表は勝点だけでなく消化試合数(intPlayed)を
    必ず併記しないと「1試合未消化で勝点3差」を誤読させる
    (順位表計算ロジック実装時にintPlayedを必須フィールドにすること)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class KickoffJst:
    iso: str        # "2026-09-09T19:00:00+09:00"
    date: str        # "2026-09-09"
    time: str        # "19:00:00"


def derive_kickoff_jst(str_timestamp: str) -> KickoffJst:
    """
    strTimestamp (UTC, "YYYY-MM-DDTHH:MM:SS"形式) からJSTの日付・時刻を導出する。
    dateEvent / dateEventLocal / strTimeLocal は一切参照しない。
    """
    utc_dt = datetime.fromisoformat(str_timestamp).replace(tzinfo=timezone.utc)
    jst_dt = utc_dt.astimezone(JST)
    return KickoffJst(
        iso=jst_dt.isoformat(),
        date=jst_dt.date().isoformat(),
        time=jst_dt.time().isoformat(),
    )


if __name__ == "__main__":
    # 上記の実例で動作確認
    sample = derive_kickoff_jst("2026-09-09T10:00:00")
    print(f"strTimestamp=2026-09-09T10:00:00 (UTC) -> kickoffJst={sample.iso}")
    assert sample.date == "2026-09-09", "dateEventLocalの8/8ではなくdateEvent側の9/9と一致するはず"
    print("OK: dateEventLocal(8/8)ではなくdateEvent(9/9)側と一致することを確認")
