# 遅延情報はデータ形式が不明のため未実装

if __name__ == "__main__":
    print()
    print("### keihan_tracker - 京阪電車・バスの接近情報解析ライブラリ ###\n")
    print("This is not an executable script. Please refer to README.md.")
    print("このスクリプトは直接実行できません。README.mdを参照してください。")
    exit()

from .schemes import (TransferGuideInfo,
                      FileList,
                      SelectStation,
                      startTimeList,
                      trainPositionList,
                      StationConnections,
                      MultiLang,
                      TrainType,
                      LineLiteral,
                      )
from . import stations_map
from .position_calculation import calc_position
# KeihanAPIError・write_snapshot・USER_AGENT・JST などは、これまでどおり tracker からも import できる
from .parse import (KeihanAPIError, _parse, _parse_delay_minutes,
                    parse_select_station, parse_transfer_guide, parse_timetable, parse_positions,
                    SELECT_STATION_URL, TRANSFER_GUIDE_URL, START_TIME_LIST_URL, TRAIN_POSITION_URL)
from .client import KHClient, USER_AGENT, write_snapshot
from .state import KHState, JST, DATE_CHANGE_TIME, _deprecated
from typing import Optional, Literal, Sequence
from httpx import AsyncClient
from pathlib import Path
import dataclasses
import functools
import xml.etree.ElementTree as ET
from tabulate import tabulate
import datetime

# 日付が変わったあと、ダイヤが今日のものだと確かめられなくても、取得のたびの取り直しをやめる時刻（安全装置）
DIA_RETRY_DEADLINE = datetime.time(5, 30)
# fetch_pos の間隔の下限（秒）。公式の位置情報は1分ごとにしか更新されないため、これより短くしても新しい情報は増えない
MIN_RATE_LIMIT = 15


class DiaNotFoundError(ValueError):
    """
    列車のダイヤ（startTimeList）が無いため、ダイヤから求める情報が分からないことを表す例外。
    運休・運転整理などで、位置情報にはあるがダイヤには無い列車が走ることがある。
    """


def _line_order(line: LineLiteral, direction: Literal["up","down"]) -> list[int]:
    """路線の駅番号を、その方向に走る順に返す"""
    match line:
        case "中之島線":
            order = stations_map.NAKANOSHIMA_UP
        case "交野線":
            order = stations_map.KATANO_UP
        case "京阪本線・鴨東線":
            order = stations_map.HONNSEN_UP
        case "宇治線":
            order = stations_map.UJI_UP
        case _:
            raise ValueError()
    return order if direction == "up" else list(reversed(order))


@dataclasses.dataclass(frozen=True, slots=True)
class TrainPosition:
    """
    座標から分かる、線路上の位置。停車するかどうかはダイヤによるため、ここには含まない。
    - station: 駅の位置座標にいるならその駅（停車中・通過中）。駅間なら None
    - behind: 駅間なら後ろの駅。駅にいるなら None
    - ahead: 進行方向の次の駅。路線の端の駅にいるなら None
    - line: 走っている路線
    """
    station: Optional["StationData"]
    behind:  Optional["StationData"]
    ahead:   Optional["StationData"]
    line:    LineLiteral


def _unwrap_master(master):
    """master に KHTracker が渡されたら、その状態（KHState）を使う（v2 では master は KHTracker だったため）"""
    return master.state if isinstance(master, KHTracker) else master


def _init_aliases(**aliases: str):
    """
    dataclass の __init__ に、別名のキーワードを足す（pydantic の alias の代わり）。
    別名で渡された値は、本来の名前の引数として渡す。@dataclass の外側に付ける。
    InitVar では、別名と同じ名前の property（wdfBlockNo・train_type など）とぶつかるため、この形にしている。
    """
    def decorate(cls):
        init = cls.__init__

        @functools.wraps(init)
        def __init__(self, *args, **kwargs):
            for alias, name in aliases.items():
                if alias in kwargs:
                    if name in kwargs:
                        raise TypeError(f"{alias} と {name} は同時に渡せません")
                    kwargs[name] = kwargs.pop(alias)
            init(self, *args, **kwargs)

        cls.__init__ = __init__
        return cls
    return decorate


def _calc_train_position(master: "KHState", col: int, row: int, direction: Literal["up","down"]) -> TrainPosition:
    line, st1, st2 = calc_position(col, row)
    if not st2:
        order = _line_order(line, direction)
        index = order.index(st1) if st1 in order else None
        ahead = order[index + 1] if index is not None and index + 1 < len(order) else None
        return TrainPosition(
            station = master.stations[st1],
            behind = None,
            ahead = master.stations.get(ahead) if ahead is not None else None,
            line = line,
        )
    # 京阪本線のみ、番号が大きい方が上り。そのほかでは小さい方が上り
    small, large = sorted((st1, st2))
    if (direction == "up") == (line == "京阪本線・鴨東線"):
        behind, ahead = small, large
    else:
        behind, ahead = large, small
    return TrainPosition(
        station = None,
        behind = master.stations[behind],
        ahead = master.stations[ahead],
        line = line,
    )

