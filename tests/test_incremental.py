"""
ダイヤの差分更新と、駅→停車駅の索引。

同じ時系列を、差分更新する tracker と、毎回全列車のダイヤを登録し直す tracker（KHState._always_full_timetable）に流し、
取得のたびに equivalence.dump() の出力が一致することを確かめる。
あわせて、停車駅の逆参照（train）が KHState.trains の今のオブジェクトを指していること、
駅の trains・upcoming_trains が索引を使わない素朴な実装と同じ順番で返ることを確かめる。
"""

import copy
import datetime

import pytest

from keihan_tracker import ActiveTrainData, TrainData

from conftest import MockKeihanAPI, load_fixture, make_position_list, make_tracker, run
from equivalence import dump

POSITION_PATH = "/zaisen-up/trainPositionList.json"
STARTTIME_PATH = "/zaisen-up/startTimeList.json"
# 素朴な実装との比較に使う駅（京橋・寝屋川市・香里園・枚方市・出町柳・中之島・私市・宇治）
CHECK_STATIONS = [4, 17, 18, 21, 42, 54, 67, 77]


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(a.keys() | b.keys(), key=str):
            if key not in a or key not in b:
                out.append(f"{path}/{key}: 片方にしかない")
            else:
                out += _diff(a[key], b[key], f"{path}/{key}")
        return out
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def _positions(created: str, wdfs=None, extra: list[dict] | None = None) -> dict:
    """fixture の位置情報のうち、wdfs の列車だけ（None なら全部）。extra は足す列車（wdf を差し替えた複製）"""
    data = load_fixture("trainPositionList.json")
    data["fileCreatedTime"] = created
    locations = []
    for loc in data["locationObjects"]:
        loc["trainInfoObjects"] = [
            info for info in loc["trainInfoObjects"] if wdfs is None or int(info["wdfBlockNo"]) in wdfs
        ]
        if loc["trainInfoObjects"]:
            locations.append(loc)
    data["locationObjects"] = locations + (extra or [])
    return data


def _clone_location(wdf: int) -> dict:
    """fixture の先頭の列車の位置を複製し、WDF番号だけ wdf にする"""
    loc = copy.deepcopy(load_fixture("trainPositionList.json")["locationObjects"][0])
    loc["trainInfoObjects"] = [loc["trainInfoObjects"][0]]
    loc["trainInfoObjects"][0]["wdfBlockNo"] = str(wdf)
    return loc


FIXTURE_WDFS = sorted(
    int(info["wdfBlockNo"])
    for loc in load_fixture("trainPositionList.json")["locationObjects"]
    for info in loc["trainInfoObjects"]
)
RUNNING = FIXTURE_WDFS[0]                    # 位置情報に出たり消えたりさせる列車
OTHER = FIXTURE_WDFS[1:]                     # ずっと走っている列車


def _timetable() -> dict:
    return load_fixture("startTimeList.json")


def _train_info(timetable: dict, wdf: int) -> dict:
    return next(t for t in timetable["TrainInfo"] if int(t["wdfBlockNo"]) == wdf)


def _naive_trains(station):
    """索引を使わない StationData.trains（v2.9 までの実装）"""
    import datetime as dt
    from keihan_tracker.train.tracker import JST
    trains = []
    for train in station.master.trains.values():
        for stop in train.stop_stations:
            if stop.station == station and stop.is_stop:
                trains.append((train, stop))
    trains.sort(key=lambda t: t[1].time or dt.datetime.max.replace(tzinfo=JST))
    return trains


def _naive_upcoming(station):
    """索引を使わない StationData.upcoming_trains"""
    import datetime as dt
    from keihan_tracker.train.tracker import JST
    trains = []
    for train, stop in _naive_trains(station):
        if isinstance(train, ActiveTrainData):
            current = train.current_or_next_stop
            if not current:
                continue
            stations = [s.station for s in train.stop_stations]
            if stations.index(station) >= stations.index(current):
                trains.append((train, stop))
        elif train._may_stop_after_observation_began(stop):
            trains.append((train, stop))
    trains.sort(key=lambda x: x[1].time or dt.datetime.max.replace(tzinfo=JST))
    return trains


