from httpx import AsyncClient
import urllib.parse
import time
from keihan_tracker.bus.schemes import BusLocationResponse

UPDATE_URL = "https://busnavi.keihanbus.jp/pc/busstateupd"
RATE_LIMIT_SECONDS = 30
# (バス停名, のりば番号):(取得したtime.monotonic(), レスポンス)
_cache: dict[tuple[str, int], tuple[float, BusLocationResponse]] = {}

async def get_khbus_info(stop_name:str, stop_num:int=1) -> BusLocationResponse:
    """バス停の接近情報を取得します。同じバス停は30秒以内なら前回の結果を返します。"""
    key = (stop_name, stop_num)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < RATE_LIMIT_SECONDS:
        return cached[1]
    async with AsyncClient() as client:
        dgmpl = f"{stop_name}:{stop_num}::"
        result = await client.post(UPDATE_URL,data={"dgmpl":urllib.parse.quote(dgmpl), "sort4":"0"})
        result.raise_for_status()
        res = BusLocationResponse.model_validate_json(result.text)
    # 取得できてから記録する（失敗したときは次の呼び出しで取り直す）
    _cache[key] = (time.monotonic(), res)
    return res
