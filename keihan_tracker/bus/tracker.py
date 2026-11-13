from pathlib import Path
from typing import Optional

from .client import BusClient
from .schemas import BusDiagram, BusLocationResponse
from .parse import parse_busstate

_default_client = BusClient(_per_request=True)


async def get_khbus_info(stop_name:str, stop_num:int=1, *, snapshot_dir: Optional[str | Path] = None) -> BusLocationResponse:
    """
    バス停の接近情報を取得します。同じバス停は30秒以内なら前回の結果を返します。
    応答が読み込めない（壊れたJSON・想定外の形式）ときは KeihanAPIError、通信の失敗は httpx.HTTPError を送出します。
    snapshot_dir を指定すると、取得した応答をそのまま
    snapshot_dir/YYYYMMDD/HHMMSS.mmm_busstateupd_バス停名_のりば.json.gz に保存します（KHTracker と同じ。キャッシュを返したときは保存しない）。
    """
    return await _default_client._get_info(stop_name, stop_num, snapshot_dir)


async def fetch_diagram(dgmpl: str, *, snapshot_dir: Optional[str | Path] = None) -> BusDiagram:
    """便の発着時刻表を取得する。同じ dgmpl は30秒間キャッシュする。

    get_khbus_info() の各便の dgmpl を渡す。HTML に便のキーがない場合は
    dgmpl が None になるため、その便は取得できない。
    応答の形式が想定と異なる場合は KeihanAPIError、通信の失敗は httpx.HTTPError。
    snapshot_dir を指定すると、取得した応答を
    snapshot_dir/YYYYMMDD/HHMMSS.mmm_linediagram_dgmpl.html.gz に保存する（dgmpl の「:」などは _ に置き換える）。
    """
    return await _default_client._fetch_diagram(dgmpl, snapshot_dir)
