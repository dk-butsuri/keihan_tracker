from httpx import AsyncClient
import urllib.parse
from keihan_tracker.bus.schemes import BusLocationResponse

UPDATE_URL = "https://busnavi.keihanbus.jp/pc/busstateupd"

async def get_khbus_info(stop_name:str, stop_num:int=1):
    async with AsyncClient() as client:
        dgmpl = f"{stop_name}:{stop_num}::"
        result = await client.post(UPDATE_URL,data={"dgmpl":urllib.parse.quote(dgmpl), "sort4":"0"})
        result.raise_for_status()
        res = BusLocationResponse.model_validate_json(result.text)
        return res