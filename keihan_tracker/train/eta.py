"""公式の遅れと位置履歴から、停車駅の時刻を推定する。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .tracker import TrainData, ActiveTrainData, StationData


@dataclass(frozen=True, slots=True)
class EtaPolicy:
    """到着予測の係数と閾値。既定値は keihan_signage と同じ。"""
    discount_rate: float = 0.1
    discount_max_delay: int = 10
    windows: tuple[tuple[int, int], ...] = ((60, 10), (25, 60))
    stuck_minutes: int = 15
    growing_ratio: float = 0.7
    check_blocked_ahead: bool = True


class EtaHidden(Enum):
    """予測を伏せた理由。複数当てはまるときは estimate_arrival の順に判定する。"""
    GROWING = "growing"
    OVER_120 = "over_120"
    STUCK = "stuck"
    BLOCKED_AHEAD = "blocked_ahead"
    OUT_OF_WINDOW = "out_of_window"
    NO_TIMETABLE = "no_timetable"
    NOT_STOPPING = "not_stopping"
    NO_TIME = "no_time"
    PASSED = "passed"
    UNKNOWN_POSITION = "unknown_position"


@dataclass(frozen=True, slots=True)
class EtaResult:
    """推定時刻と、伏せた理由。time の元は公式の発車時刻（stationDepTime）。"""
    time: datetime | None
    reason: EtaHidden | None


def _barely_moving(train: ActiveTrainData, policy: EtaPolicy) -> bool:
    return (train.is_delay_growing(ratio=policy.growing_ratio)
            or (not train.is_at_start_station and train.staying_time > timedelta(minutes=policy.stuck_minutes)))


def _on_through_line(train: TrainData) -> bool:
    try:
        return isinstance(train, ActiveTrainData) and train.line in ("京阪本線・鴨東線", "中之島線")
    except ValueError:
        return False


def _blocked_ahead(train: TrainData, station: StationData, policy: EtaPolicy) -> bool:
    # signage と同じ座標で比べる。本線（京橋〜出町柳）以外の対象駅は判定しない
    number = station.station_number
    if not 4 <= number <= 42 or not isinstance(train, ActiveTrainData) or not _on_through_line(train):
        return False
    station_row = (42 - number) * 3 + 1
    row = train.location_row
    for other in train.master.active_trains.values():
        if not _on_through_line(other) or other.direction != train.direction:
            continue
        ahead = (station_row <= other.location_row < row) if train.direction == "up" else (row < other.location_row <= station_row)
        if ahead and _barely_moving(other, policy):
            return True
    return False


def estimate_arrival(
        train: TrainData, station: StationData, *, now: datetime, policy: EtaPolicy = EtaPolicy(),
        ) -> EtaResult:
    """
    停車駅の時刻を推定する。now はタイムゾーン付きで渡す（時計は参照しない）。
    ダイヤなし・停車しない駅・時刻不明・通過済み・現在位置不明は、例外ではなく time=None と理由を返す。
    次に、120分以上の遅れ・遅れの増加・長時間停滞・前詰まり・予測範囲外の順に伏せる。
    遅れの補正は signage と同じく0未満の残り時間を0として、補正後の遅れを round で分単位に丸める。
    前詰まりは本線・中之島線の走行列車と、本線の駅番号4〜42の対象駅で判定する。
    signage の一覧表示の件数制限・残り時間による行の除外・未走行の後続列車の表示制御は行わない。
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now はタイムゾーン付きの datetime にしてください")
    if not train.has_dia:
        return EtaResult(None, EtaHidden.NO_TIMETABLE)
    stop = train.get_stop(station)
    if stop is None:
        return EtaResult(None, EtaHidden.NOT_STOPPING)
    if stop.time is None:
        return EtaResult(None, EtaHidden.NO_TIME)
    if isinstance(train, ActiveTrainData):
        current = train.current_or_next_stop
        if current is None:
            return EtaResult(None, EtaHidden.UNKNOWN_POSITION)
        stops = [s.station for s in train.stop_stations]
        if stops.index(station) < stops.index(current):
            return EtaResult(None, EtaHidden.PASSED)
    elif not train._may_stop_after_observation_began(stop):
        return EtaResult(None, EtaHidden.PASSED)

    delay = train.delay_minutes
    if delay >= 120:
        return EtaResult(None, EtaHidden.OVER_120)
    if isinstance(train, ActiveTrainData):
        if train.is_delay_growing(ratio=policy.growing_ratio):
            return EtaResult(None, EtaHidden.GROWING)
        if not train.is_at_start_station and train.staying_time > timedelta(minutes=policy.stuck_minutes):
            return EtaResult(None, EtaHidden.STUCK)
    if policy.check_blocked_ahead and _blocked_ahead(train, station, policy):
        return EtaResult(None, EtaHidden.BLOCKED_AHEAD)

    remaining = (stop.time + timedelta(minutes=delay) - now).total_seconds() / 60
    if delay >= 1 and not any(remaining <= horizon and delay <= limit for horizon, limit in policy.windows):
        return EtaResult(None, EtaHidden.OUT_OF_WINDOW)
    corrected = delay
    if delay <= policy.discount_max_delay:
        corrected -= min(delay, policy.discount_rate * max(0.0, remaining))
    return EtaResult(stop.time + timedelta(minutes=round(corrected)), None)
