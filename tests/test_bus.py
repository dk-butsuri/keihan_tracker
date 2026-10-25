"""
京阪バスの接近情報。実データの fixture は無いため、パーサーが前提にしている形式で応答を組み立てる。
"""

import json

import httpx
import pytest

from keihan_tracker import KeihanAPIError
from keihan_tracker.bus import tracker as bus_tracker
from keihan_tracker.bus.tracker import get_khbus_info

from conftest import run

# 接近順位:緯度:経度:方位:GUID:停留所ID:のりば:系統:行先:経由:状態:時刻（時刻の「:」は __________ で表す）
PRMS = "1:34.79:135.63:90:guid-1:123:1:[22]:京阪香里園:三井団地:まもなく到着:17__________30 到着予定"


def _response(prms: list[str]) -> dict:
    return {
        "head": {"errorcode": 0, "objid": ""},
        "body": {
            "datetimeStr": "2026/10/26 17:25現在",
            "busstates": [{"html": "", "html_sp": "", "busstateprms": p} for p in prms],
        },
    }


@pytest.fixture
def bus_api(monkeypatch):
    """BUS NAVI のモック。responses[0] に入れた本文を返し、受け取ったリクエスト数を数える。"""
    state = {"body": json.dumps(_response([PRMS]), ensure_ascii=False), "requests": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        return httpx.Response(200, text=state["body"])

    monkeypatch.setattr(
        bus_tracker, "AsyncClient",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(bus_tracker, "_cache", {})
    return state


def test_bus_parsed(bus_api):
    res = run(get_khbus_info("京阪香里園", 1))
    prms = res.body.busstates[0].busstateprms
    assert prms.order == 1
    assert prms.route == "[22]"
    assert prms.destination == "京阪香里園"
    assert prms.status == "まもなく到着"
    assert prms.timetable == "17:30 到着予定"


def test_bus_cached_within_30_seconds(bus_api):
    first = run(get_khbus_info("京阪香里園", 1))
    assert run(get_khbus_info("京阪香里園", 1)) is first
    assert bus_api["requests"] == 1
    # のりばが違えば別に取得する
    run(get_khbus_info("京阪香里園", 2))
    assert bus_api["requests"] == 2


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('{"head": {"errorcode": 0, "objid": ""}, "bo', id="truncated-json"),
        pytest.param(json.dumps(_response(["1:34.79:135.63"])), id="too-few-fields"),
        pytest.param(json.dumps(_response([PRMS + ":余分"])), id="too-many-fields"),
        pytest.param(json.dumps(_response([PRMS.replace("1:34.79", "x:34.79", 1)])), id="not-a-number"),
    ],
)
def test_bus_broken_response_raises_keihan_api_error(bus_api, body):
    bus_api["body"] = body
    with pytest.raises(KeihanAPIError):
        run(get_khbus_info("京阪香里園", 1))
    # 失敗した取得はキャッシュしないので、次の呼び出しで取り直す
    bus_api["body"] = json.dumps(_response([PRMS]), ensure_ascii=False)
    assert run(get_khbus_info("京阪香里園", 1)).body.busstates
