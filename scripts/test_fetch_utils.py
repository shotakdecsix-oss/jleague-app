"""
fetch_utils.fetch_round_raw() の429ハンドリングを、実際の requests.get をモックして検証する。
ネットワーク不要。sleep_fnを差し替えるので実際に75秒待つこともしない。

実行方法:
    python scripts/test_fetch_utils.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_utils as fu  # noqa: E402

import requests  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise AssertionError(
                "429レスポンスで.json()を呼んではいけない(HTMLが返ってくるため)。"
                "fetch_round_rawはステータスコードで分岐してから.json()を呼ぶ実装のはず。"
            )
        return self._json_data


def test_429_then_ok() -> None:
    """429を2回検知 -> 75秒固定待機を2回 -> 3回目で成功する。"""
    calls = {"n": 0}
    waits: list[float] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] <= 2:
            return FakeResponse(429, text="x" * 7196)  # Cloudflareの固定サイズHTMLエラーページを模擬
        return FakeResponse(200, json_data={"events": [{"idEvent": "e1"}]})

    original_get = requests.get
    requests.get = fake_get
    try:
        result = fu.fetch_round_raw(
            "4633", 1, "2026-2027",
            rate_limit_wait=75.0,
            sleep_fn=lambda s: waits.append(s),
        )
    finally:
        requests.get = original_get

    assert result.outcome == fu.Outcome.OK, f"3回目で成功するはず: {result}"
    assert result.count_429 == 2, f"429を2回カウントするはず: {result.count_429}"
    assert waits == [75.0, 75.0], f"75秒固定待機が2回のはず(指数バックオフではない): {waits}"
    assert calls["n"] == 3
    assert result.request_count == 3, f"実リクエスト数(429x2+成功1)が正しく数えられるはず: {result.request_count}"
    print("OK: 429を2回検知後、75秒固定待機を2回はさんで同じ節をリトライしOKに到達(request_count=3)")


def test_429_never_calls_json_and_exceeds_limit() -> None:
    """429が上限を超えて続く場合はHTTP_ERRORで終了し、resp.json()は一度も呼ばれない。"""
    calls = {"n": 0}
    waits: list[float] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        return FakeResponse(429, text="x" * 7196)  # 常に429(HTML)

    original_get = requests.get
    requests.get = fake_get
    try:
        result = fu.fetch_round_raw(
            "4633", 1, "2026-2027",
            rate_limit_wait=1.0,  # テスト高速化のため短くする(本番は75.0)
            max_429_retries=3,
            sleep_fn=lambda s: waits.append(s),
        )
    finally:
        requests.get = original_get

    assert result.outcome == fu.Outcome.HTTP_ERROR, f"上限超過後はHTTP_ERRORのはず: {result}"
    assert result.count_429 == 4, f"上限3を超えた4回目で打ち切るはず: {result.count_429}"
    assert calls["n"] == 4
    assert result.request_count == 4, f"実リクエスト数も4回のはず: {result.request_count}"
    print("OK: 429が上限を超えて続く場合はHTTP_ERRORで終了し、.json()は一度も呼ばれない")


def test_timeout_uses_exponential_backoff_not_fixed_wait() -> None:
    """タイムアウトは429と違い、指数バックオフ(固定75秒待機ではない)でリトライする。"""
    waits: list[float] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        raise requests.Timeout("simulated timeout")

    original_get = requests.get
    requests.get = fake_get
    try:
        result = fu.fetch_round_raw(
            "4633", 1, "2026-2027",
            max_retries=3, base_backoff=2.0,
            sleep_fn=lambda s: waits.append(s),
        )
    finally:
        requests.get = original_get

    assert result.outcome == fu.Outcome.TIMEOUT
    assert waits == [2.0, 4.0, 8.0], f"指数バックオフ(2,4,8秒)のはず、75秒固定ではない: {waits}"
    assert result.request_count == 4, f"タイムアウト4回ぶんの実リクエスト数のはず: {result.request_count}"
    print("OK: タイムアウトは指数バックオフ(429の固定75秒待機とは別ロジック)")


def main() -> None:
    tests = [
        test_429_then_ok,
        test_429_never_calls_json_and_exceeds_limit,
        test_timeout_uses_exponential_backoff_not_fixed_wait,
    ]
    for t in tests:
        t()
    print(f"\n全{len(tests)}件OK")


if __name__ == "__main__":
    main()
