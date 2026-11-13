"""
京阪バスの接近情報の境界条件。実データの検証は test_bus_diagram.py で行う。
"""

import gzip
import json

import httpx
import pytest

from keihan_tracker import KeihanAPIError
from keihan_tracker.bus import client as bus_client
from keihan_tracker.bus.tracker import _default_client
from keihan_tracker.bus.tracker import get_khbus_info
from keihan_tracker.train.tracker import USER_AGENT

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
    state = {"body": json.dumps(_response([PRMS]), ensure_ascii=False), "requests": 0, "user_agent": None}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        state["user_agent"] = request.headers.get("User-Agent")
        return httpx.Response(200, text=state["body"])

    monkeypatch.setattr(
        bus_client, "AsyncClient",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(_default_client, "_cache", {})
    monkeypatch.setattr(_default_client, "_last_snapshot", {})
    return state


def test_bus_parsed(bus_api):
    res = run(get_khbus_info("京阪香里園", 1))
    prms = res.body.busstates[0].busstateprms
    assert prms.order == 1
    assert prms.route == "[22]"
    assert prms.destination == "京阪香里園"
    assert prms.status == "まもなく到着"
    assert prms.timetable == "17:30 到着予定"


def test_bus_user_agent_names_library(bus_api):
    run(get_khbus_info("京阪香里園", 1))
    assert bus_api["user_agent"] == USER_AGENT


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


def test_bus_no_snapshot_by_default(bus_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run(get_khbus_info("京阪香里園", 1))
    assert list(tmp_path.rglob("*")) == []


def test_bus_snapshot_saves_raw_response(bus_api, tmp_path, monkeypatch):
    run(get_khbus_info("寝屋川市駅［東口］", 2, snapshot_dir=tmp_path))
    saved = list(tmp_path.rglob("*.gz"))
    assert [p.name.split("_", 1)[1] for p in saved] == ["busstateupd_寝屋川市駅［東口］_2.json.gz"]
    assert gzip.decompress(saved[0].read_bytes()).decode("utf-8") == bus_api["body"]

    # 内容が同じなら保存しない。キャッシュを返したときも保存しない
    monkeypatch.setattr(_default_client, "_cache", {})
    run(get_khbus_info("寝屋川市駅［東口］", 2, snapshot_dir=tmp_path))
    run(get_khbus_info("寝屋川市駅［東口］", 2, snapshot_dir=tmp_path))
    assert len(list(tmp_path.rglob("*.gz"))) == 1


def test_bus_snapshot_saved_even_if_validation_fails(bus_api, tmp_path):
    """仕様変更でパースに失敗した応答こそ残したい"""
    bus_api["body"] = '{"unexpected": true}'
    with pytest.raises(KeihanAPIError):
        run(get_khbus_info("京阪香里園", 1, snapshot_dir=tmp_path))
    saved = next(tmp_path.rglob("*.gz"))
    assert gzip.decompress(saved.read_bytes()) == b'{"unexpected": true}'
