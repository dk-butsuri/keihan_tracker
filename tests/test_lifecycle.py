"""取得結果・終了処理・監視ループと同期版。通信や実際の待ち時間は使わない。"""

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from keihan_tracker import KHTracker, KHTrackerSync, KHClient, FetchResult, KeihanAPIError
from keihan_tracker.train import tracker as tracker_module
from conftest import load_fixture, run


def test_fetch_result_keeps_applying_unchanged_data(fresh_tracker, api, monkeypatch):
    assert run(fresh_tracker.fetch_pos()) is FetchResult.UPDATED
    apply = Mock(wraps=fresh_tracker.state.apply_positions)
    monkeypatch.setattr(fresh_tracker.state, "apply_positions", apply)
    assert run(fresh_tracker.fetch_pos()) is FetchResult.UNCHANGED
    apply.assert_called_once()
    data = load_fixture("trainPositionList.json")
    created = fresh_tracker.position_created_at + datetime.timedelta(minutes=1)
    data["fileCreatedTime"] = created.strftime("%Y%m%d%H%M%S")
    api.set_json("/zaisen-up/trainPositionList.json", data)
    assert run(fresh_tracker.fetch_pos()) is FetchResult.UPDATED
    fresh_tracker.rate_limit_interval = 30
    before = len(api.requests)
    assert run(fresh_tracker.fetch_pos()) is FetchResult.SKIPPED
    assert len(api.requests) == before


def test_client_and_tracker_close():
    async def check():
        client = KHClient()
        await client.aclose()
        assert client.web.is_closed
        tracker = KHTracker()
        with pytest.raises(ValueError):
            async with tracker as entered:
                assert entered is tracker
                raise ValueError("test")
        assert tracker.web.is_closed
    run(check())


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("error", [None, httpx.ConnectError("offline"), KeihanAPIError("broken")])
def test_watch_schedule_and_continue(sync, error, monkeypatch, caplog):
    clock = [100.0]
    waits = []
    outcomes = iter([error, None, None] if error else [None, None])

    def fetch():
        clock[0] += 4
        outcome = next(outcomes)
        if outcome:
            raise outcome
        return FetchResult.UPDATED

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(tracker_module, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    if sync:
        with KHTrackerSync() as tracker:
            monkeypatch.setattr(tracker, "fetch_pos", fetch)
            stream = tracker.watch(60, on_error="continue")
            assert next(stream) is FetchResult.UPDATED
            assert next(stream) is FetchResult.UPDATED
            stream.close()
    else:
        async def async_fetch():
            return fetch()

        async def async_sleep(seconds):
            sleep(seconds)

        monkeypatch.setattr(tracker_module.asyncio, "sleep", async_sleep)

        async def check():
            async with KHTracker() as tracker:
                monkeypatch.setattr(tracker, "fetch_pos", async_fetch)
                stream = tracker.watch(60, on_error="continue")
                assert await anext(stream) is FetchResult.UPDATED
                assert await anext(stream) is FetchResult.UPDATED
                await stream.aclose()
        run(check())
    assert waits == ([56, 56] if error else [56])
    assert bool(caplog.records) == bool(error)
    if error:
        assert caplog.records[0].name == "keihan_tracker"
        assert caplog.records[0].levelname == "WARNING"


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("error,on_error", [
    (httpx.ConnectError("offline"), "raise"),
    (KeihanAPIError("broken"), "raise"),
    (RuntimeError("bug"), "continue"),
])
def test_watch_propagates_errors(sync, error, on_error, monkeypatch):
    if sync:
        with KHTrackerSync() as tracker:
            monkeypatch.setattr(tracker, "fetch_pos", Mock(side_effect=error))
            with pytest.raises(type(error)):
                next(tracker.watch(on_error=on_error))
    else:
        async def check():
            async with KHTracker() as tracker:
                monkeypatch.setattr(tracker, "fetch_pos", AsyncMock(side_effect=error))
                with pytest.raises(type(error)):
                    await anext(tracker.watch(on_error=on_error))
        run(check())


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("kwargs", [{"interval": 29}, {"on_error": "ignore"}])
def test_watch_rejects_invalid_arguments(sync, kwargs):
    if sync:
        with KHTrackerSync() as tracker:
            with pytest.raises(ValueError):
                next(tracker.watch(**kwargs))
    else:
        async def check():
            async with KHTracker() as tracker:
                with pytest.raises(ValueError):
                    await anext(tracker.watch(**kwargs))
        run(check())


def test_sync_reuses_loop_and_delegates(api, monkeypatch):
    with KHTrackerSync() as tracker:
        run(tracker.client.aclose())
        tracker.client.web = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
        tracker.tracker.rate_limit_interval = 0
        loops = []
        original = tracker.tracker.fetch_pos

        async def fetch():
            loops.append(asyncio.get_running_loop())
            return await original()

        monkeypatch.setattr(tracker.tracker, "fetch_pos", fetch)
        assert tracker.fetch_pos() is FetchResult.UPDATED
        assert tracker.fetch_pos() is FetchResult.UNCHANGED
        assert loops == [tracker._loop, tracker._loop]
        assert tracker.state is tracker.tracker.state
        assert tracker.client is tracker.tracker.client
        assert tracker.trains == tracker.tracker.trains
        assert tracker.stations == tracker.tracker.stations
        assert tracker.active_trains == tracker.tracker.active_trains
        assert tracker.date == tracker.tracker.date
        assert tracker.position_created_at == tracker.tracker.position_created_at
        assert tracker.find_trains(active=True) == list(tracker.active_trains.values())
        assert tracker.delay_growing_trains() == tracker.tracker.delay_growing_trains()
        assert tracker.station("KH18") is tracker.stations[18]
    assert tracker._loop.is_closed()
    assert tracker.client.web.is_closed
    tracker.close()
    with pytest.raises(RuntimeError, match="閉じ"):
        tracker.fetch_pos()


def test_sync_inside_running_loop_has_helpful_error():
    tracker = KHTrackerSync()
    async def check():
        with pytest.raises(RuntimeError, match="KHTracker を await"):
            tracker.fetch_pos()
    try:
        run(check())
    finally:
        tracker.close()
