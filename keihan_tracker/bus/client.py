"""京阪バスの取得とキャッシュ。パースは bus.parse に任せる。"""

from contextlib import asynccontextmanager
from httpx import AsyncClient
import datetime
import re
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from .parse import parse_busstate, parse_diagram, UPDATE_URL
from .schemas import BusDiagram, BusLocationResponse
from ..train.client import USER_AGENT, write_snapshot
from ..train.parse import KeihanAPIError
from ..train.state import JST

DIAGRAM_URL = "https://busnavi.keihanbus.jp/pc/linediagram"
RATE_LIMIT_SECONDS = 30


def _snapshot_name(*parts: object) -> str:
    """ファイル名に使えない文字を _ にして、parts を _ でつなぐ。"""
    return "_".join(re.sub(r'[\\/:*?"<>|\s]', "_", str(part)) for part in parts)


class BusClient:
    """
    京阪バスの取得クライアント。通信クライアント・30秒のキャッシュ・保存済み本文をインスタンスに持つ。
    async with で使うか、使い終えたら aclose() する。同じイベントループ内で使う。
    """
    def __init__(self, snapshot_dir: Optional[str | Path] = None, *, _per_request: bool = False) -> None:
        # モジュール関数だけは、asyncio.run を繰り返してもよいように通信ごとに作る
        self._per_request = _per_request
        self.web = None if _per_request else AsyncClient(headers={"User-Agent": USER_AGENT})
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir is not None else None
        self._cache: dict[tuple[str, int], tuple[float, BusLocationResponse]] = {}
        self._diagram_cache: dict[str, tuple[float, BusDiagram]] = {}
        self._last_snapshot: dict[str, bytes] = {}

    async def aclose(self) -> None:
        """通信クライアントを閉じる。"""
        if self.web is not None:
            await self.web.aclose()

    async def __aenter__(self) -> "BusClient":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.aclose()

    @asynccontextmanager
    async def _connection(self):
        if self._per_request:
            async with AsyncClient(headers={"User-Agent": USER_AGENT}) as web:
                yield web
        else:
            assert self.web is not None
            yield self.web

    async def get_info(self, stop_name: str, stop_num: int = 1) -> BusLocationResponse:
        """接近情報を取得する。同じバス停・のりばは30秒キャッシュする。"""
        return await self._get_info(stop_name, stop_num, self.snapshot_dir)

    async def _get_info(
            self, stop_name: str, stop_num: int, snapshot_dir: Optional[str | Path],
            ) -> BusLocationResponse:
        key = (stop_name, stop_num)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < RATE_LIMIT_SECONDS:
            return cached[1]
        async with self._connection() as web:
            dgmpl = f"{stop_name}:{stop_num}::"
            result = await web.post(UPDATE_URL, data={"dgmpl": urllib.parse.quote(dgmpl), "sort4": "0"})
            result.raise_for_status()
            # 読み込めない応答こそ残したいので、パースの前に保存する
            if snapshot_dir is not None:
                name = _snapshot_name("busstateupd", stop_name, stop_num) + ".json"
                await write_snapshot(Path(snapshot_dir), name, result.content, self._last_snapshot)
            res = parse_busstate(result.text, stop_name, stop_num)
        self._cache[key] = (time.monotonic(), res)
        return res

    async def fetch_diagram(self, dgmpl: str) -> BusDiagram:
        """便の発着時刻表を取得する。同じ dgmpl は30秒キャッシュする。"""
        return await self._fetch_diagram(dgmpl, self.snapshot_dir)

    async def _fetch_diagram(self, dgmpl: str, snapshot_dir: Optional[str | Path]) -> BusDiagram:
        cached = self._diagram_cache.get(dgmpl)
        if cached and time.monotonic() - cached[0] < RATE_LIMIT_SECONDS:
            return cached[1]
        async with self._connection() as web:
            result = await web.get(DIAGRAM_URL, params={
                "mode": 4,
                "dgmpl": dgmpl,
                "dt": datetime.datetime.now(JST).strftime("%Y%m%d%H%M"),
            })
            result.raise_for_status()
            if snapshot_dir is not None:
                name = _snapshot_name("linediagram", dgmpl) + ".html"
                await write_snapshot(Path(snapshot_dir), name, result.content, self._last_snapshot)
            try:
                diagram = parse_diagram(result.text, dgmpl)
            except ValueError as e:
                raise KeihanAPIError(f"{DIAGRAM_URL} を読み込めませんでした: {e}") from e
        self._diagram_cache[dgmpl] = (time.monotonic(), diagram)
        return diagram
