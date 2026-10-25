import copy
import datetime

import pytest

from keihan_tracker import ActiveTrainData, DiaNotFoundError, StationData, TrainData, TrainType
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


def test_station_equality_by_number(tracker):
    neyagawa = tracker.stations[17]
    # 駅番号が同じなら、別のオブジェクトでも等しい
    copy = StationData(
        master=tracker,
        line={"京阪本線・鴨東線"},
        station_number=17,
        station_name=neyagawa.station_name,
    )
    assert neyagawa == copy
    assert neyagawa != tracker.stations[18]
    # 駅以外と比べても例外にならない
    assert neyagawa != 17
    assert neyagawa != "寝屋川市"


def test_station_hashable(tracker):
    neyagawa = tracker.stations[17]
    copy = StationData(
        master=tracker,
        line=set(neyagawa.line),
        station_number=17,
        station_name=neyagawa.station_name,
    )
    assert len({neyagawa, copy, tracker.stations[18]}) == 2
    assert {neyagawa: "ok"}[copy] == "ok"
    assert len(set(tracker.stations.values())) == len(tracker.stations)


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


def test_inferred_direction_matches_actual(saved_tracker):
    """現在・過去の走行中列車で、ダイヤから推定した方向が公式の方向と一致すること"""
    for train in saved_tracker.active_trains.values():
        if not train.has_dia:
            continue  # 運転整理でダイヤに無い列車は、公式の方向のみ取得できる。
        inferred = TrainData(
            master=saved_tracker,
            wdfBlockNo=train.wdfBlockNo,
            date=train.date,
            has_premiumcar=None,
            train_formation=None,
            route_stations=train.route_stations,
        ).direction
        assert inferred == train.direction, f"{train.train_number}号 (wdf {train.wdfBlockNo})"


@pytest.mark.parametrize(
    ("destination_number", "expected_direction"),
    [
        pytest.param(42, "up", id="demachiyanagi-up"),
        pytest.param(1, "down", id="yodoyabashi-down"),
        pytest.param(54, "down", id="nakanoshima-down"),
    ],
)
def test_direction_matches_terminal_destination(saved_tracker, destination_number, expected_direction):
    """現在・過去の平日・土休日ダイヤの全該当列車で、終端駅行きの方向が正しいこと"""
    trains = [
        train for train in saved_tracker.trains.values()
        if train.destination.station_number == destination_number
    ]
    assert trains, f"fixtureにKH{destination_number:02d}行きの列車がありません"
    for train in trains:
        assert train.direction == expected_direction, (
            f"wdf {train.wdfBlockNo}: {train.destination.station_name.ja}行き "
            f"direction={train.direction} (expected {expected_direction})"
        )


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



@pytest.mark.parametrize("direction", ["up", "down"])
def test_find_trains_direction_includes_scheduled(tracker, direction):
    """走行中でない列車も、始発・終着から推定した方向で絞り込む"""
    found = tracker.find_trains(direction=direction)
    assert all(t.direction == direction for t in found)
    # status は実時刻で決まるため、走行中かどうかだけで分ける
    not_active = [t for t in found if not isinstance(t, ActiveTrainData)]
    assert not_active
    assert len(not_active) < len([t for t in tracker.trains.values() if not isinstance(t, ActiveTrainData)])


def test_find_trains_active_only_conditions_exclude_others(tracker):
    """走行中の列車だけが持つ値を指定したら、走行中でない列車は当てはまらない"""
    train = next(t for t in tracker.active_trains.values() if t.is_stopping is not None)
    for found in [
        tracker.find_trains(train_number=train.train_number),
        tracker.find_trains(is_special=train.is_special),
        tracker.find_trains(next_stop_station=train.next_stop_station),
        tracker.find_trains(is_stopping=train.is_stopping),
    ]:
        assert train in found
        assert all(isinstance(t, ActiveTrainData) for t in found)


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


def test_dia_not_redownloaded_at_night(api, fresh_tracker):
    """深夜帯（日付が変わる4時より前）は、取得のたびにダイヤを取り直さない"""
    for created in ["20261007020000", "20261007035900"]:
        api.set_json(POSITION_PATH, make_position_list(created))
        run(fresh_tracker.fetch_pos())
    assert fresh_tracker.date == datetime.date(2026, 10, 6)
    assert api.requests.count(STARTTIME_PATH) == 1


def _position_1096(created: str) -> dict:
    """fixtureの1096（中書島付近を走行中、三条行き）だけがいる位置情報"""
    data = load_fixture("trainPositionList.json")
    loc = next(loc for loc in data["locationObjects"] if loc["trainInfoObjects"][0]["wdfBlockNo"] == "1096")
    return make_position_list(created, [loc])


