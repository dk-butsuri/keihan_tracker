import copy
import datetime

import pytest

from keihan_tracker import ActiveTrainData, TrainData, TrainType
from keihan_tracker.keihan_train.tracker import JST

from conftest import load_fixture, make_position_list, make_tracker, run

POSITION_PATH = "/zaisen-up/trainPositionList.json"
STARTTIME_PATH = "/zaisen-up/startTimeList.json"

FIXTURE_DATE = datetime.date(2026, 10, 6)


def _fixture_train_infos() -> list[dict]:
    data = load_fixture("trainPositionList.json")
    return [t for loc in data["locationObjects"] for t in loc["trainInfoObjects"]]


def _dia(station_number: str, dep_time: str) -> dict:
    return {
        "stationNumber": station_number,
        "stationDepTime": dep_time,
        "stationNameJp": "",
        "stationNameEn": "",
        "stationNameZhTw": "",
        "stationNameZhCn": "",
        "stationNameKo": "",
    }


def _start_time_list(trains: list[dict]) -> dict:
    return {"fileCreatedTime": "20261006212206", "fileVersion": "1.0.0", "TrainInfo": trains}


def _train_info(wdf: int, dias: list[dict], ext: bool = False) -> dict:
    return {
        "wdfBlockNo": str(wdf),
        "extTrain": "1" if ext else "0",
        "premiumCar": "0",
        "trainCar": "07003",
        "diaStationInfoObjects": dias,
    }


# ---------------------------------------------------------------- 実データの読み込み


def test_stations_registered(tracker):
    expected = set(range(1, 43)) | set(range(51, 55)) | set(range(61, 68)) | set(range(71, 78))
    assert set(tracker.stations) == expected
    assert tracker.stations[1].station_name.ja == "淀屋橋"
    assert tracker.stations[42].station_name.ja == "出町柳"
    assert tracker.stations[21].line == {"京阪本線・鴨東線", "交野線"}


def test_transfer_info_registered(tracker):
    # 京橋はJR・地下鉄と接続
    assert tracker.stations[4].transfer.train is not None
    assert tracker.stations[4].transfer.subway is not None


def test_date_from_fixture(tracker):
    assert tracker.date == FIXTURE_DATE


def test_all_trains_have_one_start_and_destination(tracker):
    """start_station / destination が例外を出さない（始発・終着がちょうど1つ）こと"""
    for train in tracker.trains.values():
        assert train.start_station is not None
        assert train.destination is not None
        assert train.start_station != train.destination, train.wdfBlockNo


def test_stop_stations_sorted_by_time(tracker):
    for train in tracker.trains.values():
        times = [s.time for s in train.stop_stations if s.time is not None]
        assert times == sorted(times), train.wdfBlockNo


def test_active_trains_match_fixture(tracker):
    infos = _fixture_train_infos()
    assert set(tracker.active_trains) == {int(t["wdfBlockNo"]) for t in infos}
    for info in infos:
        train = tracker.active_trains[int(info["wdfBlockNo"])]
        assert train.train_number == info["trainNumber"]
        assert train.cars == int(info["carsOfTrain"])
        assert train.destination is tracker.stations[int(info["destStationNumber"])]


def test_active_trains_next_stop_station(tracker):
    for train in tracker.active_trains.values():
        next_stop = train.next_stop_station
        assert next_stop is not None, train.wdfBlockNo
        assert next_stop in [s.station for s in train.stop_stations], train.wdfBlockNo


def test_inferred_train_type_matches_actual(tracker):
    """停車駅から推定した種別が、APIの種別と一致すること（ライナーは仕様上特急と判定）"""
    for train in tracker.active_trains.values():
        inferred = TrainData(
            master=tracker,
            wdfBlockNo=train.wdfBlockNo,
            date=train.date,
            has_premiumcar=None,
            train_formation=None,
            route_stations=train.route_stations,
        ).train_type
        expected = TrainType.LTD_EXP if train.train_type == TrainType.LINER else train.train_type
        assert inferred == expected, f"{train.train_number}号 (wdf {train.wdfBlockNo})"


def test_inferred_direction_matches_actual(tracker):
    for train in tracker.active_trains.values():
        inferred = TrainData(
            master=tracker,
            wdfBlockNo=train.wdfBlockNo,
            date=train.date,
            has_premiumcar=None,
            train_formation=None,
            route_stations=train.route_stations,
        ).direction
        assert inferred == train.direction, f"{train.train_number}号 (wdf {train.wdfBlockNo})"


def test_midnight_time_is_next_day(tracker):
    """24:00 などの深夜時刻は翌日の日時になること"""
    train = tracker.trains[1220]
    times = {s.station.station_number: s.time for s in train.route_stations}
    assert times[35] == datetime.datetime(2026, 10, 7, 0, 0, tzinfo=JST)


def test_find_trains(tracker):
    active = tracker.find_trains(status="active")
    assert len(active) == len(tracker.active_trains)

    ltd = tracker.find_trains(status="active", train_type=TrainType.LTD_EXP, direction="up")
    assert ltd
    assert all(t.train_type == TrainType.LTD_EXP and t.direction == "up" for t in ltd)


# ---------------------------------------------------------------- 状態の更新


def test_rate_limit_skips_request(api):
    tracker = make_tracker(api)
    tracker.rate_limit_interval = 60
    run(tracker.fetch_pos())
    run(tracker.fetch_pos())
    assert api.requests.count(POSITION_PATH) == 1


def test_dia_not_redownloaded_within_an_hour(api, fresh_tracker):
    """ダイヤのファイルが古くても、取得から1時間以内は取り直さない"""
    run(fresh_tracker.fetch_pos())
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 1


