"""
公式APIが返した異常なデータ（2026-10-07〜10-10 に snapshot_dir で保存）での挙動。

ダイヤは保存されていないため、tests/fixtures/ の平日ダイヤと組み合わせている。
"""

import copy
import hashlib
import json

from keihan_tracker import ActiveTrainData

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
