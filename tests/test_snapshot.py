import gzip

import pytest
from pydantic import ValidationError

from conftest import load_fixture_bytes, make_position_list, make_tracker, run

POSITION_PATH = "/zaisen-up/trainPositionList.json"


def _snapshots(root):
    return sorted(p.name.split("_", 1)[1] for p in root.rglob("*.gz"))


def test_no_snapshot_by_default(api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tracker = make_tracker(api)
    run(tracker.fetch_pos())
    assert list(tmp_path.rglob("*")) == []


def test_snapshot_saves_raw_responses(api, tmp_path):
    tracker = make_tracker(api)
    tracker.snapshot_dir = tmp_path
    run(tracker.fetch_pos())

    assert _snapshots(tmp_path) == [
        "select_station.json.gz",
        "startTimeList.json.gz",
        "trainPositionList.json.gz",
        "transferGuideInfo.json.gz",
    ]
    saved = next(tmp_path.rglob("*_trainPositionList.json.gz"))
    assert gzip.decompress(saved.read_bytes()) == load_fixture_bytes("trainPositionList.json")


def test_snapshot_skips_unchanged_response(api, tmp_path):
    tracker = make_tracker(api)
    tracker.snapshot_dir = tmp_path
    run(tracker.fetch_pos())
    run(tracker.fetch_pos())  # 同じ内容
    assert _snapshots(tmp_path).count("trainPositionList.json.gz") == 1

    api.set_json(POSITION_PATH, make_position_list("20261006213000"))
    run(tracker.fetch_pos())
    assert _snapshots(tmp_path).count("trainPositionList.json.gz") == 2


def test_snapshot_saved_even_if_validation_fails(api, tmp_path):
    """仕様変更でパースに失敗したレスポンスこそ残したい"""
    tracker = make_tracker(api)
    tracker.snapshot_dir = tmp_path
    api.responses[POSITION_PATH] = b'{"unexpected": true}'
    with pytest.raises(ValidationError):
        run(tracker.fetch_pos())

    saved = next(tmp_path.rglob("*_trainPositionList.json.gz"))
    assert gzip.decompress(saved.read_bytes()) == b'{"unexpected": true}'


def test_snapshot_failure_does_not_stop_fetch(api, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")  # ディレクトリを作れない場所を指定する
    tracker = make_tracker(api)
    tracker.snapshot_dir = blocker
    with pytest.warns(UserWarning, match="スナップショット"):
        run(tracker.fetch_pos())
    assert tracker.active_trains
