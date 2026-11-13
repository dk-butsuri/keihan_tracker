"""公式ページから保存した接近情報・発着時刻表と、その境界条件。"""

import datetime
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from keihan_tracker import BusDiagram, KeihanAPIError, fetch_diagram, get_khbus_info
from keihan_tracker.bus import tracker as bus_tracker
from keihan_tracker.train.tracker import JST, USER_AGENT

from conftest import load_fixture_bytes, run


DGMPL = "同志社香里:1:1:75120:17:12"


@pytest.fixture
def api(monkeypatch):
    state = {
        "location": json.loads(load_fixture_bytes("bus/busstateupd.json")),
        "diagram": load_fixture_bytes("bus/linediagram.html"),
        "status": 200,
        "requests": [],
    }

    def handler(request):
        state["requests"].append(request)
        if request.url.path == "/pc/busstateupd":
            return httpx.Response(200, json=state["location"])
        assert request.method == "GET"
        assert request.url.path == "/pc/linediagram"
        return httpx.Response(state["status"], content=state["diagram"])

    monkeypatch.setattr(bus_tracker, "AsyncClient", lambda **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False, **kwargs,
    ))
    monkeypatch.setattr(bus_tracker, "_cache", {})
    monkeypatch.setattr(bus_tracker, "_diagram_cache", {})
    monkeypatch.setattr(bus_tracker, "_last_snapshot", {})
    return state


def test_saved_bus_data_unchanged():
    directory = Path(__file__).parent / "fixtures" / "bus"
    for entry in json.loads((directory / "manifest.json").read_text(encoding="utf-8")):
        raw = gzip.decompress((directory / entry["file"]).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]


def test_location_adds_dgmpl_to_each_bus_without_fetching_diagrams(api):
    response = run(get_khbus_info("同志社香里", 1))
    assert len(response.body.busstates) == 7
    assert response.body.busstates[0].dgmpl == "同志社香里:1:1:75120:17:53"
    assert all(bus.dgmpl is not None for bus in response.body.busstates)
    assert len(api["requests"]) == 1


@pytest.mark.parametrize("key, suffix", [
    ("7512:0:1032:7512", "75120:17:12"),
    ("1234:1:65:0099", "00991:1:5"),
    ("1234:0:1505:5678", "56780:25:5"),
])
def test_dgmpl_matches_official_button_rules(api, key, suffix):
    bus = api["location"]["body"]["busstates"][0]
    bus["html"] = f"<input value='{key}' class='extra bus_key' type='hidden'>"
    response = run(get_khbus_info("同志社香里", 2))
    assert response.body.busstates[0].dgmpl == f"同志社香里:2:1:{suffix}"


def test_dgmpl_uses_mobile_html_if_pc_html_has_no_key(api):
    api["location"]["body"]["busstates"][0]["html"] = "<div>接近情報</div>"
    response = run(get_khbus_info("同志社香里", 1))
    assert response.body.busstates[0].dgmpl == "同志社香里:1:1:75120:17:53"


def test_missing_bus_key_keeps_location_usable(api):
    bus = api["location"]["body"]["busstates"][0]
    bus["html"] = None
    bus["html_sp"] = ""
    response = run(get_khbus_info("同志社香里", 1))
    assert response.body.busstates[0].dgmpl is None
    assert response.body.busstates[0].busstateprms.route == "[22]"


@pytest.mark.parametrize("key", ["", "7512:0", "7512:0:x:7512", "7512:0:-1:7512"])
def test_broken_bus_key_is_not_cached(api, key):
    bus = api["location"]["body"]["busstates"][0]
    original = bus["html"]
    bus["html"] = f'<input class="bus_key" value="{key}">'
    with pytest.raises(KeihanAPIError, match="bus_key"):
        run(get_khbus_info("同志社香里", 1))
    bus["html"] = original
    assert run(get_khbus_info("同志社香里", 1)).body.busstates[0].dgmpl is not None
    assert len(api["requests"]) == 2


