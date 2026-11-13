"""
比較用データを採取した手順。通常のテストでは実行せず、signage にも依存しない。
main.py の該当関数を AST で取り出してそのまま実行するため、Webアプリや通信処理は起動しない。
表示文言にしか使わない関数だけ空の結果にする。旧プロパティはこのプロセス内だけで読み替える。
使い方: python tests/eta_signage_reference.py <リポジトリ外の signage clone> <新規の出力先.json.gz>
"""

import ast
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Optional

from pydantic import BaseModel
from keihan_tracker import (
    KHState, ActiveTrainData, parse_select_station, parse_positions, parse_timetable,
    estimate_arrival,
)
from keihan_tracker.train.state import JST

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    path = FIXTURES / name
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes())
    return path.read_bytes() if path.exists() else gzip.decompress(Path(str(path) + ".gz").read_bytes())


def build_state(timetable, positions):
    state = KHState()
    state.apply_stations(parse_select_station(read("select_station.json").decode("utf-8")))
    state.apply_positions(parse_positions(positions.decode("utf-8")))
    state.apply_timetable(parse_timetable(read(timetable).decode("utf-8")))
    return state


def parameters(repo, filename, name):
    tree = ast.parse((repo / "tests" / filename).read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.literal_eval(function.decorator_list[0].args[1])


class StationView:
    def __init__(self, station, upcoming):
        self.station_number = station.station_number
        self.station_name = station.station_name
        self.upcoming_trains = upcoming

    def __eq__(self, other):
        return getattr(other, "station_number", None) == self.station_number


def collect(repo, output):
    if output.exists():
        raise FileExistsError("既存の期待値は上書きしません")
    source = (repo / "main.py").read_bytes()
    tree = ast.parse(source)
    names = {"_is_barely_moving", "_is_unreliable", "_on_through_line", "_station_row",
             "_barely_moving_rows", "_blocked_ahead", "_within_estimate_window",
             "_corrected_delay_minutes", "_ordered_trains", "_apply_leading_estimate_mask", "TrainInfo"}
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    clock = [None]

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    namespace = {
        "__name__": "signage_eta_reference",
        "datetime": datetime,
        "Optional": Optional, "BaseModel": BaseModel, "ActiveTrainData": ActiveTrainData,
        "JST": JST, "_cfg": {}, "_THROUGH_LINES": ("京阪本線・鴨東線", "中之島線"),
        "_estimated_delay_minutes": lambda train: None,
        "_position_view": lambda *args: None, "_sub_station_fields": lambda *args: {},
        "_line_train_fields": lambda *args: {},
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(repo / "main.py"), "exec"), namespace)
    namespace["datetime"] = SimpleNamespace(datetime=Clock, timedelta=datetime.timedelta)
    ActiveTrainData.is_stopping = property(lambda train: train.stopped_at is not None)
    ActiveTrainData.next_stop_station = property(lambda train: train.current_or_next_stop)
    ActiveTrainData.next_station = property(lambda train: train.position.station or train.position.ahead)

    manifest = json.loads(read("wayback/manifest.json"))["snapshots"]
    inputs = [("weekday", "startTimeList.json", read("trainPositionList.json"))]
    inputs.append(("holiday", "startTimeList_holiday.json",
                   json.dumps({"fileCreatedTime": "20260614133525", "fileVersion": "1.0.0",
                               "linkNum": "0", "locationObjects": []}).encode()))
    inputs.extend(("wayback-" + entry["id"], "wayback/" + entry["timetable"]["file"],
                   read("wayback/" + entry["position"]["file"]))
                  for entry in manifest if entry["position"] is not None)
    result = {
        "repository": "https://github.com/dk-butsuri/keihan_signage",
        "commit": subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
        "main_sha256": hashlib.sha256(source).hexdigest(), "functions": sorted(names),
        "window_cases": parameters(repo, "test_estimate_window.py", "test_estimate_window_boundaries"),
        "correction_cases": parameters(repo, "test_estimate_window.py", "test_corrected_delay"),
        "arrival_window_cases": parameters(repo, "test_tracker_250.py", "test_arrival_estimate_window"),
        "blocked_cases": parameters(repo, "test_tracker_250.py", "test_blocked_by_train_ahead"),
        "growing_cases": parameters(repo, "test_tracker_250.py", "test_estimate_hidden_while_delay_grows"),
        "stuck_cases": parameters(repo, "test_tracker_250.py", "test_long_stay_hides_estimate_except_at_start_station"),
        "snapshots": [], "cases": [], "differences": [],
    }
    for snapshot, timetable, positions in inputs:
        state = build_state(timetable, positions)
        clock[0] = state.position_created_at
        namespace["tracker"] = state
        eligible = {number: {train.wdf for train, stop in station.upcoming_trains}
                    for number, station in state.stations.items()}
        count = 0
        for train in state.active_trains.values():
            for stop in train.stop_stations:
                station = stop.station
                namespace["_cfg"] = {"trains": {"station_id": station.station_number}}
                upcoming = [(train, stop)] if train.wdf in eligible[station.station_number] else []
                rows = namespace["_ordered_trains"](StationView(station, upcoming), 0, 10000)
                rows = [*rows[0], *rows[1]]
                expected = None
                if rows and rows[0].estimated_time is not None:
                    remaining = (stop.time + datetime.timedelta(minutes=train.delay_minutes) - clock[0]).total_seconds() / 60
                    corrected = namespace["_corrected_delay_minutes"](remaining, train.delay_minutes)
                    expected = stop.time + datetime.timedelta(minutes=round(corrected))
                    assert expected.strftime("%H:%M") == rows[0].estimated_time
                actual = estimate_arrival(train, station, now=clock[0])
                case = {"snapshot": snapshot, "wdf": train.wdf, "station": station.station_number,
                        "now": clock[0].isoformat(), "expected": expected.isoformat() if expected else None}
                if actual.time != expected:
                    difference = {**case, "library": actual.time.isoformat() if actual.time else None,
                                  "reason": actual.reason.value if actual.reason else None}
                    result["differences"].append(difference)
                result["cases"].append(case)
                count += 1
        result["snapshots"].append({"id": snapshot, "active": len(state.active_trains), "cases": count})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0))
    print(json.dumps({"snapshots": len(result["snapshots"]), "cases": len(result["cases"]),
                      "differences": len(result["differences"]), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    collect(Path(sys.argv[1]), Path(sys.argv[2]))
