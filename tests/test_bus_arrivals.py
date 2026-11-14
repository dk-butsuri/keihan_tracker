"""接近情報を BusArrival に直す（to_arrivals・BusClient.arrivals）。"""

import datetime

import httpx

from keihan_tracker import BusArrival, BusClient, BusStatus, parse_busstate, to_arrivals
from keihan_tracker.bus import client as client_module
from keihan_tracker.bus.schemas import Body, BusLocationResponse, BusState, BusStatePrms, Head
from keihan_tracker.train.state import JST
from conftest import load_fixture_bytes, run


def _fixture() -> BusLocationResponse:
    return parse_busstate(load_fixture_bytes("bus/busstateupd.json").decode(), "京阪香里園", 1)


def _response(created: str, *buses: BusState) -> BusLocationResponse:
    return BusLocationResponse(head=Head(errorcode=0, objid=""), body=Body(datetimeStr=created, busstates=list(buses)))


def _bus(order=1, timetable="21:12 到着予定", scheduled="21:11", delay_text="約1分遅れ", delay=1) -> BusState:
    prms = BusStatePrms(
        order=order, lat=34.0, lon=135.0, heading=0, guid="", stop_id=0, platform_index=0,
        route="[22]", destination="京阪香里園行", via="三井団地", status="", timetable=timetable,
    )
    return BusState(busstateprms=prms, scheduled_time=scheduled, delay_text=delay_text, delay_minutes=delay)


def at(hour, minute, day=7):
    return datetime.datetime(2026, 11, day, hour, minute, tzinfo=JST)


def test_fixture_statuses_and_times():
    arrivals = to_arrivals(_fixture())
    assert len(arrivals) == 7
    first, second, last = arrivals[0], arrivals[1], arrivals[-1]
    assert isinstance(first, BusArrival)
    assert first.status == BusStatus.ARRIVED
    assert first.scheduled == at(17, 53) and first.expected == at(17, 53)
    assert first.delay_minutes is None
    assert second.status == BusStatus.TRACKED
    assert second.delay_minutes == 0 and second.expected == at(18, 13)
    # 位置を受信していない便は「定刻 19:52」だけ
    assert last.status == BusStatus.NOT_TRACKED
    assert last.scheduled == at(19, 52) and last.expected is None
    assert first.route == "22"
    assert first.destination == "京阪香里園行" and first.via == "三井団地"
    assert first.dgmpl == "京阪香里園:1:1:75120:17:53"
    assert first.raw.busstateprms.order == first.order
    assert [a.order for a in arrivals] == sorted(a.order for a in arrivals)


def test_delayed_bus():
    (bus,) = to_arrivals(_response("2026/11/13 21:05現在", _bus()))
    assert bus.status == BusStatus.TRACKED
    assert bus.delay_minutes == 1
    assert bus.scheduled == at(21, 11, 13) and bus.expected == at(21, 12, 13)


def test_unknown_text_is_unknown():
    (bus,) = to_arrivals(_response("2026/11/13 21:05現在", _bus(delay_text="運休", delay=None)))
    assert bus.status == BusStatus.UNKNOWN
    assert bus.delay_minutes is None


def test_times_after_midnight_get_next_day():
    (bus,) = to_arrivals(_response("2026/11/07 23:50現在", _bus(timetable="00:12 到着予定", scheduled="00:10")))
    assert bus.scheduled == at(0, 10, 8) and bus.expected == at(0, 12, 8)


def test_times_before_midnight_seen_after_midnight():
    (bus,) = to_arrivals(_response("2026/11/08 00:05現在", _bus(timetable="23:59 到着予定", scheduled="23:58")))
    assert bus.scheduled == at(23, 58, 7)


def test_unreadable_created_time_gives_no_datetimes():
    (bus,) = to_arrivals(_response("", _bus()))
    assert bus.scheduled is None and bus.expected is None
    assert bus.status == BusStatus.TRACKED


def test_client_arrivals(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=load_fixture_bytes("bus/busstateupd.json"))

    monkeypatch.setattr(client_module, "AsyncClient",
                        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs))

    async def check():
        async with BusClient() as client:
            return await client.arrivals("京阪香里園", 1)

    arrivals = run(check())
    assert [a.status for a in arrivals][:2] == [BusStatus.ARRIVED, BusStatus.TRACKED]