def test_diagram_parses_actual_stop_order_and_arrivals_and_departures(api):
    diagram = run(fetch_diagram(DGMPL))
    assert isinstance(diagram, BusDiagram)
    assert diagram.dgmpl == DGMPL
    assert len(diagram.stops) == 17
    first, last = diagram.stops[0], diagram.stops[-1]
    assert first.stop_name == "寝屋川市駅［東口］〔京阪バス〕"
    assert (first.arrival_time, first.departure_time) == (None, "17:02")
    assert last.stop_name == "京阪香里園〔京阪バス〕"
    assert (last.arrival_time, last.departure_time) == ("17:31", None)
    selected = diagram.stops[6]
    assert selected.stop_name == "同志社香里〔京阪バス〕"
    assert (selected.arrival_time, selected.departure_time) == ("17:12", "17:12")
    dwell = diagram.stops[9]
    assert dwell.stop_name == "三井団地〔京阪バス〕"
    assert (dwell.arrival_time, dwell.departure_time) == ("17:18", "17:20")
    assert [index for index, stop in enumerate(diagram.stops) if stop.stop_name == "三井〔京阪バス〕"] == [7, 10]
    assert not any(stop.arrival_is_estimated or stop.departure_is_estimated for stop in diagram.stops)


def test_diagram_request_uses_jst_and_unencoded_dgmpl(api, monkeypatch):
    class FixedDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz):
            assert tz == JST
            return cls(2026, 11, 7, 9, 3, tzinfo=datetime.timezone.utc).astimezone(tz)

    monkeypatch.setattr(bus_tracker, "datetime", SimpleNamespace(datetime=FixedDatetime))
    run(fetch_diagram(DGMPL))
    request = api["requests"][0]
    assert request.url.params == httpx.QueryParams({"mode": 4, "dgmpl": DGMPL, "dt": "202611071803"})
    assert request.headers["User-Agent"] == USER_AGENT


def test_diagram_cache_expires_after_30_seconds(api, monkeypatch):
    current = [100.0]
    monkeypatch.setattr(bus_tracker, "time", SimpleNamespace(monotonic=lambda: current[0]))
    first = run(fetch_diagram(DGMPL))
    current[0] = 129.9
    assert run(fetch_diagram(DGMPL)) is first
    assert len(api["requests"]) == 1
    current[0] = 130.0
    assert run(fetch_diagram(DGMPL)) is not first
    assert len(api["requests"]) == 2


def test_diagram_cache_is_per_bus(api):
    first = run(fetch_diagram(DGMPL))
    other = run(fetch_diagram("同志社香里:1:1:75120:17:53"))
    assert first is not other
    assert len(api["requests"]) == 2


def test_estimated_and_after_midnight_times_are_preserved(api):
    api["diagram"] = '''<ul class="lineul"><li class="lines hightlight">
        <p class="eki"><a>停留所&amp;入口<input type="hidden" value="wrong"></a></p>
        <div><p class="arr">△ 25:01 着</p><p class="dep">25:02 発</p></div>
        </li></ul>'''.encode()
    stop = run(fetch_diagram(DGMPL)).stops[0]
    assert stop.stop_name == "停留所&入口"
    assert stop.arrival_time == "25:01"
    assert stop.departure_time == "25:02"
    assert stop.arrival_is_estimated is True
    assert stop.departure_is_estimated is False


@pytest.mark.parametrize("html", [
    "<html>メンテナンス中</html>",
    '<ul class="lineul"></ul>',
    '<ul class="lineul"><li class="lines"><p class="eki">停留所</p></li></ul>',
    '<ul class="lineul"><li class="lines"><p class="eki"></p><p class="arr">17:01</p><p class="dep">17:02</p></li></ul>',
    '<ul class="lineul"><li class="lines"><p class="eki">停留所</p><p class="arr">17:99</p><p class="dep">17:02</p></li></ul>',
    '<ul class="lineul"><li class="lines"><p class="eki">停留所</p><p class="arr">-</p><p class="dep">-</p></li></ul>',
])
def test_invalid_diagram_is_not_cached(api, html):
    original = api["diagram"]
    api["diagram"] = html.encode()
    with pytest.raises(KeihanAPIError):
        run(fetch_diagram(DGMPL))
    api["diagram"] = original
    assert len(run(fetch_diagram(DGMPL)).stops) == 17
    assert len(api["requests"]) == 2


