"""
ドメインクラス（StationData・StopStationData・TrainData・ActiveTrainData）を pydantic から dataclass にしたあとも、
pydantic がしていたことが保たれているか確かめる（v3 step 3a）。
"""

import dataclasses
import datetime

import pytest

from conftest import run
from keihan_tracker import ActiveTrainData, StationData, StopStationData, TrainData, TrainType

FIXTURE_DATE = datetime.date(2026, 10, 6)


def _active(tracker, **overrides) -> ActiveTrainData:
    """ActiveTrainData を、位置情報の値のキーワード名（train_type・direction・destination）で作る"""
    kwargs = dict(
        master=tracker,
        wdf=9001,
        date=FIXTURE_DATE,
        train_number="9001",
        train_type=TrainType.LOCAL,
        direction="up",
        destination=tracker.stations[17],
        is_special=False,
        cars=7,
        delay_text=tracker.stations[17].station_name,
        location_col=10,
        location_row=10,
    )
    kwargs.update(overrides)
    return ActiveTrainData(**kwargs)


def test_dataclasses_are_not_pydantic_models():
    for cls in (StationData, StopStationData, TrainData, ActiveTrainData):
        assert dataclasses.is_dataclass(cls)
        assert not hasattr(cls, "model_config")
        assert not hasattr(cls, "model_dump")


def test_train_data_accepts_wdf_and_wdfBlockNo(tracker):
    by_new = TrainData(master=tracker, wdf=1, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None)
    by_old = TrainData(master=tracker, wdfBlockNo=1, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None)
    assert by_new.wdf == by_old.wdf == 1
    with pytest.raises(TypeError):
        TrainData(master=tracker, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None)
    with pytest.raises(TypeError):
        TrainData(master=tracker, wdf=1, wdfBlockNo=1, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None)


def test_active_train_data_keyword_names(tracker):
    destination = tracker.stations[17]
    train = _active(tracker, train_type=TrainType.EXPRESS, direction="down", destination=destination)
    assert train.active_train_type == TrainType.EXPRESS
    assert train.active_direction == "down"
    assert train.active_destination is destination
    assert train.train_type == TrainType.EXPRESS
    assert train.direction == "down"
    assert train.destination is destination
    # 行先なし(00)は None
    assert _active(tracker, destination=None).active_destination is None
    # 引数が足りなければ TypeError
    with pytest.raises(TypeError):
        ActiveTrainData(master=tracker, wdf=1, date=FIXTURE_DATE)


def test_active_train_data_accepts_wdfBlockNo(tracker):
    train = ActiveTrainData(
        master=tracker, wdfBlockNo=9002, date=FIXTURE_DATE, train_number="9002",
        train_type=TrainType.LOCAL, direction="up", destination=None, is_special=False, cars=7,
        delay_text=tracker.stations[17].station_name, location_col=10, location_row=10,
    )
    assert train.wdf == 9002


def test_master_tracker_is_replaced_by_state(tracker):
    state = tracker.state
    train = TrainData(master=tracker, wdf=1, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None)
    assert train.master is state
    assert _active(tracker).master is state
    station = StationData(
        master=tracker, line={"京阪本線・鴨東線"}, station_number=17, station_name=tracker.stations[17].station_name
    )
    assert station.master is state
    # 状態をそのまま渡しても同じ
    assert TrainData(master=state, wdf=1, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None).master is state


def test_lists_are_copied_on_construction(tracker):
    route = [StopStationData(station=tracker.stations[17])]
    history: list = []
    train = TrainData(
        master=tracker, wdf=1, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None,
        route_stations=route, history=history,
    )
    assert train.route_stations is not route and train.route_stations == route
    assert train.history is not history
    # 中身は同じオブジェクト
    assert train.route_stations[0] is route[0]
    # 渡した側への append は、渡された側に見えない
    route.append(StopStationData(station=tracker.stations[18]))
    assert len(train.route_stations) == 1


def test_station_line_set_is_copied(tracker):
    lines = {"京阪本線・鴨東線"}
    station = StationData(master=tracker, line=lines, station_number=17, station_name=tracker.stations[17].station_name)
    lines.add("交野線")
    assert station.line == {"京阪本線・鴨東線"}


def test_station_default_transfer_is_not_shared(tracker):
    name = tracker.stations[17].station_name
    a = StationData(master=tracker, line=set(), station_number=90, station_name=name)
    b = StationData(master=tracker, line=set(), station_number=91, station_name=name)
    assert a.transfer is not b.transfer


def test_inactivate_does_not_share_lists(tracker):
    train = next(t for t in tracker.active_trains.values() if t.has_dia and t.history)
    inactive = train.inactivate()
    assert type(inactive) is TrainData
    assert inactive.history is not train.history
    assert inactive.route_stations is not train.route_stations
    assert inactive.history == train.history
    assert inactive.route_stations == train.route_stations
    # 片方に加えても、もう片方に見えない
    before = (len(train.history), len(train.route_stations))
    inactive.history.append(train.history[-1])
    inactive.route_stations.append(train.route_stations[-1])
    assert (len(train.history), len(train.route_stations)) == before


def test_active_train_started_again_does_not_share_history(fresh_tracker):
    """一度消えた列車がまた走り出したとき、履歴を引き継ぐが、消えた列車のリストとは別のリストになる"""
    run(fresh_tracker.fetch_pos())
    wdf = next(iter(fresh_tracker.active_trains))
    inactive = fresh_tracker.state.active_trains[wdf].inactivate()
    fresh_tracker.state.trains[wdf] = inactive
    # 位置情報にまた現れたときと同じ作り方
    again = ActiveTrainData(
        master=fresh_tracker.state, history=inactive.history, wdf=wdf, date=inactive.date,
        train_number="1", train_type=TrainType.LOCAL, direction="up", destination=None,
        is_special=False, cars=1, delay_text=fresh_tracker.stations[17].station_name,
        location_col=0, location_row=0,
    )
    assert again.history is not inactive.history
    assert again.history == inactive.history


def test_identity_equality_for_trains_and_stops(tracker):
    train = next(iter(tracker.trains.values()))
    assert train == train
    assert train in list(tracker.trains.values())
    stop = StopStationData(station=tracker.stations[17])
    same_values = StopStationData(station=tracker.stations[17])
    assert stop != same_values
    assert stop == stop


def test_station_equality_is_kept(tracker):
    name = tracker.stations[17].station_name
    copy = StationData(master=tracker, line=set(), station_number=17, station_name=name)
    assert copy == tracker.stations[17]
    assert hash(copy) == hash(tracker.stations[17])