# 前の日のダイヤ（1096が京橋止まり）と、今日のダイヤ（1096が三条行き）
STALE_DIA = _start_time_list([_train_info(1096, [_dia("011", "-"), _dia("041", "04:40")])])
TODAY_DIA = _start_time_list([_train_info(1096, [_dia("281", "-"), _dia("401", "04:50")])])


def test_dia_retried_until_it_matches_positions(api, fresh_tracker):
    """日付が変わってから、走行中の列車の行先とダイヤの終着駅が一致するまで、取得のたびに取り直す"""
    api.set_json(STARTTIME_PATH, STALE_DIA)
    api.set_json(POSITION_PATH, make_position_list("20261007030000"))
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 1

    # 日付が変わった。列車がいないので確かめられず、毎回取り直す（変わっていなければ304）
    for n, created in enumerate(["20261007040000", "20261007040100"], start=2):
        api.set_json(POSITION_PATH, make_position_list(created))
        run(fresh_tracker.fetch_pos())
        assert fresh_tracker.date == datetime.date(2026, 10, 7)
        assert api.requests.count(STARTTIME_PATH) == n

    # 列車が現れたが、ダイヤはまだ前の日のもの（行先が食い違う）なので取り直し続ける
    api.set_json(POSITION_PATH, _position_1096("20261007043100"))
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 4
    assert fresh_tracker.dia_confirmed_date is None

    # 公式のダイヤが今日のものに変わると、次の取得で追いつき、取り直しをやめる
    api.set_json(STARTTIME_PATH, TODAY_DIA)
    api.set_json(POSITION_PATH, _position_1096("20261007043200"))
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 5
    assert fresh_tracker.trains[1096].stop_stations[-1].station.station_number == 40
    assert fresh_tracker.dia_confirmed_date == datetime.date(2026, 10, 7)

    api.set_json(POSITION_PATH, _position_1096("20261007043300"))
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 5


def test_dia_retry_stops_at_deadline(api, fresh_tracker):
    """確かめられなくても、5時半を過ぎたら取得のたびの取り直しをやめる（安全装置）"""
    api.set_json(STARTTIME_PATH, STALE_DIA)
    api.set_json(POSITION_PATH, _position_1096("20261007052900"))
    run(fresh_tracker.fetch_pos())
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 2

    api.set_json(POSITION_PATH, _position_1096("20261007053000"))
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 2
    assert fresh_tracker.dia_confirmed_date is None


def _expire_dia(tracker):
    """前回のダイヤ取得から1時間以上たったことにする"""
    tracker.last_regist_dia_datetime -= datetime.timedelta(hours=2)


def test_dia_not_modified_keeps_timetable(api, fresh_tracker):
    """ダイヤが変わっていなければ 304 を受け取り、手元のダイヤをそのまま使う"""
    run(fresh_tracker.fetch_pos())
    first = fresh_tracker.starttime_list
    assert "If-None-Match" not in api.last_headers[STARTTIME_PATH]

    _expire_dia(fresh_tracker)
    run(fresh_tracker.fetch_pos())

    assert api.requests.count(STARTTIME_PATH) == 2
    assert "If-None-Match" in api.last_headers[STARTTIME_PATH]
    assert fresh_tracker.starttime_list is first  # パースし直していない
    assert all(t.route_stations for t in fresh_tracker.trains.values())


def test_dia_modified_is_parsed_again(api, fresh_tracker):
    run(fresh_tracker.fetch_pos())
    api.set_json(STARTTIME_PATH, _start_time_list([
        _train_info(1, [_dia("011", "-"), _dia("041", "21:50")]),
    ]))

    _expire_dia(fresh_tracker)
    run(fresh_tracker.fetch_pos())

    assert len(fresh_tracker.starttime_list.TrainInfo) == 1


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
    # 走行中の列車を次の駅に停車させ、遅延を設定する
    target = next(
        loc for loc in data["locationObjects"]
        if loc["trainInfoObjects"][0]["wdfBlockNo"] == "1096"  # 上り特急、石清水八幡宮→中書島 走行中
    )
    assert not fresh_tracker.trains[1096].is_stopping
    target["locationRow"] = str(int(target["locationRow"]) + 1)
    info = target["trainInfoObjects"][0]
    info["delayMinutes"] = "3分遅れ"
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())

    train = fresh_tracker.trains[1096]
    assert isinstance(train, ActiveTrainData)
    assert train.delay_minutes == 3
    assert train.delay_text.ja == "3分遅れ"
    assert fresh_tracker.max_delay_train is train
    assert fresh_tracker.max_delay_minutes == 3


