"""
テスト共通の準備。

tests/fixtures/ に保存した実際のAPIレスポンス（2026-10-06 21:24頃に取得）を
httpx.MockTransport で返すことで、ネットワークに接続せずに KHTracker を動かす。
"""

import asyncio
import datetime
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from keihan_tracker import KHTracker
from keihan_tracker.keihan_train.tracker import JST

FIXTURES = Path(__file__).parent / "fixtures"

# APIのパス → fixtureのファイル名
URL_TO_FIXTURE = {
    "/zaisen/select_station.json": "select_station.json",
    "/zaisen/transferGuideInfo.json": "transferGuideInfo.json",
    "/zaisen-up/trainPositionList.json": "trainPositionList.json",
    "/zaisen-up/startTimeList.json": "startTimeList.json",
}


def load_fixture_bytes(name: str) -> bytes:
    """fixtureを読み込む。name.json が無ければ name.json.gz を探す。"""
    path = FIXTURES / name
    if path.exists():
        return path.read_bytes()
    return gzip.decompress((FIXTURES / f"{name}.gz").read_bytes())


def load_fixture(name: str) -> Any:
    return json.loads(load_fixture_bytes(name))


def make_position_list(
    file_created_time: str,
    location_objects: Optional[list[dict]] = None,
) -> dict:
    """テスト用の最小限の trainPositionList.json を組み立てる。"""
    return {
        "fileCreatedTime": file_created_time,
        "fileVersion": "1.0.0",
        "linkNum": "0",
        "locationObjects": location_objects or [],
    }


class MockKeihanAPI:
    """京阪APIのモック。responses を書き換えると次回以降の応答が変わる。"""

    def __init__(self) -> None:
        self.responses: dict[str, bytes] = {
            path: load_fixture_bytes(name) for path, name in URL_TO_FIXTURE.items()
        }
        self.requests: list[str] = []
        self.last_headers: dict[str, httpx.Headers] = {}  # パス:最後に受け取ったリクエストヘッダー

    def set_json(self, path: str, data: Any) -> None:
        self.responses[path] = json.dumps(data, ensure_ascii=False).encode("utf-8")

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        self.last_headers[path] = request.headers
        body = self.responses.get(path)
        if body is None:
            return httpx.Response(404)
        # 本物のサーバーと同じく、ETagが一致すれば 304 Not Modified を返す
        etag = f'"{hashlib.sha1(body).hexdigest()}"'
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, content=body, headers={"ETag": etag})


def make_tracker(api: MockKeihanAPI) -> KHTracker:
    tracker = KHTracker(rate_limit=0)
    tracker.web = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
    # KHTracker.date は初期化時の実時刻から決まるため、fixtureの取得日にそろえる
    created = load_fixture("trainPositionList.json")["fileCreatedTime"]
    tracker.date = datetime.datetime.strptime(created, "%Y%m%d%H%M%S").replace(tzinfo=JST).date()
    return tracker


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def api() -> MockKeihanAPI:
    return MockKeihanAPI()


@pytest.fixture
def fresh_tracker(api: MockKeihanAPI) -> KHTracker:
    """fetch_pos 前の KHTracker（テスト内で状態を変えるとき用）"""
    return make_tracker(api)


@pytest.fixture(scope="module")
def tracker() -> KHTracker:
    """fixtureで fetch_pos 済みの KHTracker（読み取り専用で使うこと）"""
    t = make_tracker(MockKeihanAPI())
    run(t.fetch_pos())
    return t
