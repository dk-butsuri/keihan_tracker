from httpx import AsyncClient
import datetime
import re
import urllib.parse
import time
from pathlib import Path
from typing import Optional
from keihan_tracker.bus.parse import build_diagram_dgmpl, parse_bus_delay, parse_diagram
from keihan_tracker.bus.schemas import BusDiagram, BusLocationResponse
from keihan_tracker.train.tracker import JST, KeihanAPIError, USER_AGENT, write_snapshot

UPDATE_URL = "https://busnavi.keihanbus.jp/pc/busstateupd"
DIAGRAM_URL = "https://busnavi.keihanbus.jp/pc/linediagram"
RATE_LIMIT_SECONDS = 30
# (バス停名, のりば番号):(取得したtime.monotonic(), レスポンス)
_cache: dict[tuple[str, int], tuple[float, BusLocationResponse]] = {}
_diagram_cache: dict[str, tuple[float, BusDiagram]] = {}
# スナップショットのファイル名:前回保存した内容
_last_snapshot: dict[str, bytes] = {}


def _snapshot_name(*parts: object) -> str:
    """ファイル名に使えない文字（Windows の \\/:*?"<>| など）を _ にして、parts を _ でつなぐ"""
    return "_".join(re.sub(r'[\\/:*?"<>|\s]', "_", str(part)) for part in parts)


async def get_khbus_info(stop_name:str, stop_num:int=1, *, snapshot_dir: Optional[str | Path] = None) -> BusLocationResponse:
    """
    バス停の接近情報を取得します。同じバス停は30秒以内なら前回の結果を返します。
    応答が読み込めない（壊れたJSON・想定外の形式）ときは KeihanAPIError、通信の失敗は httpx.HTTPError を送出します。
    snapshot_dir を指定すると、取得した応答をそのまま
    snapshot_dir/YYYYMMDD/HHMMSS.mmm_busstateupd_バス停名_のりば.json.gz に保存します（KHTracker と同じ。キャッシュを返したときは保存しない）。
    """
    key = (stop_name, stop_num)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < RATE_LIMIT_SECONDS:
        return cached[1]
    text = await _fetch_busstate(stop_name, stop_num, snapshot_dir)
    res = parse_busstate(text, stop_name, stop_num)
    # 取得できてから記録する（失敗したときは次の呼び出しで取り直す）
    _cache[key] = (time.monotonic(), res)
    return res


async def _fetch_busstate(stop_name: str, stop_num: int, snapshot_dir: Optional[str | Path]) -> str:
    """接近情報を取得して本文を返す。snapshot_dir があれば保存する。"""
    async with AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        dgmpl = f"{stop_name}:{stop_num}::"
        result = await client.post(UPDATE_URL,data={"dgmpl":urllib.parse.quote(dgmpl), "sort4":"0"})
        result.raise_for_status()
        # 読み込めない応答こそ残したいので、パースの前に保存する
        if snapshot_dir is not None:
            name = _snapshot_name("busstateupd", stop_name, stop_num) + ".json"
            await write_snapshot(Path(snapshot_dir), name, result.content, _last_snapshot)
        return result.text


def parse_busstate(text: str, stop_name: str, stop_num: int) -> BusLocationResponse:
    """
    接近情報の本文を読み、各便に dgmpl・定刻・遅れを入れる。通信はしない。
    読み込めなければ KeihanAPIError。
    """
    try:
        res = BusLocationResponse.model_validate_json(text)
        for bus in res.body.busstates:
            bus.dgmpl = build_diagram_dgmpl(stop_name, stop_num, bus.html, bus.html_sp)
            delay = parse_bus_delay(bus.html, bus.html_sp)
            bus.scheduled_time = delay.scheduled_time
            bus.delay_text = delay.delay_text
            bus.delay_minutes = delay.delay_minutes
    except ValueError as e: # pydantic.ValidationError（壊れたJSONも含む）
        raise KeihanAPIError(f"{UPDATE_URL} を読み込めませんでした: {e}") from e
    return res


async def fetch_diagram(dgmpl: str, *, snapshot_dir: Optional[str | Path] = None) -> BusDiagram:
    """便の発着時刻表を取得する。同じ dgmpl は30秒間キャッシュする。

    get_khbus_info() の各便の dgmpl を渡す。HTML に便のキーがない場合は
    dgmpl が None になるため、その便は取得できない。
    応答の形式が想定と異なる場合は KeihanAPIError、通信の失敗は httpx.HTTPError。
    snapshot_dir を指定すると、取得した応答を
    snapshot_dir/YYYYMMDD/HHMMSS.mmm_linediagram_dgmpl.html.gz に保存する（dgmpl の「:」などは _ に置き換える）。
    """
    cached = _diagram_cache.get(dgmpl)
    if cached and time.monotonic() - cached[0] < RATE_LIMIT_SECONDS:
        return cached[1]
    async with AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        result = await client.get(DIAGRAM_URL, params={
            "mode": 4,
            "dgmpl": dgmpl,
            "dt": datetime.datetime.now(JST).strftime("%Y%m%d%H%M"),
        })
        result.raise_for_status()
        if snapshot_dir is not None:
            name = _snapshot_name("linediagram", dgmpl) + ".html"
            await write_snapshot(Path(snapshot_dir), name, result.content, _last_snapshot)
        try:
            diagram = parse_diagram(result.text, dgmpl)
        except ValueError as e:
            raise KeihanAPIError(f"{DIAGRAM_URL} を読み込めませんでした: {e}") from e
    _diagram_cache[dgmpl] = (time.monotonic(), diagram)
    return diagram