def test_staying_time(api, fresh_tracker):
    # 1096は石清水八幡宮→中書島を走行中（駅間）。fixtureの作成時刻は 2026-10-06 21:24:09
    run(fresh_tracker.fetch_pos())
    train = fresh_tracker.trains[1096]
    assert isinstance(train, ActiveTrainData)
    assert not train.is_at_station
    # 出現した時点では、それより前の時間は分からないので0
    assert train.staying_time == datetime.timedelta(0)

    # 20分間同じ座標なら、駅間でも足止めとして時間が増える
    data = copy.deepcopy(load_fixture("trainPositionList.json"))
    data["fileCreatedTime"] = "20261006214409"
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())
    assert fresh_tracker.trains[1096].staying_time == datetime.timedelta(minutes=20)

    # 座標が変わったら0に戻る
    data = copy.deepcopy(data)
    data["fileCreatedTime"] = "20261006214509"
    target = next(
        loc for loc in data["locationObjects"]
        if loc["trainInfoObjects"][0]["wdfBlockNo"] == "1096"
    )
    target["locationRow"] = str(int(target["locationRow"]) + 1)
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())
    assert fresh_tracker.trains[1096].staying_time == datetime.timedelta(0)


def _flip_direction(data: dict, wdf: str) -> dict:
    loc = next(loc for loc in data["locationObjects"] if loc["trainInfoObjects"][0]["wdfBlockNo"] == wdf)
    loc["trainDirection"] = "1" if str(loc["trainDirection"]) == "0" else "0"
    return loc


def test_direction_corrected_on_next_fetch(api, fresh_tracker):
    # 出現直後の1分だけ方向が誤っていた場合、次の取得で直る
    data = copy.deepcopy(load_fixture("trainPositionList.json"))
    _flip_direction(data, "1096")
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())
    assert fresh_tracker.trains[1096].direction == "down"

    api.set_json(POSITION_PATH, load_fixture("trainPositionList.json"))
    run(fresh_tracker.fetch_pos())
    assert fresh_tracker.trains[1096].direction == "up"


def test_direction_kept_while_stopping_at_terminal(api, fresh_tracker):
    # 終点に停車中は公式の方向が次の運行の向きに変わるが、追従しない
    run(fresh_tracker.fetch_pos())
    train = next(t for t in fresh_tracker.active_trains.values() if t.is_stopping)
    wdf, direction = train.wdfBlockNo, train.direction

    data = copy.deepcopy(load_fixture("trainPositionList.json"))
    loc = _flip_direction(data, str(wdf))
    loc["trainInfoObjects"][0]["destStationNumber"] = str(train.next_station.station_number)
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())

    train = fresh_tracker.trains[wdf]
    assert train.destination == train.next_station
    assert train.direction == direction


