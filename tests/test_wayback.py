import hashlib
import json

from keihan_tracker import TrainType
from keihan_tracker.keihan_train.schemes import trainPositionList

from conftest import WAYBACK_SNAPSHOTS, load_fixture_bytes


def test_wayback_corpus_complete_and_unchanged():
    """過去の検証で使った26日分・28件の元データが、欠落・改変なく保存されていること。"""
    assert len(WAYBACK_SNAPSHOTS) == 28
    assert len({snapshot["id"] for snapshot in WAYBACK_SNAPSHOTS}) == 28
    assert len({snapshot["id"][:8] for snapshot in WAYBACK_SNAPSHOTS}) == 26
    assert sum(snapshot["position"] is not None for snapshot in WAYBACK_SNAPSHOTS) == 25
    for snapshot in WAYBACK_SNAPSHOTS:
        for payload in (snapshot["timetable"], snapshot["position"]):
            if payload is None:
                continue
            raw = load_fixture_bytes("wayback/" + payload["file"].removesuffix(".gz"))
            assert hashlib.sha256(raw).hexdigest() == payload["sha256"], payload["file"]
            data = json.loads(raw)
            assert data["fileCreatedTime"] == payload["file_created_time"], payload["file"]
            count = len(data["TrainInfo"]) if "TrainInfo" in data else sum(
                len(loc["trainInfoObjects"]) for loc in data["locationObjects"]
            )
            assert count == payload["train_count"], payload["file"]


def test_special_train_type_prefix_removed():
    """臨時列車の種別は「臨時　　　　特急」のように来る。「臨時」を除いた種別にし、is_special を立てる。"""
    expected = {
        "20250420014659_trainPositionList.json": TrainType.LTD_EXP,
        "20250527125843_trainPositionList.json": TrainType.RAPID_EXP,
    }
    for name, train_type in expected.items():
        raw = json.loads(load_fixture_bytes("wayback/" + name))
        specials = [t["wdfBlockNo"] for loc in raw["locationObjects"]
                    for t in loc["trainInfoObjects"] if t["trainTypeJp"].startswith("臨時")]
        assert specials, name
        parsed = trainPositionList.model_validate(raw)
        trains = {t.wdfBlockNo: t for loc in parsed.locationObjects for t in loc.trainInfoObjects}
        for wdf in specials:
            assert trains[int(wdf)].is_special, (name, wdf)
            assert trains[int(wdf)].trainTypeJp == train_type, (name, wdf)
        assert not any(t.is_special for wdf, t in trains.items() if wdf not in map(int, specials))
