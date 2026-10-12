"""
公式APIが返した異常なデータ（2026-10-07〜10-10 に snapshot_dir で保存）での挙動。

ダイヤは保存されていないため、tests/fixtures/ の平日ダイヤと組み合わせている。
"""

import copy
import datetime
import hashlib
import json

import pytest

from keihan_tracker import ActiveTrainData, KeihanAPIError

from conftest import load_fixture, load_fixture_bytes, make_position_list, run

POSITION_PATH = "/zaisen-up/trainPositionList.json"
STARTTIME_PATH = "/zaisen-up/startTimeList.json"

IRREGULAR_SNAPSHOTS = {
    snapshot["file"]: snapshot
    for snapshot in json.loads(load_fixture_bytes("irregular/manifest.json"))["snapshots"]
}


def _position(name: str) -> bytes:
    return load_fixture_bytes("irregular/" + name)


def test_irregular_corpus_unchanged():
    assert len(IRREGULAR_SNAPSHOTS) == 9
    for file, snapshot in IRREGULAR_SNAPSHOTS.items():
        raw = load_fixture_bytes("irregular/" + file.removesuffix(".gz"))
        assert len(raw) == snapshot["bytes"], file
        assert hashlib.sha256(raw).hexdigest() == snapshot["sha256"], file


# ---------------------------------------------------------------- 方向が一時的に誤る


def test_direction_glitch_corrected(api, fresh_tracker):
    """1001号(wdf 445)は淀に現れた最初の1分だけ上り扱いだった。"""
    tracker = fresh_tracker
    expected = [
        # (方向, 停車中か, 次の駅)
        ("up", True, "淀"),
        ("down", True, "淀"),
        ("down", False, "石清水八幡宮"),
    ]
    for n, (direction, stopping, next_station) in enumerate(expected, start=1):
        api.responses[POSITION_PATH] = _position(f"position_direction_glitch_{n}.json")
        run(tracker.fetch_pos())
        train = tracker.trains[445]
        assert isinstance(train, ActiveTrainData)
        assert train.train_number == "1001"
        assert train.destination.station_name.ja == "中之島"
        assert train.direction == direction, n
        assert train.is_stopping is stopping, n
        assert train.next_station.station_name.ja == next_station, n


# ---------------------------------------------------------------- 行先なし(00)


def test_dest_none_snapshot_parsed(api, fresh_tracker):
    """全列車の行先が 00・destStationCode="" でも取得が止まらない。"""
    api.responses[POSITION_PATH] = _position("position_dest_none.json")
    run(fresh_tracker.fetch_pos())

    trains = fresh_tracker.active_trains
    assert len(trains) == 27
    for train in trains.values():
        assert train.active_destination is None
        # ダイヤの終着駅で補う
        assert train.has_dia
        assert train.destination == next(stop.station for stop in train.route_stations if stop.is_final)


def test_dest_none_keeps_previous_destination(api, fresh_tracker):
    run(fresh_tracker.fetch_pos())
    destination = fresh_tracker.trains[1096].destination

    data = copy.deepcopy(load_fixture("trainPositionList.json"))
    for loc in data["locationObjects"]:
        for info in loc["trainInfoObjects"]:
            info["destStationNumber"] = "00"
            info["destStationCode"] = ""
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())

    assert fresh_tracker.trains[1096].destination == destination


def test_dest_none_without_dia(api, fresh_tracker):
    """行先もダイヤも分からない列車でも、表示・非アクティブ化で止まらない。"""
    api.set_json(STARTTIME_PATH, {"fileCreatedTime": "20261007000000", "fileVersion": "1.0.0", "TrainInfo": []})
    api.responses[POSITION_PATH] = _position("position_dest_none.json")
    run(fresh_tracker.fetch_pos())

    trains = fresh_tracker.active_trains
    assert len(trains) == 27
    for train in trains.values():
        assert not train.has_dia
        assert train.destination is None
        assert "不明行き" in str(train)

    api.set_json(POSITION_PATH, make_position_list("20261007235432"))
    run(fresh_tracker.fetch_pos())
    assert fresh_tracker.active_trains == {}
    for wdf in trains:
        assert fresh_tracker.trains[wdf].status == "completed"


# ---------------------------------------------------------------- 壊れたJSON


def _state(tracker) -> dict:
    return {
        wdf: (type(train), train.direction, getattr(train, "location_row", None), getattr(train, "location_col", None))
        for wdf, train in tracker.trains.items()
    }


@pytest.mark.parametrize("name", ["position_nul_padded.json", "position_truncated.json"])
def test_broken_position_raises_and_keeps_state(api, fresh_tracker, name):
    run(fresh_tracker.fetch_pos())
    before = _state(fresh_tracker)
    position_list = fresh_tracker.train_position_list

    api.responses[POSITION_PATH] = _position(name)
    with pytest.raises(KeihanAPIError):
        run(fresh_tracker.fetch_pos())
    assert _state(fresh_tracker) == before
    assert fresh_tracker.train_position_list is position_list

    # 次の取得で正常なデータが来れば続きから動く
    data = load_fixture("trainPositionList.json")
    data["fileCreatedTime"] = "20261006212500"
    api.set_json(POSITION_PATH, data)
    run(fresh_tracker.fetch_pos())
    assert _state(fresh_tracker) == before


def test_broken_position_is_value_error(api, fresh_tracker):
    """KeihanAPIError は ValueError のサブクラスで、元の例外を __cause__ に持つ"""
    api.responses[POSITION_PATH] = _position("position_nul_padded.json")
    with pytest.raises(ValueError) as e:
        run(fresh_tracker.fetch_pos())
    assert isinstance(e.value, KeihanAPIError)
    assert isinstance(e.value.__cause__, json.JSONDecodeError)


def test_broken_dia_retried_on_next_fetch(api, fresh_tracker):
    """ダイヤが壊れていたら、1時間待たずに次の取得で取り直す"""
    good_dia = api.responses[STARTTIME_PATH]
    run(fresh_tracker.fetch_pos())
    dia = fresh_tracker.starttime_list

    # 5時に日付が変わり、ダイヤを取り直したら壊れていた
    api.set_json(POSITION_PATH, make_position_list("20261007050000"))
    api.responses[STARTTIME_PATH] = good_dia + b"\x00" * 6
    for _ in range(2):  # 壊れたままなら毎回例外（304で黙って古いダイヤを使わない）
        with pytest.raises(KeihanAPIError):
            run(fresh_tracker.fetch_pos())
        assert fresh_tracker.date == datetime.date(2026, 10, 7)
        assert fresh_tracker.starttime_list is dia
    assert api.requests.count(STARTTIME_PATH) == 3

    api.responses[STARTTIME_PATH] = good_dia
    run(fresh_tracker.fetch_pos())
    assert api.requests.count(STARTTIME_PATH) == 4
    assert fresh_tracker.last_regist_dia_date == datetime.date(2026, 10, 7)
    assert all(t.date == datetime.date(2026, 10, 7) for t in fresh_tracker.trains.values())
