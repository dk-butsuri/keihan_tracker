"""
接近情報（BusLocationResponse）を、使いやすい形（BusArrival）に直す。通信はしない。
公式の生の値は BusArrival.raw にそのまま残す。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import datetime
import re

from .schemas import BusLocationResponse, BusState
from ..train.state import JST


class BusStatus(str, Enum):
    """
    便の状態。公式の見出しの括弧内（「定時運行中」「約1分遅れ」「到着済」、未受信の便は括弧なし）から決める。
    見たことのない表記は UNKNOWN にする（読めない遅れの表記は parse_busstate が警告を出す）。
    """
    TRACKED = "tracked"          # 位置を受信している（定時運行中・約N分遅れ）
    ARRIVED = "arrived"          # 到着済
    NOT_TRACKED = "not_tracked"  # 位置を受信していない（定刻だけ分かる）
    UNKNOWN = "unknown"          # 読めない表記


@dataclass(frozen=True, slots=True)
class BusArrival:
    """
    バス停に来る1便。時刻は JST の datetime（接近情報の作成時刻を基準に日付を付ける）。
    - route: 系統番号（"[22]" の括弧を外した "22"）
    - destination: 行先（例: "京阪香里園行"）
    - via: 経由
    - scheduled: 定刻。分からなければ None
    - expected: 到着予定。公式の「HH:MM 到着予定」の時刻（定刻＋遅れ）。位置を受信していない便は None
    - delay_minutes: 遅れ（分）。定時運行中なら0。未受信・到着済・読めない表記なら None
    - status: 便の状態（BusStatus）
    - order: 接近順位
    - lat, lon: バスの位置（未受信の便では意味のない値のことがある）
    - dgmpl: fetch_diagram に渡す値。HTML に便のキーがなければ None
    - raw: 元の BusState
    """
    route: str
    destination: str
    via: str
    scheduled: Optional[datetime.datetime]
    expected: Optional[datetime.datetime]
    delay_minutes: Optional[int]
    status: BusStatus
    order: int
    lat: float
    lon: float
    dgmpl: Optional[str]
    raw: BusState


def _created_at(response: BusLocationResponse) -> Optional[datetime.datetime]:
    """「2026/11/07 17:54現在」を datetime にする。読めなければ None"""
    m = re.search(r"([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})\s+([0-9]{1,2}):([0-9]{2})", response.body.datetimeStr)
    if m is None:
        return None
    return datetime.datetime(*map(int, m.groups()), tzinfo=JST)


def _at(base: Optional[datetime.datetime], hhmm: Optional[str]) -> Optional[datetime.datetime]:
    """HH:MM に、base に最も近い日付を付ける（23:50 の接近情報に 00:10 の便があれば翌日）"""
    if base is None or hhmm is None:
        return None
    hour, minute = map(int, hhmm.split(":"))
    t = base.replace(hour=0, minute=0) + datetime.timedelta(hours=hour, minutes=minute)
    if t < base - datetime.timedelta(hours=12):
        t += datetime.timedelta(days=1)
    elif t > base + datetime.timedelta(hours=12):
        t -= datetime.timedelta(days=1)
    return t


def _status(bus: BusState) -> BusStatus:
    if bus.delay_text is None:
        return BusStatus.NOT_TRACKED
    if bus.delay_text == "到着済":
        return BusStatus.ARRIVED
    if bus.delay_minutes is not None:
        return BusStatus.TRACKED
    return BusStatus.UNKNOWN


def to_arrivals(response: BusLocationResponse) -> list[BusArrival]:
    """接近情報を BusArrival のリスト（接近順）にする。通信はしない。"""
    base = _created_at(response)
    arrivals = []
    for bus in response.body.busstates:
        prms = bus.busstateprms
        status = _status(bus)
        m = re.fullmatch(r"([0-9]{1,2}:[0-5][0-9])\s*到着予定", prms.timetable.strip())
        arrivals.append(BusArrival(
            route=prms.route.strip().strip("[]"),
            destination=prms.destination,
            via=prms.via,
            scheduled=_at(base, bus.scheduled_time),
            expected=_at(base, m[1]) if m and status != BusStatus.NOT_TRACKED else None,
            delay_minutes=bus.delay_minutes,
            status=status,
            order=prms.order,
            lat=prms.lat,
            lon=prms.lon,
            dgmpl=bus.dgmpl,
            raw=bus,
        ))
    return sorted(arrivals, key=lambda a: a.order)
