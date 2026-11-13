"""
v2.9 で足した v3 の名前と、v3 で削除する名前の非推奨警告。
"""

import warnings

import pytest

from keihan_tracker import ActiveTrainData, TrainPosition

from conftest import run


def _old(get):
    """非推奨の名前を読み、警告が出ることを確かめて値を返す"""
    with pytest.warns(DeprecationWarning, match="v3 で削除します"):
        return get()


def test_new_names_do_not_warn(saved_tracker):
    """新しい名前と、内部の処理（fetch_pos・駅の列車一覧）は警告を出さない"""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        for train in saved_tracker.trains.values():
            train.wdf
            if isinstance(train, ActiveTrainData):
                train.position, train.stopped_at, train.next_stop, train.current_or_next_stop
                train.is_at_start_station, train.estimated_delay_minutes, str(train)
                for record in train.history:
                    record.position
        for station in list(saved_tracker.stations.values())[:5]:
            station.arriving_trains, station.upcoming_trains
        saved_tracker.find_trains(active=True, is_stopping=True)


def test_old_names_warn_and_keep_values(saved_tracker):
    """旧名は警告を出し、v2.8 と同じ値を新しい名前から返す"""
    for train in saved_tracker.trains.values():
        assert _old(lambda: train.wdfBlockNo) == train.wdf
        if not isinstance(train, ActiveTrainData):
            continue
        position = train.position
        assert _old(lambda: train.next_station) == (position.station or position.ahead)
        assert _old(lambda: train.next_stop_station) == train.current_or_next_stop
        assert _old(lambda: train.is_at_station) == (position.station is not None)
        is_stopping = _old(lambda: train.is_stopping)
        assert is_stopping == (None if train.current_or_next_stop is None else train.stopped_at is not None)
        for record in train.history:
            assert _old(lambda: record.stopping_station) == record.position.station
            assert _old(lambda: record.is_at_station) == (record.position.station is not None)


def test_find_trains_next_stop_station_warns(tracker):
    station = next(t.current_or_next_stop for t in tracker.active_trains.values() if t.current_or_next_stop)
    found = _old(lambda: tracker.find_trains(next_stop_station=station))
    assert found == tracker.find_trains(current_or_next_stop=station)


def test_regist_dia_warns(tracker):
    with pytest.warns(DeprecationWarning, match="regist_dia"):
        run(tracker.regist_dia(False))


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