# eq=False: 駅は下の __eq__ / __hash__（駅番号で比べる）を使う
@dataclasses.dataclass(slots=True, kw_only=True, eq=False)
class StationData:
    """
    駅情報を表すクラス。
    """
    master:        "KHState" #親インスタンス
    line:           set[LineLiteral]
    station_number: int
    station_name:   MultiLang
    transfer:       StationConnections = dataclasses.field(default_factory=StationConnections)

    def __post_init__(self) -> None:
        # master に KHTracker が渡されたら、その状態を使う。渡された set は複製する
        self.master = _unwrap_master(self.master)
        self.line = set(self.line)

    @property
    def arriving_trains(self):
        """
        この駅に 次に停車する予定 or 停車中 の列車リストを返す。
        KHTracker.trains から該当駅が current_or_next_stop になっている列車を抽出する。
        """
        return [train for train in self.master.active_trains.values() if train.current_or_next_stop == self]

    @property
    def trains(self) -> list[tuple["TrainData","StopStationData"]]:
        """
        この駅に 今後停車する or 停車中 or 停車した すべての列車返す。[((12,00),TrainData), ...]
        stop_stationsに自身（self）が含まれる列車を、KHState が持つ駅→停車駅の索引から取る。
        """
        # 駅→停車駅の索引から取る（全列車をなめない）。並びは self.trains の順なので、時刻が同じ列車の順番は変わらない
        trains:list[tuple[TrainData,StopStationData]] = [
            (stop.train, stop) for stop in self.master._stops_at(self)
        ]

        # 時刻不明の停車は後ろに回す
        trains.sort(key = lambda t:t[1].time or datetime.datetime.max.replace(tzinfo=JST))
        return trains
    
    @property
    def upcoming_trains(self) -> list[tuple["TrainData|ActiveTrainData","StopStationData"]]:
        """
        この駅に 今後停車する or 停車中 のすべての列車を返す。
        走行中の列車は、停車駅の並び（走行順）で current_or_next_stop 以降にこの駅があるかで判定する。
        """
        trains:list[tuple[(TrainData|ActiveTrainData), StopStationData]] = []

        # 全ての列車から
        for train, stop in self.trains:
            if isinstance(train, ActiveTrainData):
                # current_or_next_stopが不明ならスキップ
                current_or_next_stop = train.current_or_next_stop
                if not current_or_next_stop:
                    continue

                # この駅が、次に停車する駅と同じか、それより後に停車する駅なら
                # （時刻不明の停車駅でも判定できるよう、時刻ではなく並び順で比較する）
                stop_stations = [s.station for s in train.stop_stations]
                if stop_stations.index(self) >= stop_stations.index(current_or_next_stop):
                    trains.append((train,stop))
            elif train._may_stop_after_observation_began(stop):
                trains.append((train,stop))

        # 時刻不明の停車は後ろに回す
        trains.sort(key=lambda x:x[1].time or datetime.datetime.max.replace(tzinfo=JST))
        return trains

    @property
    def fastest_arrivals(self) -> dict["StationData", tuple["TrainData|ActiveTrainData", datetime.datetime]]:
        """
        この駅から乗れる列車（upcoming_trains）で、この先の各駅に最も早く着く列車とその到着時刻。{駅: (列車, 到着時刻)}
        「〇〇には先着」の表示に使う。
        - 乗り換えは考えない（その列車に乗ったまま着く時刻で比べる）
        - 到着時刻はダイヤの予定時刻で、遅延を足さない（中身は公式の発車時刻。StopStationData.time を参照）
        - 時刻不明の停車駅は使わない。到着時刻が同じなら、この駅を先に出る列車にする
        - この駅自身は含まない
        """
        fastest: dict[StationData, tuple[TrainData|ActiveTrainData, datetime.datetime]] = {}
        # upcoming_trains はこの駅の時刻順なので、到着時刻が同じなら先に出る列車が残る
        for train, stop in self.upcoming_trains:
            stops = train.stop_stations
            index = next(i for i, s in enumerate(stops) if s is stop)
            for later in stops[index + 1:]:
                if later.time is None or later.station == self:
                    continue
                best = fastest.get(later.station)
                if best is None or later.time < best[1]:
                    fastest[later.station] = (train, later.time)
        return fastest

    def __str__(self):
        return self.station_name.ja

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StationData):
            return NotImplemented
        return self.station_number == other.station_number

    def __hash__(self) -> int:
        return hash(self.station_number)


@dataclasses.dataclass(frozen=True, slots=True)
class TrainHistoryRecord:
    """
    走行中の列車の、ある位置情報の時点での値。TrainData.history の1件。
    fetch_pos で更新した後の値なので、最新の1件は列車の今の値と一致する。
    1日分を残すため、軽い dataclass にしている（BaseModel の8分の1ほど）。
    """
    master: "KHState"
    file_created_time:  datetime.datetime           # その位置情報の fileCreatedTime（取得した時刻ではない）
    location_col:       int
    location_row:       int
    delay_minutes:      int
    direction:          Literal["up","down"]
    destination:        Optional[StationData]       # 行先駅（行先なし(00)の間は直前の値）

    @property
    def line(self) -> LineLiteral:
        """その時点で走っていた路線"""
        line, _, _ = calc_position(self.location_col, self.location_row)
        return line

    @property
    def position(self) -> TrainPosition:
        """その時点の線路上の位置"""
        return _calc_train_position(self.master, self.location_col, self.location_row, self.direction)

    @property
    def stopping_station(self) -> Optional[StationData]:
        """【非推奨】position.station を使ってください。"""
        _deprecated("TrainHistoryRecord.stopping_station", "position.station を使ってください。")
        return self.position.station

    @property
    def is_at_station(self) -> bool:
        """【非推奨】その時点で駅の位置座標にいたか。position.station is not None を使ってください。"""
        _deprecated("TrainHistoryRecord.is_at_station", "position.station is not None を使ってください。")
        return self.position.station is not None


# eq=False: 同じ停車駅の中身が偶然同じでも、別の停車駅として扱う（値で比べる箇所は無い）
@dataclasses.dataclass(slots=True, kw_only=True, eq=False)
class StopStationData:
    """
    列車の停車駅情報を表すクラス。
    - is_start: 始発駅か
    - is_stop: 停車駅か
    - station: 駅データ
    - time: 公式のダイヤの stationDepTime（発車時刻）。始発駅は公式のデータが "-" のため None（datetime）
    - train: この停車駅を持つ列車（逆参照）。いつも KHState.trains に今入っている列車を指す
    """

    is_start:   bool = False   # 始発駅かどうか
    is_final:   bool = False   # 終着駅かどうか
    is_stop:    bool = True    # 停車するかどうか
    station:    StationData
    time:       Optional[datetime.datetime] = None # 発車時刻（stationDepTime）
    # 列車が置き換わる（走り出す・運行終了）と付け替える。repr に含めると、列車の repr と循環する
    train:      Optional["TrainData"] = dataclasses.field(default=None, repr=False)