def test_train_disappears_then_inactivated(api, fresh_tracker):
    run(fresh_tracker.fetch_pos())
    liner = fresh_tracker.trains[1099]
    assert isinstance(liner, ActiveTrainData)

    api.set_json(POSITION_PATH, make_position_list("20261006213000"))
    run(fresh_tracker.fetch_pos())

    assert fresh_tracker.active_trains == {}
    train = fresh_tracker.trains[1099]
    assert type(train) is TrainData
    assert train.status == "completed"
    # アクティブ時の種別・方向が保持される
    assert train.train_type == TrainType.LINER
    assert train.direction == "up"


def test_active_train_updated(api, fresh_tracker):
    run(fresh_tracker.fetch_pos())

    data = copy.deepcopy(load_fixture("trainPositionList.json"))
    data["fileCreatedTime"] = "20261006212500"
    # 走行中の列車を次の駅に停車させ、遅延と通過駅を設定する
    target = next(
        loc for loc in data["locationObjects"]
        if loc["trainInfoObjects"][0]["wdfBlockNo"] == "1096"  # 上り特急、石清水八幡宮→中書島 走行中
    )
    assert not fresh_tracker.trains[1096].is_stopping
    target["locationRow"] = str(int(target["locationRow"]) + 1)
    info = target["trainInfoObjects"][0]
    info["delayMinutes"] = "3分遅れ"
    info["lastPassStation"] = "99"
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())

    train = fresh_tracker.trains[1096]
    assert isinstance(train, ActiveTrainData)
    assert train.delay_minutes == 3
    assert train.delay_text.ja == "3分遅れ"
    assert train.lastpass_station is None
    assert fresh_tracker.max_delay_train is train
    assert fresh_tracker.max_delay_minutes == 3


@pytest.mark.parametrize(
    ("file_created_time", "expected_date"),
    [
        ("20261007045959", datetime.date(2026, 10, 6)),  # 4時台は前日扱い
        ("20261007050000", datetime.date(2026, 10, 7)),  # 5時から新しい日
        ("20261007000000", datetime.date(2026, 10, 6)),
    ],
)
def test_date_change_time(api, fresh_tracker, file_created_time, expected_date):
    api.set_json(POSITION_PATH, make_position_list(file_created_time))
    api.set_json(STARTTIME_PATH, _start_time_list([
        _train_info(1, [_dia("401", "-"), _dia("392", "05:42"), _dia("422", "25:10")]),
    ]))
    run(fresh_tracker.fetch_pos())

    assert fresh_tracker.date == expected_date
    times = {s.station.station_number: s.time for s in fresh_tracker.trains[1].route_stations}
    base = datetime.datetime.combine(expected_date, datetime.time.min, tzinfo=JST)
    assert times[39] == base + datetime.timedelta(hours=5, minutes=42)
    assert times[42] == base + datetime.timedelta(hours=25, minutes=10)


def test_active_train_date_follows_position_list(api, fresh_tracker):
    # 実時刻とAPIの日付がずれていても、アクティブ列車の日付はAPI側に合わせるべき
    fresh_tracker.date = datetime.date(2026, 10, 5)
    run(fresh_tracker.fetch_pos())
    assert all(t.date == FIXTURE_DATE for t in fresh_tracker.active_trains.values())


# ---------------------------------------------------------------- ダイヤの解釈


def test_regist_dia_parses_stops(api, fresh_tracker):
    api.set_json(POSITION_PATH, make_position_list("20261006120000"))
    api.set_json(STARTTIME_PATH, _start_time_list([
        _train_info(1, [
            _dia("011", "-"),       # 淀屋橋（始発）
            _dia("021", "12:01"),   # 北浜
            _dia("031", "99:99"),   # 天満橋（通過）
            _dia("9", "12:03"),     # 3桁でないものは無視
            _dia("991", "12:04"),   # 未登録の駅は無視
            _dia("041", "12:05"),   # 京橋（終着）
        ]),
    ]))
    run(fresh_tracker.fetch_pos())

    train = fresh_tracker.trains[1]
    assert [s.station.station_number for s in train.route_stations] == [1, 2, 3, 4]
    assert [s.station.station_number for s in train.stop_stations] == [1, 2, 4]
    assert train.start_station.station_number == 1
    assert train.destination.station_number == 4
    assert train.get_stop_time(fresh_tracker.stations[3]) is None


def test_regist_dia_start_station_with_time(api, fresh_tracker):
    """始発駅に "-" ではなく時刻が入っている場合でも、最も早い駅を始発とする"""
    api.set_json(POSITION_PATH, make_position_list("20261006120000"))
    api.set_json(STARTTIME_PATH, _start_time_list([
        _train_info(1, [_dia("011", "12:00"), _dia("021", "12:01"), _dia("041", "12:05")]),
    ]))
    run(fresh_tracker.fetch_pos())

    train = fresh_tracker.trains[1]
    assert train.start_station.station_number == 1
    assert train.destination.station_number == 4


def test_regist_dia_skips_ext_train_unless_active(api, fresh_tracker):
    """臨時列車は運行中でなければ登録しない"""
    api.set_json(POSITION_PATH, make_position_list("20261006120000"))
    api.set_json(STARTTIME_PATH, _start_time_list([
        _train_info(1, [_dia("011", "-"), _dia("041", "12:05")], ext=True),
        _train_info(2, [_dia("011", "-"), _dia("041", "12:05")]),
    ]))
    run(fresh_tracker.fetch_pos())

    assert 1 not in fresh_tracker.trains
    assert 2 in fresh_tracker.trains
