import datetime

import httpx
import pytest

from keihan_tracker import get_ekispert_delay
from keihan_tracker.delay_tracker import JST, _announced_at

from conftest import run


def test_announced_at_same_year():
    now = datetime.datetime(2026, 10, 22, 19, 0, tzinfo=JST)
    assert _announced_at(10, 22, 18, 30, now) == datetime.datetime(2026, 10, 22, 18, 30, tzinfo=JST)


def test_announced_at_previous_year_after_new_year():
    now = datetime.datetime(2027, 1, 1, 0, 10, tzinfo=JST)
    assert _announced_at(12, 31, 23, 50, now) == datetime.datetime(2026, 12, 31, 23, 50, tzinfo=JST)


def test_ekispert_error_hides_api_key(monkeypatch):
    requested: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        return httpx.Response(401)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda: original(transport=httpx.MockTransport(handler)))

    with pytest.raises(httpx.HTTPStatusError) as e:
        run(get_ekispert_delay("SECRET123"))

    assert requested[0].params["key"] == "SECRET123"
    assert requested[0].params["prefectureCode"] == "26:27:28"
    assert "SECRET123" not in str(e.value)
    assert "SECRET123" not in str(e.value.request.url)
    assert e.value.__cause__ is None and e.value.__suppress_context__