def test_diagram_http_error_is_not_cached(api):
    api["status"] = 503
    with pytest.raises(httpx.HTTPStatusError):
        run(fetch_diagram(DGMPL))
    api["status"] = 200
    assert len(run(fetch_diagram(DGMPL)).stops) == 17
    assert len(api["requests"]) == 2


def test_diagram_snapshot_saves_raw_response(api, tmp_path):
    run(fetch_diagram(DGMPL, snapshot_dir=tmp_path))
    saved = list(tmp_path.rglob("*.gz"))
    # dgmpl の「:」はファイル名に使えないので _ にする
    assert [p.name.split("_", 1)[1] for p in saved] == ["linediagram_同志社香里_1_1_75120_17_12.html.gz"]
    assert gzip.decompress(saved[0].read_bytes()) == api["diagram"]


# 2026-11-13 に実際に遅れていた便（[22] 寝屋川市駅行）の HTML。PC 版の見出しまでを抜き出した
DELAYED_HTML = """
<div class="pc divbusstate ">
    <input type="hidden" class="bus_key" value="7512:9:1271:7512" />
    <div class="fl bsno">1</div>
    <div class="fl bsdtl">
        <div class="bsul first">
            <span class="bsmidashi">定刻：</span>
            21:11
            <span class="bsmidashi">(約1分遅れ)</span>
            <span class="signal_status">受信中</span>
        </div>
        <ul class="bsul first">
            <li class="more_min delay">約9分後に到着</li>
            <li class="time">
                21:12 到着予定
            </li>
        </ul>
    </div>
</div>
"""


def test_delay_read_from_saved_response(api):
    """保存した応答には、到着済・定時運行中・未受信の便がある"""
    buses = run(get_khbus_info("同志社香里", 1)).body.busstates
    assert [(b.scheduled_time, b.delay_text, b.delay_minutes) for b in buses] == [
        ("17:53", "到着済", None),
        ("18:13", "定時運行中", 0),
        ("18:33", "定時運行中", 0),
        ("18:53", "定時運行中", 0),
        ("19:12", None, None),
        ("19:32", None, None),
        ("19:52", None, None),
    ]


def test_delay_read_from_delayed_bus(api):
    bus = api["location"]["body"]["busstates"][0]
    bus["html"] = DELAYED_HTML
    bus["busstateprms"] = bus["busstateprms"].rsplit(":", 2)[0] + ":約9分後に到着:21__________12 到着予定"
    bus = run(get_khbus_info("同志社香里", 2)).body.busstates[0]
    assert (bus.scheduled_time, bus.delay_text, bus.delay_minutes) == ("21:11", "約1分遅れ", 1)
    assert bus.busstateprms.timetable == "21:12 到着予定"


@pytest.mark.parametrize("text, minutes", [
    ("約1分遅れ", 1),
    ("約15分遅れ", 15),
    ("約1時間遅れ", 60),
    ("約1時間5分遅れ", 65),
])
def test_delay_text_formats(text, minutes):
    from keihan_tracker.bus.parse import parse_bus_delay
    html = DELAYED_HTML.replace("約1分遅れ", text)
    assert parse_bus_delay(html).delay_minutes == minutes


def test_delay_read_from_mobile_html():
    from keihan_tracker.bus.parse import parse_bus_delay
    html = """<dd class="sp tableDetail divbusstate"><ul class="bsul"><li class="busstate_header">
        <div class="fl bsno">1</div><span class="bsmidashi">定刻：</span> 21:11
        <span class="bsmidashi">(約3分遅れ)</span><span class="signal_status">受信中</span></li></ul></dd>"""
    delay = parse_bus_delay(None, html)
    assert (delay.scheduled_time, delay.delay_minutes) == ("21:11", 3)


def test_unknown_delay_text_warns_and_keeps_text():
    from keihan_tracker.bus.parse import parse_bus_delay
    with pytest.warns(UserWarning, match="遅れの表記"):
        delay = parse_bus_delay(DELAYED_HTML.replace("約1分遅れ", "運休"))
    assert (delay.delay_text, delay.delay_minutes) == ("運休", None)
