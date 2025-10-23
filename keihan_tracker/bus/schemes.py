from typing import List, Optional
from pydantic import BaseModel, model_validator


class Head(BaseModel):
    errorcode: int
    objid: str

class BusStatePrms(BaseModel):
    order: int                 # 接近順位
    lat: float                 # 緯度
    lon: float                 # 経度
    heading: int               # 方位
    guid: str                  # GUID
    stop_id: int               # 停留所ID？
    platform_index: int        # のりばインデックス？
    route: str                 # 系統番号（例: [22]）
    destination: str           # 行先
    via: str                   # 経由
    status: str                # 状態（到着済など）
    timetable: str             # 時刻文字列（例: 17:30 到着予定）

    @classmethod
    def from_string(cls, raw: str):
        parts = raw.split(":")
        return cls(
            order=int(parts[0]),
            lat=float(parts[1]),
            lon=float(parts[2]),
            heading=int(parts[3]),
            guid=parts[4],
            stop_id=int(parts[5]),
            platform_index=int(parts[6]),
            route=parts[7],
            destination=parts[8],
            via=parts[9],
            status=parts[10],
            timetable=parts[11].replace("__________", ":"),
        )

class BusState(BaseModel):
    html: Optional[str] = None
    html_sp: Optional[str] = None
    busstateprms: BusStatePrms

    @model_validator(mode="before")
    def parse_prms(cls, values):
        if isinstance(values, dict) and "busstateprms" in values and isinstance(values["busstateprms"], str):
            values["busstateprms"] = BusStatePrms.from_string(values["busstateprms"])
        return values

class Body(BaseModel):
    datetimeStr: str
    busstates: List[BusState]

class BusLocationResponse(BaseModel):
    head: Head
    body: Body
