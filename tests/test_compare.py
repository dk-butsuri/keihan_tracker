"""
比較（v3 step 4）。種別の順位、列車の ==・hash、停車駅の順序と引き算、station in train。
"""

import dataclasses
import datetime
import itertools

import pytest

from keihan_tracker import ActiveTrainData, StationData, StopStationData, TrainData, TrainType

EXTRA = TrainType.EXTRA_TRAIN
# 低い順
RANKED = [t for t in TrainType if t is not EXTRA]
OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


# ---------------------------------------------------------------- TrainType

def test_train_type_rank_order_is_definition_order():
    assert [t.value for t in RANKED] == [
        "普通", "区間急行", "準急", "通勤準急", "急行", "通勤急行", "深夜急行",
        "快速急行", "通勤快急", "特急", "ライナー", "快速特急 洛楽",
    ]
    for (i, a), (j, b) in itertools.product(enumerate(RANKED), repeat=2):
        assert (a < b) == (i < j)
        assert (a <= b) == (i <= j)
        assert (a > b) == (i > j)
        assert (a >= b) == (i >= j)


def test_train_type_rank_examples():
    assert TrainType.LTD_EXP > TrainType.LOCAL
    assert TrainType.LTD_EXP >= TrainType.EXPRESS
    assert TrainType.LOCAL < TrainType.SEMI_EXP
    assert not TrainType.RAPID_LTD_EXP < TrainType.LTD_EXP
    # 文字コード順では 快速急行 < 急行 だが、順位では 急行 < 快速急行
    assert TrainType.EXPRESS < TrainType.RAPID_EXP
    assert max(TrainType.LOCAL, TrainType.RAPID_LTD_EXP, TrainType.EXPRESS) is TrainType.RAPID_LTD_EXP
    assert sorted([TrainType.LTD_EXP, TrainType.LOCAL, TrainType.EXPRESS]) == [
        TrainType.LOCAL, TrainType.EXPRESS, TrainType.LTD_EXP,
    ]


def test_train_type_eq_and_hash_unchanged():
    assert TrainType.LTD_EXP == "特急"
    assert TrainType.LTD_EXP == TrainType.LTD_EXP
    assert TrainType.LTD_EXP != TrainType.LOCAL
    assert hash(TrainType.LTD_EXP) == hash("特急")
    assert {TrainType.LTD_EXP: 1}["特急"] == 1
    assert TrainType("特急") is TrainType.LTD_EXP


@pytest.mark.parametrize("op", OPS)
def test_train_type_extra_train_is_nan(op):
    compare = OPS[op]
    for t in TrainType:
        assert compare(EXTRA, t) is False
        assert compare(t, EXTRA) is False
        assert compare(EXTRA, t.value) is False
        assert compare(t.value, EXTRA) is False
    # NaN と同じく、自分自身とも <= >= が成り立たない。== は今のまま
    assert EXTRA == EXTRA


def test_train_type_extra_train_breaks_sorted():
    """臨時列車は NaN と同じ扱いなので、sorted() に混ぜると並びが崩れる（docstring に書いてある注意）"""
    types = [TrainType.LTD_EXP, TrainType.LOCAL, EXTRA, TrainType.EXPRESS, TrainType.RAPID_EXP, TrainType.SEMI_EXP]
    assert sorted(types) != sorted(t for t in types if t is not EXTRA) + [EXTRA]
    assert "臨時列車は NaN と同じ扱い" in TrainType.__lt__.__doc__
    assert "sorted()" in TrainType.__lt__.__doc__


@pytest.mark.parametrize("op", OPS)
def test_train_type_compares_with_str_by_rank(op):
    """str が相手でも、str の比較（文字コード順）にすり替わらず、順位で比べる"""
    compare = OPS[op]
    for a, b in itertools.product(RANKED, repeat=2):
        assert compare(a, b.value) == compare(a, b)
        assert compare(a.value, b) == compare(a, b)


def test_train_type_str_not_a_train_type():
    with pytest.raises(ValueError):
        TrainType.LTD_EXP < "なにこれ"
    with pytest.raises(ValueError):
        TrainType.LTD_EXP >= "なにこれ"
    with pytest.raises(ValueError):
        "なにこれ" > TrainType.LTD_EXP
    with pytest.raises(ValueError):
        TrainType.LTD_EXP > ""


@pytest.mark.parametrize("other", [1, 1.5, None, "特急".encode(), ("特急",), object()])
@pytest.mark.parametrize("op", OPS)
def test_train_type_compare_with_other_types_raises(op, other):
    with pytest.raises(TypeError):
        OPS[op](TrainType.LTD_EXP, other)
    with pytest.raises(TypeError):
        OPS[op](other, TrainType.LTD_EXP)


