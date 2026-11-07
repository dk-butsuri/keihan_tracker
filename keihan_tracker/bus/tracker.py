from httpx import AsyncClient
import datetime
import urllib.parse
import time
from keihan_tracker.bus.parsing import build_diagram_dgmpl, parse_diagram
from keihan_tracker.bus.schemes import BusDiagram, BusLocationResponse
from keihan_tracker.keihan_train.tracker import JST, KeihanAPIError, USER_AGENT

UPDATE_URL = "https://busnavi.keihanbus.jp/pc/busstateupd"
DIAGRAM_URL = "https://busnavi.keihanbus.jp/pc/linediagram"
RATE_LIMIT_SECONDS = 30
# (バス停名, のりば番号):(取得したtime.monotonic(), レスポンス)
_cache: dict[tuple[str, int], tuple[float, BusLocationResponse]] = {}
_diagram_cache: dict[str, tuple[float, BusDiagram]] = {}

async def get_khbus_info(stop_name:str, stop_num:int=1) -> BusLocationResponse:
    """
    バス停の接近情報を取得します。同じバス停は30秒以内なら前回の結果を返します。
    応答が読み込めない（壊れたJSON・想定外の形式）ときは KeihanAPIError、通信の失敗は httpx.HTTPError を送出します。
    """
    key = (stop_name, stop_num)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < RATE_LIMIT_SECONDS:
        return cached[1]
    async with AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        dgmpl = f"{stop_name}:{stop_num}::"
        result = await client.post(UPDATE_URL,data={"dgmpl":urllib.parse.quote(dgmpl), "sort4":"0"})
        result.raise_for_status()
        try:
            res = BusLocationResponse.model_validate_json(result.text)
            for bus in res.body.busstates:
                bus.dgmpl = build_diagram_dgmpl(stop_name, stop_num, bus.html, bus.html_sp)
        except ValueError as e: # pydantic.ValidationError（壊れたJSONも含む）
            raise KeihanAPIError(f"{UPDATE_URL} を読み込めませんでした: {e}") from e
    # 取得できてから記録する（失敗したときは次の呼び出しで取り直す）
    _cache[key] = (time.monotonic(), res)
    return res


async def fetch_diagram(dgmpl: str) -> BusDiagram:
    """便の発着時刻表を取得する。同じ dgmpl は30秒間キャッシュする。

    get_khbus_info() の各便の dgmpl を渡す。HTML に便のキーがない場合は
    dgmpl が None になるため、その便は取得できない。
    応答の形式が想定と異なる場合は KeihanAPIError、通信の失敗は httpx.HTTPError。
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
        try:
            diagram = parse_diagram(result.text, dgmpl)
        except ValueError as e:
            raise KeihanAPIError(f"{DIAGRAM_URL} を読み込めませんでした: {e}") from e
    _diagram_cache[dgmpl] = (time.monotonic(), diagram)
    return diagram
