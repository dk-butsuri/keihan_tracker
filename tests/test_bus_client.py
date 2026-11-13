"""バスクライアントの寿命と、インスタンスごとのキャッシュ。"""

import gzip

import httpx
import pytest

from keihan_tracker import BusClient, parse_busstate, parse_diagram, get_khbus_info
from keihan_tracker.bus import client as client_module
from keihan_tracker.bus import tracker as tracker_module
from conftest import load_fixture_bytes, run

DGMPL = "同志社香里:1:1:75120:17:12"


@pytest.fixture
def clients(monkeypatch):
    created = []
    requests = []

    def handler(request):
        requests.append(request)
        name = "bus/busstateupd.json" if request.method == "POST" else "bus/linediagram.html"
        return httpx.Response(200, content=load_fixture_bytes(name))

    def factory(**kwargs):
        web = httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
        created.append(web)
        return web

    monkeypatch.setattr(client_module, "AsyncClient", factory)
    return created, requests


def test_client_reuses_connection_and_keeps_caches_separate(clients, tmp_path):
    created, requests = clients

    async def check():
        async with BusClient(tmp_path / "a") as first, BusClient(tmp_path / "b") as second:
            a = await first.get_info("同志社香里")
            assert await first.get_info("同志社香里") is a
            assert await second.get_info("同志社香里") is not a
            diagram = await first.fetch_diagram(DGMPL)
            assert await first.fetch_diagram(DGMPL) is diagram
            assert await second.fetch_diagram(DGMPL) is not diagram
            assert len(created) == 2
            assert not any(web.is_closed for web in created)
            assert first._last_snapshot is not second._last_snapshot
        assert all(web.is_closed for web in created)

    run(check())
    assert len(requests) == 4
    for directory in (tmp_path / "a", tmp_path / "b"):
        saved = list(directory.rglob("*.gz"))
        assert len(saved) == 2
        for path in saved:
            name = "bus/busstateupd.json" if "busstateupd_" in path.name else "bus/linediagram.html"
            assert gzip.decompress(path.read_bytes()) == load_fixture_bytes(name)


def test_module_functions_close_each_connection_across_event_loops(clients, monkeypatch):
    created, requests = clients
    singleton = BusClient(_per_request=True)
    monkeypatch.setattr(tracker_module, "_default_client", singleton)
    first = run(get_khbus_info("同志社香里"))
    assert run(get_khbus_info("同志社香里")) is first
    run(get_khbus_info("同志社香里", 2))
    assert len(created) == len(requests) == 2
    assert all(web.is_closed for web in created)


def test_client_closes_on_exception(clients):
    async def check():
        with pytest.raises(RuntimeError):
            async with BusClient():
                raise RuntimeError("test")
    run(check())
    assert clients[0][0].is_closed


def test_parsers_are_available_without_a_client(clients):
    response = parse_busstate(load_fixture_bytes("bus/busstateupd.json").decode("utf-8"), "同志社香里", 1)
    diagram = parse_diagram(load_fixture_bytes("bus/linediagram.html").decode("utf-8"), DGMPL)
    assert response.body.busstates
    assert diagram.stops
    assert clients == ([], [])
