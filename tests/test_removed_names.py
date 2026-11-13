"""
v2.9 で非推奨にした名前は v3 で消えた。消えた名前が AttributeError / TypeError になることと、
新しい名前（status など）の確かめ。
"""

import pytest

from keihan_tracker import ActiveTrainData, KHTracker, TrainData, TrainPosition, TrainStatus
from keihan_tracker.keihan_train.tracker import TrainHistoryRecord


def _active_train(tracker) -> ActiveTrainData:
    return next(iter(tracker.active_trains.values()))


@pytest.mark.parametrize("name", ["next_station", "next_stop_station", "is_stopping", "is_at_station"])
def test_active_train_old_names_removed(tracker, name):
    assert not hasattr(ActiveTrainData, name)
    with pytest.raises(AttributeError):
        getattr(_active_train(tracker), name)


@pytest.mark.parametrize("name", ["stopping_station", "is_at_station"])
def test_history_record_old_names_removed(tracker, name):
    assert not hasattr(TrainHistoryRecord, name)
    record = next(r for t in tracker.active_trains.values() for r in t.history)
    with pytest.raises(AttributeError):
        getattr(record, name)


def test_wdfBlockNo_removed(tracker):
    train = _active_train(tracker)
    assert not hasattr(TrainData, "wdfBlockNo")
    with pytest.raises(AttributeError):
        train.wdfBlockNo
    # コンストラクタの別名も無い
    with pytest.raises(TypeError):
        TrainData(master=tracker, wdfBlockNo=1, date=train.date, has_premiumcar=None, train_formation=None)
    with pytest.raises(TypeError):
        ActiveTrainData(
            master=tracker, wdfBlockNo=9002, date=train.date, train_number="9002",
            train_type=train.train_type, direction="up", destination=None, is_special=False, cars=7,
            delay_text=train.delay_text, location_col=10, location_row=10,
        )


def test_find_trains_old_arguments_removed(tracker):
    with pytest.raises(TypeError):
        tracker.find_trains(next_stop_station=None)
    with pytest.raises(TypeError):
        tracker.find_trains(is_stopping=True)
    with pytest.raises(TypeError):
        tracker.state.find_trains(next_stop_station=None)
    with pytest.raises(TypeError):
        tracker.state.find_trains(is_stopping=True)


def test_tracker_old_members_removed(tracker):
    for name in ["regist_dia", "fetch_filelist", "file_list"]:
        assert not hasattr(KHTracker, name)
        with pytest.raises(AttributeError):
            getattr(tracker, name)


def test_position_between_stations(tracker):
    """1096は石清水八幡宮→中書島を走行中（上り）"""
    train = tracker.trains[1096]
    assert isinstance(train, ActiveTrainData)
    position = train.position
    assert isinstance(position, TrainPosition)
    assert position.station is None
    assert position.line == "京阪本線・鴨東線"
    assert position.behind.station_number < position.ahead.station_number  # 本線の上りは番号が増える
    assert train.stopped_at is None
    assert train.next_stop == train.current_or_next_stop


def test_next_stop_excludes_stopped_station(tracker):
    """停車中なら next_stop はその次の停車駅。current_or_next_stop は停車中の駅"""
    stopped = [t for t in tracker.active_trains.values() if t.stopped_at is not None]
    assert stopped
    for train in stopped:
        assert train.current_or_next_stop == train.stopped_at == train.position.station
        stops = [s.station for s in train.stop_stations]
        index = stops.index(train.stopped_at)
        expected = stops[index + 1] if index + 1 < len(stops) else None
        assert train.next_stop == expected, train.wdf


# ---------------------------------------------------------------- status

def _by_status(tracker) -> dict[TrainStatus, list[ActiveTrainData]]:
    found: dict[TrainStatus, list[ActiveTrainData]] = {s: [] for s in TrainStatus}
    for train in tracker.active_trains.values():
        found[train.status].append(train)
    return found


def test_status_stopped(tracker):
    trains = _by_status(tracker)[TrainStatus.STOPPED]
    assert trains
    for train in trains:
        assert train.stopped_at is not None
        assert train.current_or_next_stop is not None


def test_status_passing(tracker):
    """駅の座標にいるが、停車駅ではない（1094 は鳥羽街道を通過中）"""
    trains = _by_status(tracker)[TrainStatus.PASSING]
    assert tracker.trains[1094] in trains
    for train in trains:
        assert train.position.station is not None
        assert train.stopped_at is None
        assert train.current_or_next_stop is not None


def test_status_between(tracker):
    trains = _by_status(tracker)[TrainStatus.BETWEEN]
    assert tracker.trains[1096] in trains
    for train in trains:
        assert train.position.station is None
        assert train.stopped_at is None
        assert train.current_or_next_stop is not None


def test_status_unknown_without_dia(tracker):
    """停車駅が分からない（ダイヤが無い）なら UNKNOWN。v2 の is_stopping が None だったのと同じ"""
    base = tracker.trains[1096]
    train = ActiveTrainData(
        master=tracker, wdf=9003, date=base.date, train_number="9003",
        train_type=base.train_type, direction=base.direction, destination=None, is_special=False, cars=7,
        delay_text=base.delay_text, location_col=base.location_col, location_row=base.location_row,
    )
    assert not train.has_dia
    assert train.current_or_next_stop is None
    assert train.status is TrainStatus.UNKNOWN
    assert train.is_at_start_station is None


def test_find_trains_by_status(tracker):
    for status, trains in _by_status(tracker).items():
        assert tracker.find_trains(status=status) == trains
        assert tracker.state.find_trains(status=status) == trains


def test_find_trains_returns_list(tracker):
    assert isinstance(tracker.find_trains(), list)
    active = tracker.find_trains(active=True)
    assert isinstance(active, list)
    assert all(isinstance(t, ActiveTrainData) for t in active)
    assert isinstance(tracker.state.find_trains(active=True), list)