def check_consistency(tracker) -> None:
    """逆参照と索引が、いつも self.trains の今の中身と合っていること"""
    for wdf, train in tracker.trains.items():
        for stop in train.route_stations:
            assert stop.train is train, f"WDF{wdf} の停車駅の train が今の列車を指していない"
    for number in CHECK_STATIONS:
        station = tracker.stations[number]
        for name, got, want in [
            ("trains", station.trains, _naive_trains(station)),
            ("upcoming_trains", station.upcoming_trains, _naive_upcoming(station)),
        ]:
            assert len(got) == len(want), (number, name)
            for (t1, s1), (t2, s2) in zip(got, want):
                assert t1 is t2 and s1 is s2, (number, name)


class Pair:
    """差分更新する tracker（inc）と、毎回全登録する tracker（full）に、同じ取得をさせる"""

    def __init__(self, api: MockKeihanAPI) -> None:
        self.api = api
        self.inc = make_tracker(api)
        self.full = make_tracker(api)
        self.full.state._always_full_timetable = True
        self.count = 0

    def fetch(self, position: dict, timetable: dict | None = None, expire: bool = False) -> None:
        self.api.set_json(POSITION_PATH, position)
        if timetable is not None:
            self.api.set_json(STARTTIME_PATH, timetable)
        for tracker in (self.inc, self.full):
            if expire:
                tracker.last_regist_dia_datetime -= datetime.timedelta(hours=2)
            run(tracker.fetch_pos())
        self.count += 1
        diffs = _diff(dump(self.full), dump(self.inc))
        assert not diffs, f"{self.count}回目の取得で {len(diffs)} 件違う:\n" + "\n".join(diffs[:20])
        check_consistency(self.inc)
        check_consistency(self.full)

    def stops(self, wdf: int) -> list:
        """inc の列車の停車駅オブジェクト（作り直されたかを、同一性で確かめる）"""
        return list(self.inc.trains[wdf].route_stations)


@pytest.fixture
def pair(api: MockKeihanAPI) -> Pair:
    return Pair(api)


def test_steady_fetches_and_not_modified(pair):
    """位置が変わらない取得と、ダイヤの 304 では、停車駅を作り直さない"""
    pair.fetch(_positions("20261006212409"))
    before = {wdf: pair.stops(wdf) for wdf in pair.inc.trains}
    timetable = pair.inc.starttime_list

    pair.fetch(_positions("20261006212509"))
    pair.fetch(_positions("20261006212609"), expire=True)  # 1時間たって取り直すが、304
    assert pair.inc.starttime_list is timetable
    assert pair.api.requests.count(STARTTIME_PATH) == 4  # 2つの tracker が1回ずつ全文を取り、304 を1回ずつ受け取った

    for wdf, stops in before.items():
        assert pair.stops(wdf) == stops
        assert all(a is b for a, b in zip(pair.stops(wdf), stops)), wdf
    # 毎回全登録する側は、作り直している
    assert pair.full.trains[OTHER[0]].route_stations[0] is not before[OTHER[0]][0]


def test_train_starts_running(pair):
    """予定の列車（TrainData）が走り出す。停車駅・編成・プレミアムカーは、走り出す前のものを引き継ぐ"""
    pair.fetch(_positions("20261006212409", wdfs=set(OTHER)))
    assert type(pair.inc.trains[RUNNING]) is TrainData
    stops = pair.stops(RUNNING)
    assert stops

    pair.fetch(_positions("20261006212509"))
    train = pair.inc.trains[RUNNING]
    assert isinstance(train, ActiveTrainData)
    assert pair.stops(RUNNING) == stops and all(a is b for a, b in zip(pair.stops(RUNNING), stops))
    assert train.train_formation is not None and train.has_premiumcar is not None
    assert [s.train for s in train.route_stations] == [train] * len(stops)