# ---------------------------------------------------------------- 列車の == / hash

def _with_dia(tracker) -> list[TrainData]:
    return [t for t in tracker.trains.values() if t.has_dia]


def test_train_eq_by_date_and_wdf(tracker):
    train = next(iter(tracker.trains.values()))
    same = TrainData(master=tracker, wdf=train.wdf, date=train.date, has_premiumcar=None, train_formation=None)
    assert train == same and not train != same
    assert hash(train) == hash(same)
    # 日付か WDF番号が違えば別の列車
    assert train != TrainData(master=tracker, wdf=train.wdf + 100000, date=train.date, has_premiumcar=None, train_formation=None)
    other_day = train.date + datetime.timedelta(days=1)
    assert train != TrainData(master=tracker, wdf=train.wdf, date=other_day, has_premiumcar=None, train_formation=None)
    # 同じ wdf でも中身（ダイヤ・遅延）が違うかどうかは見ない
    assert train == TrainData(master=tracker, wdf=train.wdf, date=train.date, has_premiumcar=True, train_formation=3, delay_minutes=9)


def test_train_eq_other_types_not_implemented(tracker):
    train = next(iter(tracker.trains.values()))
    assert train.__eq__(train.wdf) is NotImplemented
    assert train.__eq__(None) is NotImplemented
    assert train != train.wdf
    assert train != (train.date, train.wdf)
    assert train is not None and train != None  # noqa: E711


def test_active_train_equals_its_inactivated_train(tracker):
    active = next(t for t in tracker.active_trains.values() if t.has_dia)
    inactive = active.inactivate()
    assert type(inactive) is TrainData
    assert active == inactive and inactive == active
    assert hash(active) == hash(inactive)
    assert len({active, inactive}) == 1
    assert {active: "x"}[inactive] == "x"


def test_train_in_set_and_dict_removes_duplicates(tracker):
    trains = list(tracker.trains.values())
    old_and_new = trains + [t.inactivate() if isinstance(t, ActiveTrainData) else t for t in trains]
    assert len(set(old_and_new)) == len(trains)
    assert len({t: None for t in old_and_new}) == len(trains)
    # 別の列車は区別される
    assert len(set(trains)) == len(trains)


def test_train_eq_hash_not_overridden_by_subclass_or_dataclass():
    """slots の dataclass（eq=False）でも、サブクラスが TrainData の __eq__ / __hash__ を引き継ぐ"""
    assert ActiveTrainData.__eq__ is TrainData.__eq__
    assert ActiveTrainData.__hash__ is TrainData.__hash__
    assert TrainData.__hash__ is not None
    assert dataclasses.is_dataclass(TrainData) and dataclasses.is_dataclass(ActiveTrainData)


def test_train_has_no_ordering(tracker):
    a, b = list(tracker.trains.values())[:2]
    for op in OPS.values():
        with pytest.raises(TypeError):
            op(a, b)


def test_train_state_updates_keep_equality(tracker):
    """走行中の列車が運行終了すると別のオブジェクトになるが、同じ列車として扱える"""
    active = next(t for t in tracker.active_trains.values())
    assert tracker.trains[active.wdf] is active
    assert tracker.trains[active.wdf] == active.inactivate()
    assert active.inactivate() in [active]
    assert [active].index(active.inactivate()) == 0


# ---------------------------------------------------------------- 停車駅の比較・引き算

def _stop(tracker, number: int, time: datetime.datetime | None) -> StopStationData:
    return StopStationData(station=tracker.stations[number], time=time)


def test_stop_ordering_by_time(tracker):
    t0 = datetime.datetime(2026, 10, 6, 21, 0)
    early, late = _stop(tracker, 4, t0), _stop(tracker, 21, t0 + datetime.timedelta(minutes=30))
    assert early < late and early <= late
    assert late > early and late >= early
    assert not late < early and not late <= early
    assert not early > late and not early >= late


def test_stop_ordering_ignores_station_and_train(tracker):
    """別の駅・別の列車の停車駅とも時刻で比べられる"""
    trains = _with_dia(tracker)[:30]
    stops = [s for t in trains for s in t.stop_stations if s.time is not None]
    assert len({s.train.wdf for s in stops}) > 1
    for a, b in itertools.product(stops[:40], repeat=2):
        assert (a < b) == (a.time < b.time)
        assert (a <= b) == (a.time <= b.time)
        assert (a > b) == (a.time > b.time)
        assert (a >= b) == (a.time >= b.time)


def test_stop_same_time_is_not_equal(tracker):
    t0 = datetime.datetime(2026, 10, 6, 21, 0)
    a, b = _stop(tracker, 4, t0), _stop(tracker, 4, t0)
    assert a <= b and b <= a
    assert not a < b and not a > b
    assert a != b and a == a
    assert len({a, b}) == 2
    assert "a != b" in StopStationData.__doc__ and "時刻だけを見る" in StopStationData.__doc__


