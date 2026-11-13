"""
KHState を、通信もモックも使わずに動かすテスト。
fixture の JSON を parse 関数で読み、apply_stations → apply_timetable → apply_positions の順に渡す。
"""

from conftest import load_fixture_bytes
from keihan_tracker import (KHState, KHTracker, ActiveTrainData,
                            parse_select_station, parse_transfer_guide, parse_timetable, parse_positions)


def _text(name: str) -> str:
    return load_fixture_bytes(name).decode("utf-8")


def _build_state() -> KHState:
    state = KHState()
    state.apply_stations(
        parse_select_station(_text("select_station.json")),
        parse_transfer_guide(_text("transferGuideInfo.json")),
    )
    # KHTracker.fetch_pos と同じく、位置情報の後にダイヤを渡す。
    # 臨時列車は、走行中（位置情報にある）ときだけダイヤに登録されるため、順番を替えると結果が変わる。
    # ダイヤの日付は、位置情報から決まった営業日になる
    state.apply_positions(parse_positions(_text("trainPositionList.json")))
    state.apply_timetable(parse_timetable(_text("startTimeList.json")))
    return state


def test_state_matches_tracker(tracker: KHTracker):
    state = _build_state()

    assert state.date == tracker.date
    assert state.position_created_at == tracker.position_created_at
    assert state.stations.keys() == tracker.stations.keys()
    for number, station in state.stations.items():
        other = tracker.stations[number]
        assert station.station_name == other.station_name
        assert station.line == other.line
        assert station.transfer == other.transfer

    assert state.trains.keys() == tracker.trains.keys()
    assert state.active_trains.keys() == tracker.active_trains.keys()
    for wdf, train in state.trains.items():
        other = tracker.trains[wdf]
        assert type(train) is type(other)
        assert train.date == other.date
        assert train.has_dia == other.has_dia
        assert train.line == other.line
        assert train.delay_minutes == other.delay_minutes
        assert [(s.station.station_number, s.is_start, s.is_final, s.is_stop, s.time) for s in train.route_stations] == \
               [(s.station.station_number, s.is_start, s.is_final, s.is_stop, s.time) for s in other.route_stations]
        if isinstance(train, ActiveTrainData):
            assert isinstance(other, ActiveTrainData)
            assert train.train_type == other.train_type
            assert train.direction == other.direction
            assert train.current_or_next_stop == other.current_or_next_stop
    assert state.max_delay_minutes == tracker.max_delay_minutes
    assert state._dia_matches_positions() == tracker._dia_matches_positions()


def test_state_uses_no_clock():
    """apply_positions は fileCreatedTime だけから営業日を決める（初期の date は上書きされる）"""
    state = KHState()
    assert state.date is None
    state.apply_stations(
        parse_select_station(_text("select_station.json")),
        parse_transfer_guide(_text("transferGuideInfo.json")),
    )
    positions = parse_positions(_text("trainPositionList.json"))
    state.apply_positions(positions)
    assert state.date == positions.fileCreatedTime.date()
    assert state._first_observed_time == positions.fileCreatedTime
