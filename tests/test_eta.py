"""signage の保存済み出力と、移した境界値テストで到着予測を確かめる。"""

import dataclasses
import datetime
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from keihan_tracker import (
    ActiveTrainData, TrainData, KHState, KHTrackerSync, StopStationData,
    EtaPolicy, EtaResult, EtaHidden, estimate_arrival,
)
from keihan_tracker.train import tracker as tracker_module
from keihan_tracker.train.state import JST

REFERENCE = json.loads(gzip.decompress((Path(__file__).parent / "fixtures/eta/signage.json.gz").read_bytes()))
NOW = datetime.datetime(2026, 11, 10, 12, tzinfo=JST)


def active(station, *, delay=0, remaining=20, growing=False, staying=0, at_start=False, row=90, direction="up"):
    train = Mock(spec=ActiveTrainData)
    stop = StopStationData(station=station, time=NOW + datetime.timedelta(minutes=remaining - delay))
    train.has_dia = True
    train.get_stop.side_effect = lambda key: stop if key == station else None
    train.stop_stations = [stop]
    train.current_or_next_stop = station
    train.is_delay_growing.return_value = growing
    train.staying_time = datetime.timedelta(minutes=staying)
    train.is_at_start_station = at_start
    train.delay_minutes = delay
    train.line = "京阪本線・鴨東線"
    train.location_row = row
    train.direction = direction
    train.master = KHState()
    return train


def test_all_saved_active_trains_and_stops_match_signage(saved_tracker, request):
    snapshot = request.node.callspec.id
    cases = [case for case in REFERENCE["cases"] if case["snapshot"] == snapshot]
    expected_count = sum(len(train.stop_stations) for train in saved_tracker.active_trains.values())
    assert len(cases) == expected_count
    differences = []
    for case in cases:
        train = saved_tracker.trains[case["wdf"]]
        result = estimate_arrival(train, saved_tracker.stations[case["station"]], now=datetime.datetime.fromisoformat(case["now"]))
        actual = result.time.isoformat() if result.time else None
        if actual != case["expected"]:
            differences.append({**case, "library": actual, "reason": result.reason.value if result.reason else None})
    assert differences == [case for case in REFERENCE["differences"] if case["snapshot"] == snapshot]


def test_holiday_fixture_has_no_active_trains(holiday_tracker):
    summary = next(s for s in REFERENCE["snapshots"] if s["id"] == "holiday")
    assert summary["active"] == len(holiday_tracker.active_trains) == 0
    assert summary["cases"] == 0
    # 土休日の走行列車は、保存された別日の土休日位置情報との比較で補っている
    weekend = [s for s in REFERENCE["snapshots"] if s["id"] == "wayback-20250420014659"]
    assert weekend[0]["active"] > 0 and weekend[0]["cases"] > 0


@pytest.mark.parametrize("remaining,delay,visible", REFERENCE["window_cases"])
def test_signage_window_boundaries(tracker, remaining, delay, visible):
    station = tracker.station(18)
    result = estimate_arrival(active(station, delay=delay, remaining=remaining), station, now=NOW)
    assert (result.time is not None) is visible
    assert result.reason is (None if visible else EtaHidden.OVER_120 if delay >= 120 else EtaHidden.OUT_OF_WINDOW)


@pytest.mark.parametrize("remaining,delay,corrected", REFERENCE["correction_cases"])
def test_signage_correction(tracker, remaining, delay, corrected):
    station = tracker.station(18)
    train = active(station, delay=delay, remaining=remaining)
    result = estimate_arrival(train, station, now=NOW)
    assert result.time == train.get_stop(station).time + datetime.timedelta(minutes=round(corrected))


@pytest.mark.parametrize("seconds,delay,visible", REFERENCE["arrival_window_cases"])
def test_signage_seconds_boundaries(tracker, seconds, delay, visible):
    station = tracker.station(18)
    result = estimate_arrival(active(station, delay=delay, remaining=seconds / 60), station, now=NOW)
    assert (result.time is not None) is visible


@pytest.mark.parametrize("delay,growing,unreliable", REFERENCE["growing_cases"])
def test_signage_growing_delay(tracker, delay, growing, unreliable):
    station = tracker.station(18)
    result = estimate_arrival(active(station, delay=delay, growing=growing), station, now=NOW)
    assert (result.time is None) is unreliable
    if delay >= 120:
        assert result.reason is EtaHidden.OVER_120
    elif growing:
        assert result.reason is EtaHidden.GROWING


@pytest.mark.parametrize("at_start,unreliable", REFERENCE["stuck_cases"])
def test_signage_start_station_exemption(tracker, at_start, unreliable):
    station = tracker.station(18)
    result = estimate_arrival(active(station, delay=3, staying=16, at_start=at_start), station, now=NOW)
    assert (result.time is None) is unreliable
    assert result.reason is (EtaHidden.STUCK if unreliable else None)