# eq=False: 列車は同一性で比べる（値で比べる箇所は無い。master を含む全フィールドの比較も避ける）
# wdfBlockNo は v2.8 までのキーワード名
@_init_aliases(wdfBlockNo="wdf")
@dataclasses.dataclass(slots=True, kw_only=True, eq=False)
class TrainData:
    """
    列車情報を表すクラス。
    - 列車番号、種別、編成、行先、停車駅リストなどを保持
    - stop_stations, start_station, get_stop_time で駅・時刻情報取得
    """
    master:     "KHState"
    # WDF番号（その日の列車を区別する番号）。v2.8 までの wdfBlockNo= でも作れる
    wdf:int
    date:datetime.date
    has_premiumcar: Optional[bool]
    train_formation: Optional[int]
    route_stations: list[StopStationData] = dataclasses.field(default_factory=list)      # 経路にある駅リスト
    delay_minutes: int = 0
    # その日、走っている間の位置情報ごとの値（古い順）。走り終えた後も残り、また走り出すと続きから加える
    history: list[TrainHistoryRecord] = dataclasses.field(default_factory=list)
    # 以下はActiveだった時のデータを保持する変数
    actual_train_type: Optional[TrainType] = None
    actual_direction: Optional[Literal["up","down"]] = None
    actual_destination: Optional[StationData] = None
    actual_line: Optional[LineLiteral] = None

    def __post_init__(self) -> None:
        # master に KHTracker が渡されたら、その状態を使う。渡されたリストは複製する（中身は同じ）
        self.master = _unwrap_master(self.master)
        self.route_stations = list(self.route_stations)
        self.history = list(self.history)

    @property
    def has_dia(self) -> bool:
        """ダイヤ（停車駅・時刻）があるか。運休・運転整理などで、ダイヤに無い列車が走ることがある。"""
        return len(self.route_stations) > 0

    @property
    def wdfBlockNo(self) -> int:
        """【非推奨】wdf を使ってください。"""
        _deprecated("wdfBlockNo", "wdf を使ってください。")
        return self.wdf

    @property
    def line(self) -> LineLiteral:
        # アクティブ時のデータが残っているならそのまま返す
        if self.actual_line is not None:
            return self.actual_line

        stop_stations = [stop.station for stop in self.stop_stations]
        if self.master.stations[54] in stop_stations:
            return "中之島線"
        elif self.master.stations[67] in stop_stations:
            return "交野線"
        elif self.master.stations[77] in stop_stations:
            return "宇治線"
        else:
            return "京阪本線・鴨東線"
    
    @property
    def direction(self) -> Literal["up","down"] :
        """始発・終着駅から方向を推定する"""
        # アクティブ時のデータが残っているならそのまま返す
        if self.actual_direction is not None:
            return self.actual_direction

        # ダイヤがなく、行先もない列車は存在しない
        # 一度でもpositionに登場したならactual_directionには値が入る。ダイヤに登場したならdestinationにも値が入る
        if self.destination is None:
            raise DiaNotFoundError(f"[WDF{self.wdf}] 行先が分からないため、方向を推定できません。")

        if self.line == "交野線":
            # 私市行きなら下り
            if self.destination == self.master.stations[67]:
                return "down"
            else:
                return "up"
        elif self.line == "宇治線":
            # 宇治行きなら下り
            if self.destination == self.master.stations[77]:
                return "down"
            else:
                return "up"
        elif self.line == "中之島線":
            # 中ノ島行きなら下り
            if self.destination == self.master.stations[54]:
                return "down"
            else:
                return "up"
        else:
            # 目的地の方が番号が若いなら（大阪方面行）
            if self.destination.station_number < self.start_station.station_number:
                return "down"
            else:
                return "up"

    @property
    def train_type(self) -> TrainType:
        """
        停車駅リストに基づいて列車種別を推定する
        【注：ライナーは停車駅から区別できないため、特急と判定される。樟葉始発のライナーは通勤快急と判定される。】
        """
        # アクティブ時のデータが残っているならそのまま返す
        if self.actual_train_type is not None:
            return self.actual_train_type
        if not self.has_dia or self.destination is None:
            raise DiaNotFoundError(f"[WDF{self.wdf}] ダイヤに無い列車のため、種別を推定できません。")

        stop_stations_list = [stop.station for stop in self.stop_stations]

        try:
            st_kyobashi = self.master.stations[4]      # KH04 京橋
            st_noe = self.master.stations[5]           # KH05 野江
            st_moriguchishi = self.master.stations[11] # KH11 守口市
            st_kadomashi = self.master.stations[13]    # KH13 門真市
            st_kayashima = self.master.stations[16]    # KH16 萱島
            st_neyagawashi = self.master.stations[17]  # KH17 寝屋川市
            st_korien = self.master.stations[18]       # KH18 香里園
            st_hirakatakoen = self.master.stations[20] # KH20 枚方公園
            st_hirakatashi = self.master.stations[21]  # KH21 枚方市
            
            # 京都側の判定用
            st_fushimiinari = self.master.stations[34] # KH34 伏見稲荷
            st_tobakaido = self.master.stations[35]    # KH35 鳥羽街道
            st_jingu_marutamachi = self.master.stations[41] # KH41 神宮丸太町

        except KeyError:
            return TrainType.LOCAL

        # 対象外路線
        if self.line not in ["京阪本線・鴨東線", "中之島線"]:
            return TrainType.LOCAL

        # 1. 【普通】 
        # 大阪側: 野江(KH05)に停車するなら普通
        if st_noe in stop_stations_list:
            return TrainType.LOCAL
        
        # --- 京橋(KH04)を通らない列車の判定 ---
        if st_kyobashi not in stop_stations_list:
            # 鳥羽街道(KH35)に停車するなら確実に普通
            if st_tobakaido in stop_stations_list:
                return TrainType.LOCAL
            
            # 鳥羽街道に停車しない場合
            # 「鳥羽街道(KH35)をまたぐ運行かどうか」で判定を分岐
            
            # 始発駅と終着駅の駅番号を取得
            start_num = self.start_station.station_number
            last_num = self.destination.station_number
            min_num, max_num = sorted((start_num, last_num))
            
            # 運行区間に鳥羽街道(35)が含まれているか？
            # (区間がまたいでいる = min < 35 < max)
            is_cross_tobakaido = min_num < 35 < max_num
            
            if is_cross_tobakaido:
                # 鳥羽街道を通過する列車（例：淀行き急行）
                if st_fushimiinari in stop_stations_list:
                    return TrainType.EXPRESS

                # 寝屋川市・香里園に停車するなら快速急行（例：寝屋川市発出町柳行き）
                if st_neyagawashi in stop_stations_list or st_korien in stop_stations_list:
                    return TrainType.RAPID_EXP

                # return TrainType.LINER
                return TrainType.LTD_EXP
            
            else:
                # 鳥羽街道をまたがない列車
                # 始発から終着までの駅にすべて停車するなら普通（例：香里園～淀の臨時普通）
                if all(self.master.stations[n] in stop_stations_list for n in range(min_num, max_num + 1)):
                    return TrainType.LOCAL

                # 鳥羽街道まで行かない京都側の短距離列車（例：出町柳～三条）
                # 神宮丸太町(KH41)に停車するなら普通
                if st_jingu_marutamachi in stop_stations_list:
                    return TrainType.LOCAL
                
                # 万が一、神宮丸太町を通過する短距離列車があれば特急扱い
                return TrainType.LTD_EXP


        # --- 以下、京橋(KH04)を通る列車の判定 (既存ロジック) ---
        if st_kadomashi in stop_stations_list:
             return TrainType.SEMI_EXP

        if st_kayashima in stop_stations_list:
            if st_moriguchishi in stop_stations_list:
                return TrainType.SUB_EXP          # 準急
            else:
                return TrainType.COMMUTER_SUB_EXP # 通勤準急

        if st_moriguchishi in stop_stations_list:
            if st_hirakatakoen in stop_stations_list:
                return TrainType.EXPRESS     # 急行
            else:
                return TrainType.RAPID_EXP   # 快速急行
        else:
            if st_hirakatashi not in stop_stations_list:
                return TrainType.RAPID_LTD_EXP # 快速特急 洛楽

            if st_neyagawashi in stop_stations_list:
                return TrainType.COMMUTER_RAPID_EXP # 通勤快急
            else:
                # return TrainType.LINER
                return TrainType.LTD_EXP # 特急

    # 始発駅（is_startがTrueの停車駅のうち1番目を返す）
    @property
    def start_station(self) -> StationData:
        if not self.has_dia:
            raise DiaNotFoundError(f"[WDF{self.wdf}] ダイヤに無い列車のため、始発駅が分かりません。")
        start_stations = [station for station in self.route_stations if station.is_start]
        if len(start_stations) != 1:
            raise ValueError(f"[WDF{self.wdf}] 始発駅が{len(start_stations)}個登録されています。これはバグです。\n({start_stations})")
        station = start_stations[0].station
        return station

    @property
    def destination(self) -> StationData|None:
        # アクティブ時のデータが残っているならそのまま返す
        if self.actual_destination is not None:
            return self.actual_destination
        if not self.has_dia:
            raise DiaNotFoundError(f"[WDF{self.wdf}] ダイヤに無い列車のため、行先が分かりません。")
        stop_stationdata = [station for station in self.route_stations if station.is_final]
        if len(stop_stationdata) != 1:
            raise ValueError(f"[WDF{self.wdf}] 終着駅が{len(stop_stationdata)}個登録されています。これはバグです。\n({stop_stationdata})")
        station = stop_stationdata[0].station
        return station
    
    @property
    def was_active(self) -> bool:
        """
        その日、走っているのを観測したことがあるか。走行中の列車も True。
        運行を終えたことは保証しない（止まったまま位置情報から消えることがある）。
        """
        return bool(self.history)

    def _may_stop_after_observation_began(self, stop: StopStationData) -> bool:
        """
        走っていない列車が、これから stop に停車するかもしれないか（upcoming_trains 用）。
        走っているのを観測したことがあれば、もう停車しない。
        その営業日に最初に位置情報を取れた時刻より前に停車する予定なら、観測を始める前に停車したとみなす。
        stop の時刻が不明なら、その後の時刻が分かっている停車駅で判定する。どれも不明なら停車するかもしれないとする。
        """
        if self.was_active:
            return False
        began = self.master._first_observed_time
        if began is None:
            return True
        stops = self.stop_stations
        later = stops[stops.index(stop):]
        time = next((s.time for s in later if s.time), None)
        return time is None or time > began

    # 停車駅リスト
    @property
    def stop_stations(self) -> list[StopStationData]:
        """停車する駅のリストを走行順に返します。"""
        # ダイヤの並び順は走行順。時刻不明の停車駅もあるため、時刻でソートしない
        return [station for station in self.route_stations if station.is_stop == True]
    
    def get_stop(self, station:StationData) -> Optional[StopStationData]:
        """駅に停車する停車駅（StopStationData）を返します。停車しない駅（通過駅・経路にない駅）は None。"""
        for stop in self.stop_stations:
            if stop.station == station:
                return stop
        return None

    def get_stop_time(self, station:StationData) -> Optional[datetime.datetime]:
        """駅に停車する時刻を返します。"""
        stop = self.get_stop(station)
        return stop.time if stop is not None else None

    # 整形して文字列化

    def __str__(self) -> str:
        start = self.start_station if self.has_dia else "不明"
        text = f'【非アクティブ】{self.train_type.value} {self.destination or "不明"} 行き（{start} 発）\n'
        text += f'{self.train_formation}編成 {"プレミアムカー付き /" if self.has_premiumcar else ""}\n'

        header = ["到着時刻","停車駅","ホーム番線"]
        body:list[list[str]] = []
        for stop in self.stop_stations:
            if stop.time != None:
                body.append([f"{stop.time.hour:02}:{stop.time.minute:02}", f"{stop.station}"])
            elif stop.is_start:
                body.append([f"出発駅",f"{stop.station}"])
            else:
                body.append([f"不明",f"{stop.station}"])
        return text + tabulate(body, header)


