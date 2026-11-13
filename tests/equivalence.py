"""
同値性テスト用に、KHTracker の全列車・全駅のプロパティを書き出す。

v3 で内部を作り直しても、種別・方向・停車駅などの推定が変わっていないことを確かめるため、
v2.8 の出力を tests/fixtures/equivalence/ に保存しておき、test_equivalence.py で比べる。
キーは v2 の名前のまま。v3 で名前を変えたので、この関数で v2 の名前に読み替えて書き出す
（next_station → position.station or position.ahead、is_stopping → status など）。

保存し直すとき（推定を意図して変えたときだけ）:
    python tests/equivalence.py
"""

import datetime
import gzip
import json
import sys
from pathlib import Path
from typing import Any, Callable

FIXTURES = Path(__file__).parent / "fixtures"
EQUIVALENCE_DIR = FIXTURES / "equivalence"


def _station(station) -> Any:
    return None if station is None else station.station_number


def _time(t: datetime.datetime | None) -> str | None:
    return None if t is None else t.strftime("%Y-%m-%d %H:%M")


def _safe(get: Callable[[], Any]) -> Any:
    """例外を投げるプロパティは、例外の名前を値として書き出す"""
    try:
        return get()
    except Exception as e:
        return f"!{type(e).__name__}"


def _stop(stop) -> list:
    flags = ("S" if stop.is_start else "") + ("F" if stop.is_final else "") + ("P" if stop.is_stop else "")
    return [stop.station.station_number, flags, _time(stop.time)]


def dump_tracker(tracker) -> dict:
    from keihan_tracker import ActiveTrainData, TrainStatus

    trains: dict[str, dict] = {}
    for wdf, train in sorted(tracker.trains.items()):
        d: dict[str, Any] = {
            "active": isinstance(train, ActiveTrainData),
            "date": train.date.isoformat(),
            "has_dia": train.has_dia,
            "has_premiumcar": train.has_premiumcar,
            "train_formation": train.train_formation,
            "delay_minutes": train.delay_minutes,
            "was_active": train.was_active,
            "line": _safe(lambda: train.line),
            "direction": _safe(lambda: train.direction),
            "train_type": _safe(lambda: train.train_type.value),
            "start_station": _safe(lambda: _station(train.start_station)),
            "destination": _safe(lambda: _station(train.destination)),
            "route": [_stop(s) for s in train.route_stations],
            "history": len(train.history),
        }
        if isinstance(train, ActiveTrainData):
            d.update({
                "train_number": train.train_number,
                "is_special": train.is_special,
                "cars": train.cars,
                "delay_text": train.delay_text.ja,
                "location": [train.location_col, train.location_row],
                "position_since": _time(train.position_since),
                "staying_seconds": train.staying_time.total_seconds(),
                "next_station": _safe(lambda: _station(train.position.station or train.position.ahead)),
                "next_stop_station": _safe(lambda: _station(train.current_or_next_stop)),
                "is_stopping": _safe(lambda: None if train.status is TrainStatus.UNKNOWN else train.status is TrainStatus.STOPPED),
                "is_at_station": _safe(lambda: train.position.station is not None),
                "is_at_start_station": _safe(lambda: train.is_at_start_station),
                "estimated_delay_minutes": _safe(lambda: train.estimated_delay_minutes),
            })
        trains[str(wdf)] = d

    stations: dict[str, dict] = {}
    for number, station in sorted(tracker.stations.items()):
        stations[str(number)] = {
            "name": station.station_name.ja,
            "line": sorted(station.line),
            "arriving": _safe(lambda: sorted(t.wdf for t in station.arriving_trains)),
            "trains": [t.wdf for t, _ in station.trains],
            "upcoming": _safe(lambda: [t.wdf for t, _ in station.upcoming_trains]),
        }

    max_delay = tracker.max_delay_train
    return {
        "date": tracker.date.isoformat(),
        "position_created_at": _time(tracker.position_created_at),
        "max_delay_train": None if max_delay is None else max_delay.wdf,
        "trains": trains,
        "stations": stations,
    }


def dump(tracker) -> dict:
    return dump_tracker(tracker)


def golden_path(name: str) -> Path:
    return EQUIVALENCE_DIR / f"{name}.json.gz"


def load_golden(name: str) -> dict:
    return json.loads(gzip.decompress(golden_path(name).read_bytes()))


def save_golden(name: str, data: dict) -> None:
    EQUIVALENCE_DIR.mkdir(exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # mtime=0 にして、内容が同じなら同じバイト列にする
    golden_path(name).write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))


def all_trackers():
    """(名前, KHTracker) を、平日・土休日・Wayback の順に返す"""
    from conftest import (WAYBACK_SNAPSHOTS, MockKeihanAPI, load_fixture_bytes,
                          make_position_list, make_tracker, make_wayback_tracker, run)

    t = make_tracker(MockKeihanAPI())
    run(t.fetch_pos())
    yield "weekday", t

    api = MockKeihanAPI()
    api.responses["/zaisen-up/startTimeList.json"] = load_fixture_bytes("startTimeList_holiday.json")
    api.set_json("/zaisen-up/trainPositionList.json", make_position_list("20260614133525"))
    t = make_tracker(api)
    run(t.fetch_pos())
    yield "holiday", t

    for snapshot in WAYBACK_SNAPSHOTS:
        yield "wayback-" + snapshot["id"], make_wayback_tracker(snapshot)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    for name, tracker in all_trackers():
        save_golden(name, dump(tracker))
        print(name, golden_path(name).stat().st_size)
