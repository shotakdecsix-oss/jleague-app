"""
複数節をまとめて取得するための共通ユーティリティ。
scripts/fetch_batch.py がこれを使う。

レート制限の実測値(PC実機計測・2026-08-13。推測で変えないこと):
  制限     : 約25リクエスト/分(26件目から429)
  429の中身 : CloudflareのHTMLエラーページ(固定7196バイト)。JSONではない
  回復     : 最後の429から60〜75秒で復帰
  ヘッダ    : RateLimit-* / Retry-After は一切返らない -> ヘッダを読む実装は無意味

このため429は指数バックオフの対象外にしてある: 固定75秒待ってから同じ節を
そのままリトライする。429のレスポンスに resp.json() は絶対に呼ばない
(HTMLが返ってくるため)。タイムアウト・5xx・不正JSONは従来どおり指数バックオフ。

もう一つの安全弁: J1/J2/J3は全38節の日程が確定済みなので、正常なら全節で
イベントが返るはず。`{"events": null}` は「その節に試合が無い」ではなく異常
シグナルとして扱い、N節連続で異常が続いたら処理を中断する(壊れたJSONを
静かにコミットする事故を防ぐため)。
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

API_URL = "https://www.thesportsdb.com/api/v1/json/123/eventsround.php"

DEFAULT_SLEEP_BETWEEN = 2.5      # 節と節の間の通常ウェイト(実測: 25req/分制限に対する安全マージン込み)
DEFAULT_RATE_LIMIT_WAIT = 75.0   # 429検知時の固定待機秒数(実測: 60〜75秒で復帰)
DEFAULT_MAX_429_RETRIES = 5      # 429の連続リトライ上限(75秒*5=375秒待っても復帰しなければ異常とみなす)


class Outcome(Enum):
    OK = "ok"                  # events に1件以上あり
    EMPTY = "empty"             # HTTP 200 だが events が null または []
    HTTP_ERROR = "http_error"   # リトライを使い切っても非200(429の上限超過含む)
    TIMEOUT = "timeout"         # リトライを使い切ってもタイムアウト
    BAD_JSON = "bad_json"       # レスポンスがJSONとしてパースできない


@dataclass
class RoundFetch:
    """fetch_round_raw() の戻り値。"""
    outcome: Outcome
    events: list[dict] | None
    detail: str
    count_429: int = 0
    request_count: int = 0  # 実際にrequests.get()を呼んだ回数(429/5xx/タイムアウトのリトライも含む)


@dataclass
class RoundResult:
    round: int
    outcome: Outcome
    event_count: int
    detail: str = ""
    events: list[dict] = field(default_factory=list)  # 呼び出し元(fetch_batch.py)が実データを使うために保持


@dataclass
class FetchAllResult:
    league: str
    results: list[RoundResult] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    count_429: int = 0
    request_count: int = 0  # 実際にAPIへ投げたHTTPリクエストの総数(リトライ込み)。meta.jsonの日次制限監視用

    @property
    def ok_rounds(self) -> list[int]:
        return [r.round for r in self.results if r.outcome == Outcome.OK]

    @property
    def bad_rounds(self) -> list[RoundResult]:
        return [r for r in self.results if r.outcome != Outcome.OK]


def fetch_round_raw(
    id_league: str,
    round_: int,
    season: str,
    timeout: float = 15.0,
    max_retries: int = 4,
    base_backoff: float = 2.0,
    rate_limit_wait: float = DEFAULT_RATE_LIMIT_WAIT,
    max_429_retries: int = DEFAULT_MAX_429_RETRIES,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> RoundFetch:
    """
    1節ぶんの生fetch。

    429(レート制限)は指数バックオフの対象外: 固定rate_limit_wait秒待ってから
    同じ節をそのままリトライする。429のレスポンスボディはHTMLなのでresp.json()は呼ばない
    (必ずステータスコードで分岐する)。

    タイムアウト・5xx・不正JSONは指数バックオフ(max_retriesまで)。
    """
    import requests

    params = {"id": id_league, "r": str(round_), "s": season}
    backoff_attempt = 0
    count_429 = 0
    request_count = 0
    last_detail = ""

    while True:
        request_count += 1
        try:
            resp = requests.get(API_URL, params=params, timeout=timeout)
        except requests.Timeout:
            backoff_attempt += 1
            if backoff_attempt > max_retries:
                return RoundFetch(
                    Outcome.TIMEOUT, None, f"timeout_after_{max_retries}_retries", count_429, request_count
                )
            last_detail = f"timeout(attempt{backoff_attempt})"
            sleep_fn(base_backoff ** backoff_attempt)
            continue
        except requests.RequestException as e:  # noqa: BLE001
            backoff_attempt += 1
            if backoff_attempt > max_retries:
                return RoundFetch(
                    Outcome.HTTP_ERROR, None, f"request_exception:{e}", count_429, request_count
                )
            last_detail = f"request_exception:{e}(attempt{backoff_attempt})"
            sleep_fn(base_backoff ** backoff_attempt)
            continue

        if resp.status_code == 429:
            # 注意: resp.json()は絶対に呼ばない(CloudflareのHTMLエラーページが返るため)
            count_429 += 1
            if count_429 > max_429_retries:
                return RoundFetch(
                    Outcome.HTTP_ERROR, None, f"429_exceeded_{max_429_retries}_retries", count_429, request_count
                )
            print(
                f"[warn] HTTP 429 (rate limited) round={round_} -> "
                f"{rate_limit_wait:.0f}秒固定待機してリトライ ({count_429}/{max_429_retries})",
                file=sys.stderr,
            )
            sleep_fn(rate_limit_wait)
            continue  # バックオフのattemptは消費しない。同じ節を再試行する

        if resp.status_code >= 500:
            backoff_attempt += 1
            if backoff_attempt > max_retries:
                return RoundFetch(
                    Outcome.HTTP_ERROR, None, f"http{resp.status_code}_after_retries", count_429, request_count
                )
            sleep_fn(base_backoff ** backoff_attempt)
            continue

        if resp.status_code != 200:
            return RoundFetch(Outcome.HTTP_ERROR, None, f"http{resp.status_code}", count_429, request_count)

        try:
            data = resp.json()
        except ValueError:
            backoff_attempt += 1
            if backoff_attempt > max_retries:
                return RoundFetch(
                    Outcome.BAD_JSON, None, "bad_json_after_retries", count_429, request_count
                )
            sleep_fn(base_backoff ** backoff_attempt)
            continue

        events = data.get("events")
        if not events:
            return RoundFetch(Outcome.EMPTY, events, "events_null_or_empty", count_429, request_count)
        return RoundFetch(Outcome.OK, events, f"{len(events)}events", count_429, request_count)


def fetch_all_rounds(
    league: str,
    id_league: str,
    season: str,
    total_rounds: int = 38,
    rounds: Iterable[int] | None = None,
    sleep_between: float = DEFAULT_SLEEP_BETWEEN,
    max_consecutive_bad: int = 3,
    fetch_fn: Callable[..., RoundFetch] = fetch_round_raw,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> FetchAllResult:
    """
    指定した節(デフォルト1〜total_rounds)を順に取得する。roundsで部分取得可能
    (検証用: --rounds 1-5 等)。

    max_consecutive_bad節連続で異常(OK以外)が続いたら中断する。
    """
    target_rounds = list(rounds) if rounds is not None else list(range(1, total_rounds + 1))
    result = FetchAllResult(league=league)
    consecutive_bad = 0

    for idx, r in enumerate(target_rounds):
        fetched = fetch_fn(id_league, r, season, sleep_fn=sleep_fn)
        count = len(fetched.events) if fetched.events else 0
        result.results.append(
            RoundResult(
                round=r,
                outcome=fetched.outcome,
                event_count=count,
                detail=fetched.detail,
                events=fetched.events or [],
            )
        )
        result.count_429 += fetched.count_429
        result.request_count += fetched.request_count

        if fetched.outcome == Outcome.OK:
            consecutive_bad = 0
        else:
            consecutive_bad += 1
            print(
                f"[warn] league={league} round={r} outcome={fetched.outcome.value} detail={fetched.detail} "
                f"(連続異常 {consecutive_bad}/{max_consecutive_bad})",
                file=sys.stderr,
            )

        if consecutive_bad >= max_consecutive_bad:
            result.aborted = True
            result.abort_reason = (
                f"round={r - max_consecutive_bad + 1}〜{r} で {max_consecutive_bad}節連続異常"
                f"(最後の詳細: {fetched.detail})。全38節は日程確定済みのため異常とみなし中断。"
            )
            print(f"[error] {result.abort_reason}", file=sys.stderr)
            break

        if idx < len(target_rounds) - 1:
            sleep_fn(sleep_between)

    return result


if __name__ == "__main__":
    # ネットワーク無しのオフライン動作確認。
    # 429まわりの詳細な検証は scripts/test_fetch_utils.py 側で行う。
    def fake_fetch(id_league, round_, season, sleep_fn=time.sleep):  # noqa: ANN001
        if round_ == 3:
            # 429を2回踏んでから成功した節、という想定(request_count=3: 429x2 + 成功1)
            return RoundFetch(Outcome.OK, [{"idEvent": "e3"}] * 10, "10events", count_429=1, request_count=3)
        if 6 <= round_ <= 8:
            return RoundFetch(Outcome.EMPTY, None, "events_null_or_empty", request_count=1)
        return RoundFetch(Outcome.OK, [{"idEvent": f"e{round_}"}] * 10, "10events", request_count=1)

    result = fetch_all_rounds(
        "J1(mock)", "4633", "2026-2027",
        total_rounds=38, sleep_between=0, max_consecutive_bad=3,
        fetch_fn=fake_fetch, sleep_fn=lambda s: None,
    )
    print(f"aborted={result.aborted}")
    print(f"count_429={result.count_429}")
    print(f"request_count={result.request_count}")
    print(f"最終round={result.results[-1].round}")
    assert result.aborted is True
    assert result.results[-1].round == 8, "round8(3連続EMPTY目)で中断されるはず"
    assert result.ok_rounds == [1, 2, 3, 4, 5]
    assert result.count_429 == 1, "round3の429リトライ1回がFetchAllResult.count_429に集計されるはず"
    # round1,2,4,5,6,7,8はrequest_count=1 x7、round3だけ3 -> 合計10
    assert result.request_count == 10, f"実リクエスト数が正しく積算されるはず: {result.request_count}"
    print("OK: 3節連続異常での中断 + count_429/request_count集計を確認")