# train_type・direction・destination は、位置情報の値を入れる active_* のキーワード名
@_init_aliases(wdfBlockNo="wdf", train_type="active_train_type",
               direction="active_direction", destination="active_destination")
@dataclasses.dataclass(slots=True, kw_only=True, eq=False)
class ActiveTrainData(TrainData):
    """
    現在アクティブな列車情報を表すクラス。
    - 列車番号、種別、編成、行先、停車駅リストなどを保持
    - stop_stations, start_station, get_stop_time で駅・時刻情報取得
    """
    train_formation:Optional[int] = None       # 編成番号
    train_number:   str             # 列車番号（1051号など）
    active_train_type:  TrainType
    active_direction:   Literal["up","down"]      # 方向（up:京都方面、down:大阪方面）
    active_destination: Optional[StationData]     # 行先駅（行先なし(00)ならNone）
    is_special:     bool            # 臨時か
    position_since: Optional[datetime.datetime] = None # 今の座標に来た時刻（位置情報のfileCreatedTime）。出現時は最初に観測した時刻
    has_premiumcar: Optional[bool] = None
    cars :          int             # 車両数
    delay_text:     MultiLang       # 遅延時間（テキスト）

    # サイト上で列車位置を表示するときのグリッド座標
    location_col: int
    location_row: int

    def _record_history(self, file_created_time: datetime.datetime) -> None:
        """今の値を history に加える。同じ位置情報を取り直したときは加えない"""
        if self.history and self.history[-1].file_created_time == file_created_time:
            return
        self.history.append(TrainHistoryRecord(
            master=self.master, 
            file_created_time = file_created_time,
            location_col = self.location_col,
            location_row = self.location_row,
            delay_minutes = self.delay_minutes,
            direction = self.active_direction,
            destination = self.active_destination,
        ))

    @property
    def train_type(self):
        return self.active_train_type

    @property
    def destination(self) -> Optional[StationData]:
        """行先駅。位置情報で行先なし(00)のときはダイヤの終着駅、ダイヤも無ければNone"""
        if self.active_destination is not None:
            return self.active_destination
        if self.has_dia:
            # slots=True で作り直されたクラスでも動くよう、引数付きの super を使う
            return super(ActiveTrainData, self).destination
        return None

    @property
    def direction(self) -> Literal["up","down"]:
        return self.active_direction

    @property
    def position(self) -> TrainPosition:
        """座標から分かる線路上の位置（駅・後ろの駅・進行方向の次の駅・路線）"""
        return _calc_train_position(self.master, self.location_col, self.location_row, self.direction)

    def _stops_from(self, station: Optional[StationData]) -> Optional[StationData]:
        """station から進行方向へたどり、最初に停車する駅を返す（station 自身を含む）"""
        if station is None:
            return None
        stop_stations = [stop.station.station_number for stop in self.stop_stations]
        line = _line_order(self.line, self.direction)
        if station.station_number not in line:
            raise IndexError(f"{station} is not in {self.line}")
        for number in line[line.index(station.station_number):]:
            if number in stop_stations:
                return self.master.stations[number]
        # 不明な場合
        return None

    @property
    def current_or_next_stop(self) -> Optional[StationData]:
        """停車中ならその駅、でなければ次に停車する駅（発車標用）。分からなければ None"""
        position = self.position
        return self._stops_from(position.station or position.ahead)

    @property
    def stopped_at(self) -> Optional[StationData]:
        """停車中の駅。停車中でなければ（通過中・駅間なら） None"""
        station = self.position.station
        if station is not None and self.current_or_next_stop == station:
            return station
        return None

    @property
    def next_stop(self) -> Optional[StationData]:
        """この先で次に停車する駅。停車中の駅は含まない。終点にいるときなど、分からなければ None"""
        position = self.position
        if self.stopped_at is not None:
            return self._stops_from(position.ahead)
        return self._stops_from(position.station or position.ahead)

    @property
    def is_stopping(self) -> Optional[bool]:
        """【非推奨】列車が駅に停車中かどうか。stopped_at is not None を使ってください。"""
        _deprecated("is_stopping", "stopped_at is not None を使ってください。")
        return self._is_stopping()

    def _is_stopping(self) -> Optional[bool]:
        # v2 の is_stopping と同じく、停車駅が分からなければ None
        if self.current_or_next_stop is None:
            return None
        return self.stopped_at is not None

    @property
    def is_at_station(self) -> bool:
        """【非推奨】列車が駅の位置座標にあるか。position.station is not None を使ってください。"""
        _deprecated("is_at_station", "position.station is not None を使ってください。")
        return self.position.station is not None

    @property
    def is_at_start_station(self) -> Optional[bool]:
        """始発駅に停車中かどうか"""
        is_stopping = self._is_stopping()
        if is_stopping is None:
            return None

        if is_stopping is False:
            return False

        return self.stopped_at == self.start_station

    @property
    def estimated_delay_minutes(self) -> int:
        """
        0から120分ならdelay_minutesの値を返し、
        120分を超えた場合は遅延分数の最小値を推定します。
        """
        # 120分以内なら公式の値を用いる
        official_delay_minutes = self.delay_minutes
        current_or_next_stop = self.current_or_next_stop
        if official_delay_minutes < 120 or current_or_next_stop is None:
            return official_delay_minutes

        min_delay:list[int] = []
        min_delay.append(official_delay_minutes)

        stopped_at = self.stopped_at
        # 停車中なら：その駅の実際の到着時刻と定刻の到着時刻の差を算出（最低遅延分数）
        if stopped_at is not None:
            scheduled_time = self.get_stop_time(stopped_at)

            # その駅にいつから止まっているか
            actual_arrived_time:Optional[datetime.datetime] = None
            first_obs_index = None
            for record in self.history:
                if record.location_col == self.location_col and record.location_row == self.location_row:
                    first_obs_index = self.history.index(record)
                    break
                continue

            # 0番目でないなら前の要素もある
            if first_obs_index is not None and first_obs_index > 0:
                actual_arrived_time = self.history[first_obs_index-1].file_created_time

            if actual_arrived_time and scheduled_time:
                min_delay.append(int((actual_arrived_time - scheduled_time).total_seconds() // 60))

        # 次の駅の定刻停車時刻と現在の時刻の差（最低遅延分数）
        if stopped_at is None:
            next_stop_scheduled_time = [i for i in self.route_stations if i.station == current_or_next_stop][0].time

        else:
            current_stopstationdata = [i for i in self.stop_stations if i.station == stopped_at][0]
            next_stop_scheduled_time = None
            if len(self.stop_stations)-1 >= self.stop_stations.index(current_stopstationdata)+1:
                next_stop_scheduled_time = self.stop_stations[self.stop_stations.index(current_stopstationdata)+1].time
        if next_stop_scheduled_time and self.master.position_created_at:
            min_delay.append(int((self.master.position_created_at - next_stop_scheduled_time).total_seconds() // 60))

        return max(min_delay)

    @property
    def staying_time(self) -> datetime.timedelta:
        """
        今の座標に留まっている時間。駅でも駅間でも、座標が変わらなければ増える。
        - 最後に取得した位置情報の時点の値で、位置情報の作成時刻（fileCreatedTime）で測る。取得の間や、APIが止まっている間は増えない
        - 座標は1駅あたり3マスと粗いため、走行中でも数分は増えることがある
        - 出現した直後は、それより前に同じ座標にいた時間を含まない
        """
        position_list = self.master.train_position_list
        if self.position_since is None or position_list is None:
            return datetime.timedelta(0)
        return position_list.fileCreatedTime - self.position_since

    def delay_growth_rate(self, window: datetime.timedelta = datetime.timedelta(minutes=5)) -> Optional[float]:
        """
        遅延の増える速さ（分/分）。最新の位置情報と、それより window 以上前で最も新しい位置情報の時点を比べ、
        遅延の増えを経過時間で割る。1.0 なら止まっているのと同じ速さで遅延が増えている。
        - 比べられる記録が無い（出現してから window が経っていない）ときは None
        - 遅延が「2時間以上」になると delay_minutes は120で止まるため、止まったままでも0になる
        """
        if window <= datetime.timedelta(0):
            raise ValueError(f"window は正の時間にしてください: {window}")
        if not self.history:
            return None
        now = self.history[-1]
        past = next(
            (r for r in reversed(self.history) if now.file_created_time - r.file_created_time >= window),
            None,
        )
        if past is None:
            return None
        elapsed = (now.file_created_time - past.file_created_time).total_seconds() / 60
        return (now.delay_minutes - past.delay_minutes) / elapsed

    def is_delay_growing(self, window: datetime.timedelta = datetime.timedelta(minutes=5), ratio: float = 0.9) -> bool:
        """
        遅延が経過時間とほぼ同じ速さで増えているか（delay_growth_rate(window) が ratio 以上か）。
        止まっている列車は待たされた時間がそのまま遅延になるため、当てはまりやすい。
        """
        rate = self.delay_growth_rate(window)
        return rate is not None and rate >= ratio

    @property
    def line(self) -> LineLiteral:
        line, _, _ = calc_position(self.location_col, self.location_row)
        return line

    @property
    def next_station(self) -> StationData:
        """【非推奨】停車中の駅、もしくは次に停車（通過）する駅。駅にいるなら position.station、駅間なら position.ahead を使ってください。"""
        _deprecated("next_station", "駅にいるなら position.station、駅間なら position.ahead を使ってください。")
        position = self.position
        assert position.station or position.ahead
        return position.station or position.ahead

    @property
    def next_stop_station(self) -> Optional[StationData]:
        """【非推奨】停車中の駅、もしくは次に停車する駅。current_or_next_stop を使ってください。"""
        _deprecated("next_stop_station", "同じ値の current_or_next_stop を使ってください。")
        return self.current_or_next_stop

    # 整形して文字列化
    def __str__(self) -> str:
        text = f'【{"上り線" if self.direction == "up" else "下り線"}】 {"臨時" if self.is_special else ""}{self.train_type.value} {self.train_number}号: {self.destination or "不明"}行き {self.train_formation}編成/{self.cars}両 '
        text += f'(col, row: {self.location_col}, {self.location_row})\n'\
        f'{"プレミアムカー付き /" if self.has_premiumcar else ""}遅延：{self.delay_minutes} 分\n'
        text += f'次の停車駅は【{self.current_or_next_stop or "不明"}駅】\n\n' if not self.stopped_at else f"【{self.stopped_at}駅】に停車中\n\n"

        header = ["到着時刻","停車駅","ホーム番線"]
        body:list[list[str]] = []
        for stop in self.route_stations:
            if stop.time != None:
                body.append([f"{stop.time.hour:02}:{stop.time.minute:02}", f"{stop.station}"])
            elif stop.is_start:
                body.append([f"出発駅",f"{stop.station}"])
            else:
                body.append([f"不明",f"{stop.station}"])
        return text + tabulate(body,header)
    
    def inactivate(self) -> TrainData:
        """非アクティブ化する際に実行。遅延・位置等のリアルタイム情報は削除される。"""
        train = TrainData(
            master=self.master,
            wdf=self.wdf,
            actual_train_type=self.train_type,
            actual_direction=self.direction,
            actual_destination=self.destination,
            actual_line=self.line,
            has_premiumcar=self.has_premiumcar,
            train_formation=self.train_formation,
            route_stations=self.route_stations,
            history=self.history,
            date=self.date
        )
        return train

def _state_property(name: str, doc: str = "") -> property:
    """KHState の属性を、KHTracker から同じ名前で読み書きできるようにする"""
    def getter(self: "KHTracker"):
        return getattr(self.state, name)
    def setter(self: "KHTracker", value) -> None:
        setattr(self.state, name, value)
    return property(getter, setter, doc=doc)


def _client_property(name: str, doc: str = "") -> property:
    """KHClient の属性を、KHTracker から同じ名前で読み書きできるようにする"""
    def getter(self: "KHTracker"):
        return getattr(self.client, name)
    def setter(self: "KHTracker", value) -> None:
        setattr(self.client, name, value)
    return property(getter, setter, doc=doc)


class KHTracker:
    """
    京阪電車のリアルタイム列車位置情報を管理するメインクラス。KHClient（取得）と KHState（状態）を合わせたもの。
    - stations: 全駅の辞書（駅番号:StationData）
    - trains: 全列車の辞書（WDF番号:TrainData）
    - fetch_pos, fetch_dia でAPIから最新情報取得
    - snapshot_dir を指定すると、取得したAPIレスポンスをそのまま保存する（デバッグ用）
    """
    def __init__(self, rate_limit:float = 30, snapshot_dir: Optional[str | Path] = None) -> None:
        if rate_limit < MIN_RATE_LIMIT:
            raise ValueError(f"rate_limit は {MIN_RATE_LIMIT} 秒以上にしてください（指定値: {rate_limit}）。")
        date: datetime.date = datetime.datetime.now(JST).date()   # 日度（始発から終電までを1日とする日付）
        # 深夜帯は-1日することで27時の扱い
        if 0 <= datetime.datetime.now(JST).hour < DATE_CHANGE_TIME:
            date = date - datetime.timedelta(days=1)
        self.client = KHClient(snapshot_dir)
        self.state = KHState(date)
        self.file_list: Optional[FileList] = None

        self.last_fetch_pos_datetime: Optional[datetime.datetime] = None # 最後にfetch_posを行った時刻
        self.last_regist_dia_datetime: Optional[datetime.datetime] = None # 最後にregist_diaでfetchした時刻
        self.last_regist_dia_date: Optional[datetime.date] = None
        self.rate_limit_interval:float = rate_limit                      # アクセス間隔

    # KHState の属性。代入もできる
    transfer_guide_info = _state_property("transfer_guide_info")  # 駅ごとの乗り入れデータ
    select_station = _state_property("select_station")           # 路線ごとの駅名データ
    starttime_list = _state_property("starttime_list")           # 列車ごとの駅到着時刻データ
    train_position_list = _state_property("train_position_list") # 列車の種別・位置・遅延情報データ
    date = _state_property("date")                               # 日度（始発から終電までを1日とする日付）
    dia_confirmed_date = _state_property("dia_confirmed_date")   # ダイヤが今日のものだと確かめた営業日
    _first_observed_date = _state_property("_first_observed_date")
    _first_observed_time = _state_property("_first_observed_time") # その営業日に最初に位置情報を取れた時刻（fileCreatedTime）
    # 読み取り専用（書き換えると TypeError）
    trains = property(lambda self: self.state.trains, doc="WDF番号:TrainData。読み取り専用")
    stations = property(lambda self: self.state.stations, doc="駅番号:StationData。読み取り専用")

    # KHClient の属性。代入もできる
    web = _client_property("web")                                # 代入すると、取得に使う AsyncClient が替わる
    snapshot_dir = _client_property("snapshot_dir")              # 取得したレスポンスの保存先（Noneなら保存しない）
    _last_snapshot = _client_property("_last_snapshot")          # ファイル名:前回保存した内容
    _etags = _client_property("_etags")                          # URL:前回受け取ったETag

    @property
    def position_created_at(self) -> Optional[datetime.datetime]:
        """手元の位置情報の作成時刻（fileCreatedTime）。取得に失敗しても変わらないので、データの古さの判断に使う"""
        return self.state.position_created_at

    @property
    def active_trains(self) -> dict[int, ActiveTrainData]:
        return self.state.active_trains

    def find_trains(
            self,
            active:         Optional[bool] = None,
            train_type:           Optional[TrainType] = None,
            direction:      Optional[Literal["up","down"]] = None,
            is_special:     Optional[bool] = None,
            train_number:   Optional[str] = None,
            has_premiumcar: Optional[bool] = None,
            destination:    Optional[StationData] = None,
            next_stop_station:   Optional[StationData] = None,
            min_delay:      Optional[int] = None,
            max_delay:      Optional[int] = None,
            is_stopping:    Optional[bool] = None,
            line:           Optional[LineLiteral] = None,
            current_or_next_stop: Optional[StationData] = None,
            ) -> Sequence[TrainData]:
        """
        条件に合致する列車を検索してリストで返します。
        - active: 走行中か
        - type: 列車種別（例: "普通"）
        - direction: 上り線（京都方面）か下り線（大阪方面）か（"up" | "down"）。予定の列車は始発・終着から推定した方向
        - is_special: 臨時列車かどうか
        - train_number: 列車番号（例: "2201"）
        - has_premiumcar: プレミアムカー付きかどうか
        - destination: 行き先駅
        - current_or_next_stop: 停車中の駅 or 次の停車駅
        - next_stop_station: 【非推奨】current_or_next_stop と同じ
        - line: 路線。走行中の列車は今いる路線、走行中でない列車は TrainData.line（走ったことがあればそのときの路線、なければ停車駅から推定した路線）
        is_special・train_number・current_or_next_stop・is_stopping は走行中の列車だけが持つ値のため、
        指定すると走行中でない列車は当てはまりません。
        """
        # 警告の発生元が呼び出し側になるよう、ここで警告してから渡す
        if next_stop_station is not None:
            _deprecated("find_trains の next_stop_station", "同じ意味の current_or_next_stop を使ってください。")
            if current_or_next_stop is None:
                current_or_next_stop = next_stop_station
        return self.state.find_trains(
            active=active, train_type=train_type, direction=direction, is_special=is_special,
            train_number=train_number, has_premiumcar=has_premiumcar, destination=destination,
            min_delay=min_delay, max_delay=max_delay, is_stopping=is_stopping, line=line,
            current_or_next_stop=current_or_next_stop,
        )

    def delay_growing_trains(
            self,
            line:   Optional[LineLiteral] = None,
            window: datetime.timedelta = datetime.timedelta(minutes=5),
            ratio:  float = 0.9,
            ) -> list[ActiveTrainData]:
        """
        is_delay_growing(window, ratio) が True の走行中の列車を返します。
        - line: 指定すると、今その路線にいる列車だけを返す（find_trains の line と同じ）
        """
        return self.state.delay_growing_trains(line, window, ratio)

    async def _get(self, url: str) -> str:
        """URLを取得して本文を返す。snapshot_dirが指定されていれば保存もする。"""
        return await self.client._get(url)

    async def _get_if_modified(self, url: str) -> Optional[str]:
        """前回の取得から変わっていれば本文を返し、変わっていなければ（304 Not Modified）Noneを返す。"""
        return await self.client._get_if_modified(url)

    #動的データを更新
    async def fetch_pos(self):
        "列車走行位置を更新します。1分に1回が適切でしょう。"
        #不変データをダウンロード
        if not self.select_station:
            url = SELECT_STATION_URL
            self.state.apply_stations(select_station=parse_select_station(await self._get(url), url))
        if not self.transfer_guide_info:
            url = TRANSFER_GUIDE_URL
            self.state.apply_stations(transfer_guide_info=parse_transfer_guide(await self._get(url), url))

        # レート制限
        now = datetime.datetime.now(tz=JST)
        if self.last_fetch_pos_datetime:
            # 前回の取得 + 制限interval
            next_fetch = self.last_fetch_pos_datetime + datetime.timedelta(seconds=self.rate_limit_interval)
            if now <= next_fetch:
                return

        # 列車位置を取得
        self.last_fetch_pos_datetime = now
        # 壊れていれば、列車の状態を変える前に KeihanAPIError で止まる
        url = TRAIN_POSITION_URL
        self.state.apply_positions(parse_positions(await self._get(url), url))
        assert self.train_position_list is not None

        #ダイア情報を登録
        # 始発前は、公式のダイヤがまだ前の日のままのことがある。日付が変わってから、
        # ダイヤが今日のものだと確かめられるまでは、取得のたびに取り直す（DIA_RETRY_DEADLINE まで）
        created = self.train_position_list.fileCreatedTime.time()
        retry = (self.dia_confirmed_date != self.date
                 and datetime.time(DATE_CHANGE_TIME) <= created < DIA_RETRY_DEADLINE)
        if self.starttime_list:
            need_download = (
                retry
                or self.last_regist_dia_datetime is None
                or datetime.datetime.now(JST) - self.last_regist_dia_datetime > datetime.timedelta(hours=1)
                or self.last_regist_dia_date != self.date
            )
            if need_download:
                await self._regist_dia(True)
            else:
                await self._regist_dia(False)
        else:
            await self._regist_dia(True)
        if self.dia_confirmed_date != self.date and self._dia_matches_positions():
            self.dia_confirmed_date = self.date

    def _dia_matches_positions(self) -> Optional[bool]:
        """
        走行中の列車の公式の行先と、ダイヤの終着駅が1本も食い違わないか。前の日のダイヤを使っていると食い違う。
        比べられる列車（行先があり、ダイヤもある走行中の列車）がいなければ None。
        """
        return self.state._dia_matches_positions()

    async def regist_dia(self, download:bool):
        "【非推奨】ダイヤ情報を更新します。更新が必要な際にはfetch_posから自動的に実行されます。"
        _deprecated("regist_dia", "ダイヤは fetch_pos が必要なときに取得するため、呼ばなくてかまいません。")
        return await self._regist_dia(download)

    async def _regist_dia(self, download:bool):
        starttime_list = self.starttime_list
        if download or starttime_list is None:
            url = START_TIME_LIST_URL
            try:
                if starttime_list is None:
                    starttime_list = parse_timetable(await self._get(url), url)
                # 変わっていなければ本体を受け取らず、手元のダイヤをそのまま使う
                elif (text := await self._get_if_modified(url)) is not None:
                    starttime_list = parse_timetable(text, url)
            except KeihanAPIError:
                # 壊れた本文のETagで304が返り続けないよう忘れ、次の取得で取り直す
                self._etags.pop(url, None)
                raise
            # 読み込めてから記録する（先に記録すると、壊れていたとき1時間取り直さない）
            self.last_regist_dia_date = self.date
            self.last_regist_dia_datetime = datetime.datetime.now(JST)
            self.starttime_list = starttime_list

        # startTimeListからデータを登録
        self.state.apply_timetable(starttime_list)
        return self

    async def fetch_filelist(self):
        """【非推奨・未実装】FileList.xmlを取得する。"""
        _deprecated("fetch_filelist", "代わりの機能はありません。")
        text = await self._get("https://www.keihan.co.jp/tinfo/05-flist/FileList.xml")
        root = ET.fromstring(text)

        # 時刻設定
        t = root.findtext("time")
        if t:
            time = datetime.datetime.strptime(t,"%Y%m%d%H%M%S").replace(tzinfo=JST)
        else:
            return
        traininfo = root.findtext("traininfo") or ""
        image_PC = root.findtext("image_PC") or ""
        image_SP = root.findtext("image_SP") or ""
        html_FP = root.findtext("html_FP") or ""

        filelist = FileList(
            time=time,
            traininfo=traininfo,
            image_PC=image_PC,
            image_SP=image_SP,
            html_FP=html_FP
        )
        self.file_list = filelist

    @property
    def max_delay_train(self) -> Optional[ActiveTrainData]:
        """現在もっとも遅延している運行中の列車"""
        return self.state.max_delay_train

    @property
    def max_delay_minutes(self) -> int:
        """現在の最大遅延分数"""
        return self.state.max_delay_minutes