@pytest.mark.parametrize(
    ("file_created_time", "expected_date"),
    [
        ("20261007035959", datetime.date(2026, 10, 6)),  # 3時台は前日扱い
        ("20261007040000", datetime.date(2026, 10, 7)),  # 4時から新しい日
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
            _dia("030", "99:99"),   # 天満橋（通過）
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


@pytest.mark.parametrize("name", ["startTimeList.json", "startTimeList_holiday.json"])
def test_station_number_third_digit_means_pass(name):
    """通常のダイヤでは、stationNumber の3桁目が0 ⇔ 時刻が99:99（通過）。崩れたらAPIの仕様変更を疑う"""
    for train in load_fixture(name)["TrainInfo"]:
        for stop in train["diaStationInfoObjects"]:
            assert (stop["stationNumber"][2] == "0") == (stop["stationDepTime"] == "99:99"), (train["wdfBlockNo"], stop)


def test_regist_dia_stop_with_unknown_time(api, fresh_tracker):
    """ダイヤ乱れ時は、停車するが時刻不明（99:99）の駅がある。3桁目で停車・通過を判定する"""
    # 2025-03-21 の wdf 336（淀屋橋発出町柳行き準急）を元にしたダイヤ。萱島より先は時刻不明
    dias = [_dia("011", "-"), _dia("021", "12:01"), _dia("031", "12:03"), _dia("041", "12:05"),
            _dia("111", "12:10"), _dia("161", "12:15"), _dia("990", "99:99")]
    dias += [_dia(f"{n:02}2", "99:99") for n in range(17, 43)]
    api.set_json(POSITION_PATH, make_position_list("20261006120000"))
    api.set_json(STARTTIME_PATH, _start_time_list([_train_info(1, dias)]))
    run(fresh_tracker.fetch_pos())

    train = fresh_tracker.trains[1]
    assert [s.station.station_number for s in train.stop_stations] == [1, 2, 3, 4, 11, 16] + list(range(17, 43))
    assert train.destination.station_number == 42
    assert train.get_stop_time(fresh_tracker.stations[17]) is None
    assert train.train_type == TrainType.SUB_EXP
    # 終着駅の時刻が不明でも、時刻が分かっている最後の停車駅（萱島 12:15）で判定する
    assert train.status == "completed"


def test_upcoming_trains_with_unknown_time(api, fresh_tracker):
    """時刻不明の停車駅があっても、走行中の列車がこれから停車する駅の発車標に出る"""
    data = load_fixture("startTimeList.json")
    # wdf 1096：上り特急三条行き、石清水八幡宮→中書島 走行中。中書島より先の停車駅の時刻を不明にする
    info = next(t for t in data["TrainInfo"] if t["wdfBlockNo"] == "1096")
    for stop in info["diaStationInfoObjects"]:
        if int(stop["stationNumber"][:2]) > 28 and stop["stationNumber"][2] != "0":
            stop["stationDepTime"] = "99:99"
    api.set_json(STARTTIME_PATH, data)
    run(fresh_tracker.fetch_pos())

    train = fresh_tracker.trains[1096]
    assert train.next_stop_station.station_number == 28
    assert [s.station.station_number for s in train.stop_stations][-4:] == [30, 37, 39, 40]
    assert train.destination.station_number == 40

    tambabashi = fresh_tracker.stations[30].upcoming_trains
    assert (train, train.stop_stations[-4]) in tambabashi
    assert tambabashi[-1][0] is train  # 時刻不明は後ろ
    assert train not in [t for t, _ in fresh_tracker.stations[24].upcoming_trains]  # 樟葉は通過済み


# ---------------------------------------------------------------- ダイヤに無い列車

NO_DIA_WDF = 1194  # 宇治線 宇治行き普通、観月橋に停車中


@pytest.fixture
def no_dia_tracker(api, fresh_tracker):
    """運休・運転整理などで、位置情報にはあるがダイヤに無い列車がいる状態"""
    data = load_fixture("startTimeList.json")
    data["TrainInfo"] = [t for t in data["TrainInfo"] if int(t["wdfBlockNo"]) != NO_DIA_WDF]
    api.set_json(STARTTIME_PATH, data)
    run(fresh_tracker.fetch_pos())
    return fresh_tracker


def test_active_train_without_dia(no_dia_tracker):
    tracker = no_dia_tracker
    train = tracker.trains[NO_DIA_WDF]
    assert isinstance(train, ActiveTrainData)
    assert not train.has_dia
    assert tracker.trains[1192].has_dia

    # ダイヤから求める情報は分からない
    with pytest.raises(DiaNotFoundError):
        train.start_station
    assert issubclass(DiaNotFoundError, ValueError)
    assert train.stop_stations == []
    assert train.next_stop_station is None
    # 駅の位置にはいるが、停車か通過かはダイヤが無いと分からない
    assert train.is_at_station
    assert train.is_stopping is None
    assert train.is_at_start_station is None

    # 位置情報から分かる情報は使える
    assert train.train_type == TrainType.LOCAL
    assert train.destination is tracker.stations[77]
    assert train.direction == "down"
    assert train.line == "宇治線"
    assert not train.is_special
    assert train.next_station is tracker.stations[71]
    str(train)

    # 駅から列車を探しても落ちない（ダイヤに無い列車は出てこない）
    kangetsukyo = tracker.stations[71]
    assert train not in kangetsukyo.arriving_trains
    assert train not in [t for t, _ in kangetsukyo.upcoming_trains]
    assert train in tracker.find_trains(destination=tracker.stations[77])


def test_inactivated_train_without_dia(api, no_dia_tracker):
    """ダイヤに無い列車が運行を終えても、アクティブ時の情報が残る"""
    tracker = no_dia_tracker
    api.set_json(POSITION_PATH, make_position_list("20261006213000"))
    run(tracker.fetch_pos())

    train = tracker.trains[NO_DIA_WDF]
    assert type(train) is TrainData
    assert not train.has_dia
    assert train.status == "completed"
    assert train.train_type == TrainType.LOCAL
    assert train.destination is tracker.stations[77]
    assert train.direction == "down"
    assert train.line == "宇治線"
    with pytest.raises(DiaNotFoundError):
        train.start_station
    assert "不明 発" in str(train)
    assert train in tracker.find_trains(destination=tracker.stations[77])


def test_train_data_without_dia_or_actual_data(tracker):
    """ダイヤもアクティブ時の情報も無ければ、推定する情報は DiaNotFoundError になる"""
    train = TrainData(master=tracker, wdfBlockNo=1, date=FIXTURE_DATE, has_premiumcar=None, train_formation=None)
    for name in ["train_type", "direction", "destination", "start_station"]:
        with pytest.raises(DiaNotFoundError):
            getattr(train, name)
