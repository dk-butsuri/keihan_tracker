import hashlib
import json

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