@pytest.mark.parametrize("op", OPS)
def test_stop_without_time_is_nan(tracker, op):
    compare = OPS[op]
    timed = _stop(tracker, 4, datetime.datetime(2026, 10, 6, 21, 0))
    none = _stop(tracker, 21, None)
    assert compare(none, timed) is False
    assert compare(timed, none) is False
    assert compare(none, _stop(tracker, 5, None)) is False
    assert compare(none, none) is False


@pytest.mark.parametrize("op", OPS)
def test_stop_compare_with_other_types(tracker, op):
    stop = _stop(tracker, 4, datetime.datetime(2026, 10, 6, 21, 0))
    with pytest.raises(TypeError):
        OPS[op](stop, stop.time)
    with pytest.raises(TypeError):
        OPS[op](stop, 1)
    method = {"<": "__lt__", "<=": "__le__", ">": "__gt__", ">=": "__ge__"}[op]
    assert getattr(stop, method)(stop.time) is NotImplemented


def test_stop_subtraction(tracker):
    t0 = datetime.datetime(2026, 10, 6, 21, 0)
    a = _stop(tracker, 4, t0)
    b = _stop(tracker, 21, t0 + datetime.timedelta(minutes=34))
    assert b - a == datetime.timedelta(minutes=34)
    assert a - b == -datetime.timedelta(minutes=34)
    assert a - a == datetime.timedelta(0)
    assert isinstance(b - a, datetime.timedelta)


def test_stop_subtraction_between_trains(tracker):
    """同じ列車なら所要時間、別の列車なら何分後か"""
    train = next(t for t in _with_dia(tracker) if len([s for s in t.stop_stations if s.time]) >= 2)
    timed = [s for s in train.stop_stations if s.time]
    assert timed[-1] - timed[0] == timed[-1].time - timed[0].time
    assert (timed[-1] - timed[0]) >= datetime.timedelta(0)


def test_stop_subtraction_none(tracker):
    timed = _stop(tracker, 4, datetime.datetime(2026, 10, 6, 21, 0))
    none = _stop(tracker, 21, None)
    assert timed - none is None
    assert none - timed is None
    assert none - none is None
    # 始発駅は時刻が None
    start = next(s for t in _with_dia(tracker) for s in t.route_stations if s.is_start)
    assert start.time is None
    assert timed - start is None


def test_stop_subtraction_other_types(tracker):
    stop = _stop(tracker, 4, datetime.datetime(2026, 10, 6, 21, 0))
    assert stop.__sub__(stop.time) is NotImplemented
    with pytest.raises(TypeError):
        stop - stop.time
    with pytest.raises(TypeError):
        stop - 1
    with pytest.raises(TypeError):
        stop.time - stop


def test_stop_eq_is_identity(tracker):
    train = _with_dia(tracker)[0]
    stops = train.stop_stations
    assert stops[0] == stops[0]
    assert stops[0] != stops[1]
    assert stops.index(stops[1]) == 1


# ---------------------------------------------------------------- station in train

def test_station_in_train(tracker):
    train = next(t for t in tracker.active_trains.values() if any(not s.is_stop for s in t.route_stations))
    stopped = {s.station for s in train.stop_stations}
    passed = {s.station for s in train.route_stations if not s.is_stop}
    assert stopped and passed
    for station in stopped:
        assert station in train
    for station in passed:
        assert station not in train
    # 経路にない駅
    outside = [s for s in tracker.stations.values() if s not in stopped and s not in passed]
    assert outside
    for station in outside:
        assert station not in train


def test_station_in_train_matches_get_stop(tracker):
    for train in _with_dia(tracker)[:50]:
        for station in tracker.stations.values():
            assert (station in train) == (train.get_stop(station) is not None)


def test_station_in_inactive_train(tracker):
    active = next(t for t in tracker.active_trains.values() if t.has_dia)
    inactive = active.inactivate()
    for station in tracker.stations.values():
        assert (station in inactive) == (station in active)


def test_station_in_train_without_dia_is_false(tracker):
    train = TrainData(master=tracker, wdf=1, date=datetime.date(2026, 10, 6), has_premiumcar=None, train_formation=None)
    assert not train.has_dia
    for station in tracker.stations.values():
        assert station not in train   # DiaNotFoundError は出さない


def test_other_than_station_in_train_is_false(tracker):
    train = next(t for t in tracker.active_trains.values() if t.has_dia)
    stop = train.stop_stations[0]
    for value in [stop, stop.station.station_number, stop.station.station_name.ja, None, train]:
        assert (value in train) is False
    assert isinstance(stop.station, StationData)