def test_train_stops_running_and_restarts(pair):
    """走り終える（inactivate）と、また走り出す。停車駅の逆参照は、そのときの列車に付け替わる"""
    pair.fetch(_positions("20261006212409"))
    stops = pair.stops(RUNNING)
    active = pair.inc.trains[RUNNING]

    pair.fetch(_positions("20261006212509", wdfs=set(OTHER)))
    inactive = pair.inc.trains[RUNNING]
    assert type(inactive) is TrainData and inactive is not active
    assert all(s.train is inactive for s in stops)
    assert all(a is b for a, b in zip(inactive.route_stations, stops))

    pair.fetch(_positions("20261006212609", wdfs=set(OTHER)))     # 走り終えたまま
    pair.fetch(_positions("20261006212709"), expire=True)         # また走り出す（ダイヤは 304）
    restarted = pair.inc.trains[RUNNING]
    assert isinstance(restarted, ActiveTrainData) and restarted is not inactive
    assert all(s.train is restarted for s in stops)
    assert restarted.was_active and len(restarted.history) == 2
    # 辞書の中の位置は変わらない
    assert list(pair.inc.trains) == list(pair.full.trains)


def test_ext_train_starts_running(pair):
    """臨時列車は、走り出したときに1本だけ登録される（走り出すまでは self.trains に無い）"""
    ext_wdf = OTHER[0]
    timetable = _timetable()
    _train_info(timetable, ext_wdf)["extTrain"] = "1"
    unseen = next(int(t["wdfBlockNo"]) for t in timetable["TrainInfo"] if int(t["wdfBlockNo"]) not in FIXTURE_WDFS)
    _train_info(timetable, unseen)["extTrain"] = "1"

    others = set(OTHER[1:]) | {RUNNING}
    pair.fetch(_positions("20261006212409", wdfs=others), timetable=timetable)
    assert ext_wdf not in pair.inc.trains and unseen not in pair.inc.trains
    normal = pair.stops(RUNNING)

    # 位置情報に出た臨時列車（ダイヤの更新は無い）
    pair.fetch(_positions("20261006212509", extra=[_clone_location(unseen)]))
    assert pair.inc.trains[ext_wdf].has_dia and pair.inc.trains[unseen].has_dia
    assert pair.inc.trains[unseen].train_formation is not None
    assert all(a is b for a, b in zip(pair.stops(RUNNING), normal))
    # 走り終えても、臨時列車は残り、停車駅も残る
    pair.fetch(_positions("20261006212609", wdfs=others))
    assert pair.inc.trains[unseen].has_dia and type(pair.inc.trains[unseen]) is TrainData
    pair.fetch(_positions("20261006212709", extra=[_clone_location(unseen)]), expire=True)
    assert isinstance(pair.inc.trains[unseen], ActiveTrainData) and pair.inc.trains[unseen].has_dia


def test_date_change_rebuilds_with_not_modified(pair):
    """営業日が変わると、ダイヤが 304（変わっていない）でも、時刻から作り直す"""
    pair.fetch(_positions("20261006235900"))
    pair.fetch(_positions("20261007000100"))                 # 日付は 10-06 のまま
    pair.fetch(_positions("20261007035900"))
    assert pair.inc.date == datetime.date(2026, 10, 6)
    timetable = pair.inc.starttime_list
    old_stops = pair.stops(OTHER[0])

    pair.fetch(_positions("20261007040000", wdfs=set()))     # 4時: 営業日が変わり、列車がいない
    assert pair.inc.date == datetime.date(2026, 10, 7)
    assert pair.inc.starttime_list is timetable              # ダイヤは同じ（304）
    train = pair.inc.trains[OTHER[0]]
    assert train.date == datetime.date(2026, 10, 7)
    assert all(s.time is None or s.time.date() >= datetime.date(2026, 10, 7) for s in train.route_stations)
    assert pair.stops(OTHER[0])[0] is not old_stops[0]

    pair.fetch(_positions("20261007043000", wdfs=set(OTHER)))
    pair.fetch(_positions("20261007043100"))                 # 日付が変わったあとに、走り出す
    assert isinstance(pair.inc.trains[RUNNING], ActiveTrainData)
    assert pair.inc.trains[RUNNING].date == datetime.date(2026, 10, 7)
    assert pair.inc.trains[RUNNING].route_stations[-1].time.date() >= datetime.date(2026, 10, 7)