@pytest.mark.parametrize("direction,row,other_direction,other_row,growing,staying,delay,blocked", REFERENCE["blocked_cases"])
def test_signage_blocked_ahead(tracker, direction, row, other_direction, other_row, growing, staying, delay, blocked):
    station = tracker.station(18)
    train = active(station, delay=3, direction=direction, row=row)
    other = active(station, delay=delay, direction=other_direction, row=other_row, growing=growing, staying=staying)
    train.master._trains = {1: train, 2: other}
    result = estimate_arrival(train, station, now=NOW)
    assert (result.time is None) is blocked
    assert result.reason is (EtaHidden.BLOCKED_AHEAD if blocked else None)


def test_policy_overrides(tracker):
    station = tracker.station(18)
    train = active(station, delay=6, remaining=30)
    assert estimate_arrival(train, station, now=NOW).time == NOW + datetime.timedelta(minutes=27)
    assert estimate_arrival(train, station, now=NOW, policy=EtaPolicy(discount_rate=0)).time == NOW + datetime.timedelta(minutes=30)
    assert estimate_arrival(train, station, now=NOW, policy=EtaPolicy(discount_max_delay=5)).time == NOW + datetime.timedelta(minutes=30)
    assert estimate_arrival(train, station, now=NOW, policy=EtaPolicy(windows=())).reason is EtaHidden.OUT_OF_WINDOW
    train.master._trains[1] = active(station, row=80, growing=True)
    assert estimate_arrival(train, station, now=NOW).reason is EtaHidden.BLOCKED_AHEAD
    assert estimate_arrival(train, station, now=NOW, policy=EtaPolicy(check_blocked_ahead=False)).time is not None
    train.master._trains.clear()
    train.staying_time = datetime.timedelta(minutes=16)
    assert estimate_arrival(train, station, now=NOW, policy=EtaPolicy(stuck_minutes=16)).time is not None
    estimate_arrival(train, station, now=NOW, policy=EtaPolicy(growing_ratio=0.95))
    train.is_delay_growing.assert_called_with(ratio=0.95)


@pytest.mark.parametrize("remaining,delay,corrected", [(-5, 3, 3), (5, 3, 2), (15, 4, 2)])
def test_negative_remaining_and_rounding(tracker, remaining, delay, corrected):
    station = tracker.station(18)
    train = active(station, delay=delay, remaining=remaining)
    result = estimate_arrival(train, station, now=NOW)
    assert result.time == train.get_stop(station).time + datetime.timedelta(minutes=corrected)


def test_invalid_inputs_have_reasons(tracker):
    station = tracker.station(18)
    train = active(station)
    with pytest.raises(ValueError, match="タイムゾーン"):
        estimate_arrival(train, station, now=NOW.replace(tzinfo=None))
    train.has_dia = False
    assert estimate_arrival(train, station, now=NOW).reason is EtaHidden.NO_TIMETABLE
    train.has_dia = True
    assert estimate_arrival(train, tracker.station(19), now=NOW).reason is EtaHidden.NOT_STOPPING
    train.get_stop(station).time = None
    assert estimate_arrival(train, station, now=NOW).reason is EtaHidden.NO_TIME
    train.get_stop(station).time = NOW
    train.current_or_next_stop = None
    assert estimate_arrival(train, station, now=NOW).reason is EtaHidden.UNKNOWN_POSITION
    train.current_or_next_stop = tracker.station(19)
    train.stop_stations.append(StopStationData(station=tracker.station(19), time=NOW))
    assert estimate_arrival(train, station, now=NOW).reason is EtaHidden.PASSED


def test_scheduled_and_finished_trains(tracker):
    station = tracker.station(18)
    train = TrainData(master=KHState(), wdf=1, date=NOW.date(), has_premiumcar=False, train_formation=None,
                      route_stations=[StopStationData(station=station, time=NOW)])
    assert estimate_arrival(train, station, now=NOW) == EtaResult(NOW, None)
    train.history.append(object())
    assert estimate_arrival(train, station, now=NOW).reason is EtaHidden.PASSED


def test_tracker_eta_uses_jst_and_sync_delegates(tracker, monkeypatch):
    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == JST
            return NOW
    monkeypatch.setattr(tracker_module, "datetime", SimpleNamespace(datetime=Clock, timedelta=datetime.timedelta))
    station = tracker.station(18)
    train = active(station)
    policy = EtaPolicy(discount_rate=0)
    expected = estimate_arrival(train, station, now=NOW, policy=policy)
    assert tracker.eta(train, station, policy) == expected
    with KHTrackerSync() as sync:
        assert sync.eta(train, station, policy) == expected


def test_policy_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        EtaPolicy().discount_rate = 0


@pytest.mark.parametrize("case", ["branch-train", "branch-blocker", "branch-station"])
def test_blocking_has_the_same_line_scope_as_signage(tracker, case):
    station = tracker.station(51 if case == "branch-station" else 18)
    train = active(station)
    other = active(station, row=80, growing=True)
    train.master._trains[1] = other
    if case == "branch-train":
        train.line = "交野線"
    elif case == "branch-blocker":
        other.line = "宇治線"
    assert estimate_arrival(train, station, now=NOW).time is not None