def test_new_timetable_rebuilds(pair):
    """新しいダイヤ（別のオブジェクト）を受け取ると、全部作り直す。ダイヤにあった列車が消えても、今までと同じ"""
    pair.fetch(_positions("20261006212409"))
    old_stops = pair.stops(OTHER[0])

    timetable = _timetable()
    shifted = _train_info(timetable, OTHER[0])
    for dia in shifted["diaStationInfoObjects"]:
        if dia["stationDepTime"] not in ("-", "99:99"):
            hour, minute = map(int, dia["stationDepTime"].split(":"))
            dia["stationDepTime"] = f"{hour:02}:{(minute + 1) % 60:02}"
    _train_info(timetable, RUNNING)["premiumCar"] = "1" if _train_info(timetable, RUNNING)["premiumCar"] == "0" else "0"
    _train_info(timetable, RUNNING)["trainCar"] = "99999"
    timetable["TrainInfo"] = [t for t in timetable["TrainInfo"] if int(t["wdfBlockNo"]) != OTHER[1]]
    timetable["fileCreatedTime"] = "20261006220000"
    pair.fetch(_positions("20261006212509"), timetable=timetable, expire=True)

    assert pair.stops(OTHER[0])[0] is not old_stops[0]
    assert pair.inc.trains[RUNNING].train_formation == 99999
    assert OTHER[1] in pair.inc.trains  # 消えた列車は、これまでと同じく残る

    # 新しいダイヤでも、続けて 304 と走り出しを通る
    pair.fetch(_positions("20261006212609", wdfs=set(OTHER)))
    pair.fetch(_positions("20261006212709"), expire=True)
    assert pair.inc.trains[RUNNING].train_formation == 99999


def test_trains_and_stations_are_read_only(api):
    """trains・stations は読み取り専用。索引とずれないよう、書き換えは apply_* だけが行う"""
    tracker = make_tracker(api)
    run(tracker.fetch_pos())
    wdf = next(iter(tracker.trains))
    for mapping in (tracker.trains, tracker.state.trains):
        with pytest.raises(TypeError):
            mapping[wdf] = mapping[wdf]
        with pytest.raises(TypeError):
            del mapping[wdf]
    with pytest.raises(TypeError):
        tracker.stations[17] = tracker.stations[17]
    for owner in (tracker, tracker.state):
        with pytest.raises(AttributeError):
            owner.trains = {}
        with pytest.raises(AttributeError):
            owner.stations = {}
    check_consistency(tracker)


def test_rebuild_index_after_route_edit(api):
    """route_stations の中身だけを書き換えたときは、_rebuild_index() で合わせられる"""
    tracker = make_tracker(api)
    run(tracker.fetch_pos())
    train = next(t for t in tracker.trains.values() if t.has_dia and tracker.stations[18] in [s.station for s in t.stop_stations])
    stop = train.get_stop(tracker.stations[18])
    stop.is_stop = False
    assert (train, stop) in tracker.stations[18].trains       # 索引は書き換えを知らない
    tracker.state._rebuild_index()
    assert (train, stop) not in tracker.stations[18].trains


def test_get_stop(tracker):
    train = tracker.trains[1096]
    station = tracker.stations[18]
    stop = train.get_stop(station)
    if stop is None:
        # 1096 が香里園に停車しないときは、停車する駅で確かめる
        station = train.stop_stations[1].station
        stop = train.get_stop(station)
    assert stop is not None
    assert stop.station == station and stop.is_stop and stop.train is train
    assert train.get_stop_time(station) == stop.time
    assert stop in train.stop_stations


def test_get_stop_none_for_pass_and_unrelated(tracker):
    # 通過駅がある列車（急行など）
    train = next(t for t in tracker.trains.values() if t.has_dia and any(not s.is_stop for s in t.route_stations))
    stops = {s.station.station_number for s in train.stop_stations}
    passed = [s.station for s in train.route_stations if not s.is_stop]
    assert train.get_stop(passed[0]) is None
    assert train.get_stop_time(passed[0]) is None
    unrelated = next(st for number, st in tracker.stations.items() if number not in stops and st not in [s.station for s in train.route_stations])
    assert train.get_stop(unrelated) is None


def test_stop_train_back_reference_and_repr(tracker):
    train = next(t for t in tracker.trains.values() if t.has_dia)
    stop = train.route_stations[0]
    assert stop.train is train
    assert repr(stop) and "TrainData" not in repr(stop)    # 列車との循環で repr が終わらなくならない
