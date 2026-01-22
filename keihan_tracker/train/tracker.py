# 遅延情報はデータ形式が不明のため未実装

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
from pydantic import BaseModel, Field
import warnings
from typing import Optional, Literal, Sequence
from httpx import AsyncClient
import json
import xml.etree.ElementTree as ET
from tabulate import tabulate
import datetime
import re
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

class StationData(BaseModel):
    """
    駅情報を表すモデル。
    """
    master:        "KHTracker" #親インスタンス
    line:           set[LineLiteral]
    station_number: int
    station_name:   MultiLang
    transfer:       StationConnections = StationConnections()

    @property
    def arriving_trains(self):
        """
        この駅に 次に停車する予定 or 停車中 の列車リストを返す。
        KHTracker.trains から該当駅が next_stop_station になっている列車を抽出する。
        """
        return [train for train in self.master.active_trains.values() if train.next_stop_station == self]

    @property
    def trains(self) -> list[tuple["TrainData","StopStationData"]]:
        """
        この駅に 今後停車する or 停車中 or 停車した すべての列車返す。[((12,00),TrainData), ...]
        KHTracker.trains から、stop_stationsに自身（self）が含まれる列車を抽出する。
        """
        trains:list[tuple[TrainData,StopStationData]] = []
        for train in self.master.trains.values():
            for stop in train.stop_stations:
                if stop.station == self and stop.is_stop:
                    trains.append((train,stop))

        trains.sort(key = lambda t:t[1].time or datetime.datetime.min.replace(tzinfo=JST))
        return trains
    
    @property
    def upcoming_trains(self) -> list[tuple["TrainData|ActiveTrainData","StopStationData"]]:
        """
        この駅に 今後停車する or 停車中 のすべての列車を返す。
        列車のnext_stop_stationの停車時刻とこの駅に停車する時刻を比較する。
        """
        trains:list[tuple[(TrainData|ActiveTrainData), StopStationData]] = []

        # 全ての列車から
        for train, stop in self.trains:
            if isinstance(train, ActiveTrainData):
                # next_stop_stationが不明ならスキップ
                if not train.next_stop_station:
                    continue
                
                # もし始発駅 or selfが次に停車する駅なら
                if train.next_stop_station == self:
                    trains.append((train,stop))
                    continue

                # その列車がこの駅(self)に停車する時刻を取得
                train_stops_self_time = stop.time
                # その列車がnext_stationの駅に停車する時刻を取得
                train_stops_next_time = train.get_stop_time(train.next_stop_station)

                # どちらかが存在しないならスキップ
                if not train_stops_next_time or not train_stops_self_time:
                    continue
                
                #この駅のほうが大きい(=未来)なら
                if train_stops_self_time >= train_stops_next_time:
                    trains.append((train,stop))
                else:
                    continue
            else:
                if self in [s.station for s in train.stop_stations] and train.status == "scheduled":
                    trains.append((train,stop))

        trains.sort(key=lambda x:x[1].time or datetime.datetime.min.replace(tzinfo=JST))
        return trains

    def __str__(self):
        return self.station_name.ja
    
    # masterの型を許可する
    model_config = {"arbitrary_types_allowed": True}


class StopStationData(BaseModel):
    """
    列車の停車駅情報を表すモデル。
    - is_start: 始発駅か
    - is_stop: 停車駅か
    - station: 駅データ
    - time: 標準到着時刻（datetime）
    """

    is_start:   bool = False   # 始発駅かどうか
    is_final:   bool = False   # 終着駅かどうか
    is_stop:    bool = True    # 停車するかどうか
    station:    StationData
    time:       Optional[datetime.datetime] = None #標準到着時刻、(12,00)の形式

class TrainData(BaseModel):
    """
    列車情報を表すモデル。
    - 列車番号、種別、編成、行先、停車駅リストなどを保持
    - stop_stations, start_station, get_stop_time で駅・時刻情報取得
    """
    master:     "KHTracker"
    wdfBlockNo:int
    date:datetime.date
    has_premiumcar: Optional[bool]
    train_formation: Optional[int]
    route_stations: list[StopStationData] = Field(default_factory=list)      # 経路にある駅リスト
    is_completed:bool = False # Falseは必ずしも運行前、運行中であるとは限らない
    delay_minutes: int = 0
    # 以下はActiveだった時のデータを保持する変数
    actual_train_type: Optional[TrainType] = None
    actual_direction: Optional[Literal["up","down"]] = None

    @property
    def line(self) -> LineLiteral:
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
    def direction(self) -> Literal["up","down"]:
        """始発・終着駅から方向を推定する"""
        # アクティブ時のデータが残っているならそのまま返す
        if self.actual_direction is not None:
            return self.actual_direction

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
        """
        # アクティブ時のデータが残っているならそのまま返す
        if self.actual_train_type is not None:
            return self.actual_train_type

        stop_stations_list = [stop.station for stop in self.stop_stations]

        try:
            st_kyobashi = self.master.stations[4]      # KH04 京橋
            st_noe = self.master.stations[5]           # KH05 野江
            st_moriguchishi = self.master.stations[11] # KH11 守口市
            st_kadomashi = self.master.stations[13]    # KH13 門真市
            st_kayashima = self.master.stations[16]    # KH16 萱島
            st_neyagawashi = self.master.stations[17]  # KH17 寝屋川市
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
                
                if "ライナー" in getattr(self, 'name', ''):
                    return TrainType.LINER
                return TrainType.LTD_EXP
            
            else:
                # 鳥羽街道まで行かない短距離列車（例：出町柳～三条）
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
                if "ライナー" in getattr(self, 'name', ''):
                    return TrainType.LINER
                return TrainType.LTD_EXP # 特急

    # 始発駅（is_startがTrueの停車駅のうち1番目を返す）
    @property
    def start_station(self) -> StationData:
        start_stations = [station for station in self.route_stations if station.is_start]
        if len(start_stations) != 1:
            raise ValueError(f"始発駅が複数登録されています。これはバグです。\n({start_stations})")
        station = start_stations[0].station
        return station

    @property
    def destination(self) -> StationData:
        stop_stationdata = [station for station in self.route_stations if station.is_final]
        if len(stop_stationdata) != 1:
            raise ValueError(f"終着駅が複数登録されています。これはバグです。\n({stop_stationdata})")
        station = stop_stationdata[0].station
        return station
    
    @property
    def status(self) -> Literal["active","scheduled","completed"]:
        """運行情報を推定します。completed/scheduledの精度は保証されません。"""
        if isinstance(self, ActiveTrainData):
            return "active"
        if self.is_completed == True:
            return "completed"
        # もし終着駅の予定時刻を過ぎていたらcompleted
        stop_time = self.get_stop_time(self.destination)
        if stop_time:
            if stop_time < datetime.datetime.now(JST):
                return "completed"
        return "scheduled"

    # 停車駅リスト
    @property
    def stop_stations(self) -> list[StopStationData]:
        """停車する駅のリストを返します。"""
        stops = [station for station in self.route_stations if station.is_stop == True]
        stops.sort(key = lambda x:x.time or datetime.datetime.min.replace(tzinfo=JST))
        return stops
    
    def get_stop_time(self, station:StationData) -> Optional[datetime.datetime]:
        """駅に停車する時刻を返します。"""
        for stop in self.stop_stations:
            if stop.station == station:
                return stop.time
        return None
        # 整形して文字列化

    def __str__(self) -> str:
        text = f'【非アクティブ】{self.train_type.value} {self.destination or "不明"} 行き（{self.start_station} 発）\n'
        text += f'{self.train_formation}編成 {"プレミアムカー付き /" if self.has_premiumcar else ""}\n'

        header = ["到着時刻","停車駅","ホーム番線"]
        body = []
        for stop in self.stop_stations:
            if stop.time != None:
                body.append([f"{stop.time.hour:02}:{stop.time.minute:02}", f"{stop.station}"])
            elif stop.is_start:
                body.append([f"出発駅",f"{stop.station}"])
            else:
                body.append([f"不明",f"{stop.station}"])
        return text + tabulate(body,header)
    
    # masterの型を許可する
    model_config = {"arbitrary_types_allowed": True}


# ActiveTrainDataによる属性上書きの警告を無効化
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
class ActiveTrainData(TrainData):
    """
    現在アクティブな列車情報を表すモデル。
    - 列車番号、種別、編成、行先、停車駅リストなどを保持
    - stop_stations, start_station, get_stop_time で駅・時刻情報取得
    """
    train_formation:Optional[int] = None       # 編成番号
    train_number:   str             # 列車番号（1051号など）
    active_train_type:  TrainType            = Field(alias="train_type")
    active_direction:   Literal["up","down"] = Field(alias="direction")     # 方向（up:京都方面、down:大阪方面）
    active_destination: StationData          = Field(alias="destination")   # 行先駅
    is_special:     bool            # 臨時か
    has_premiumcar: Optional[bool] = None
    lastpass_station:   Optional[StationData] = None
    cars :          int             # 車両数
    delay_text:     MultiLang       # 遅延時間（テキスト）

    # サイト上で列車位置を表示するときのグリッド座標
    location_col: int
    location_row: int
    
    @property
    def train_type(self):
        return self.active_train_type

    @property
    def destination(self):
        return self.active_destination

    @property
    def direction(self) -> Literal["up","down"]:
        return self.active_direction

    @property
    def is_stopping(self) -> bool:
        """列車が停車中かどうか"""
        line, st1, st2 = calc_position(self.location_col, self.location_row)
        # st2がなければ停車中
        return True if not st2 else False
    
    @property
    def line(self) -> LineLiteral:
        line, st1, st2 = calc_position(self.location_col, self.location_row)
        return line

    @property
    def next_station(self):
        """停車中の駅、もしくは次に停車（通過）する駅を返します。"""
        line, st1, st2 = calc_position(self.location_col, self.location_row)
        if not st2:
            return self.master.stations[st1]
        else:
            sts = sorted((st1,st2))
            # 京阪本線のみ、番号が大きい方が上り
            # そのほかでは小さい方が上り
            if self.direction == "up":
                if self.line == "京阪本線・鴨東線":
                    return self.master.stations[sts[1]] # 大きい方
                else:
                    return self.master.stations[sts[0]] # 小さい方

            else:
                if self.line == "京阪本線・鴨東線":
                    return self.master.stations[sts[0]]
                else:
                    return self.master.stations[sts[1]]

    @property
    def next_stop_station(self) -> Optional[StationData]:
        next_station = self.next_station.station_number
        stop_stations = [station.station.station_number for station in self.stop_stations]

        match self.line:
            case "中之島線":
                line = stations_map.NAKANOSHIMA_UP
            case "交野線":
                line = stations_map.KATANO_UP
            case "京阪本線・鴨東線":
                line = stations_map.HONNSEN_UP
            case "宇治線":
                line = stations_map.UJI_UP
            case _:
                raise ValueError()
        if self.direction == "down":
            line = list(reversed(line))

        if not next_station in line:
            raise IndexError(f"{self.next_station} is not in {self.line}")
        
        # next_stationが停車駅なら
        if next_station in stop_stations:
            return self.master.stations[next_station]
        else:
            index = line.index(next_station)
            for i in range(100):
                if index + i >= len(line): # 範囲チェックを追加
                    break
                if line[index+i] in stop_stations:
                    return self.master.stations[line[index+i]]
                else:
                    continue
            # 不明な場合
            return None

    # 整形して文字列化
    def __str__(self) -> str:
        text = f'【{"上り線" if self.direction == "up" else "下り線"}】 {"臨時" if self.is_special else ""}{self.train_type.value} {self.train_number}号: {self.destination or "不明"}行き {self.train_formation}編成/{self.cars}両 '
        text += f'(col, row: {self.location_col}, {self.location_row})\n'\
        f'{"プレミアムカー付き /" if self.has_premiumcar else ""}遅延：{self.delay_minutes if self.delay_minutes != None else "不明"} 分\n'
        text += f'次の停車駅は【{self.next_stop_station}駅】\n\n' if not self.is_stopping else f"【{self.next_station}駅】に停車中\n\n"

        header = ["到着時刻","停車駅","ホーム番線"]
        body = []
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
            wdfBlockNo=self.wdfBlockNo,
            actual_train_type=self.train_type,
            actual_direction=self.direction,
            has_premiumcar=self.has_premiumcar,
            train_formation=self.train_formation,
            route_stations=self.route_stations,
            is_completed=True,
            date=self.date
        )
        return train

    # masterの型を許可する
    model_config = {"arbitrary_types_allowed": True}

class KHTracker:
    """
    京阪電車のリアルタイム列車位置情報を管理するメインクラス。
    - stations: 全駅の辞書（駅番号:StationData）
    - trains: 全列車の辞書（列車管理番号:TrainData）
    - fetch_pos, fetch_dia でAPIから最新情報取得
    """
    def __init__(self) -> None:
        #パースしたJSONデータ（BaseModel）
        self.transfer_guide_info: Optional[TransferGuideInfo] = None # 駅ごとの乗り入れデータ
        self.select_station: Optional[SelectStation] = None          # 路線ごとの駅名データ
        self.starttime_list: Optional[startTimeList] = None          # 列車ごとの駅到着時刻データ
        self.train_position_list: Optional[trainPositionList] = None # 列車の種別・位置・遅延情報データ
        self.file_list: Optional[FileList] = None
        self.date: datetime.date = datetime.datetime.now(JST).date()    # 日度（始発から終電までを1日とする日付）
        self.web = AsyncClient()

        # wdfBlockNo:TrainData
        ## 現在アクティブな列車リスト
        self.trains:dict[int, TrainData|ActiveTrainData] = {}
        ## 駅リスト
        self.stations:dict[int, StationData] = {}

    @property
    def active_trains(self) -> dict[int, ActiveTrainData]:
        d:dict[int, ActiveTrainData] = {}
        for train in self.trains.values():
            if isinstance(train, ActiveTrainData):
                d[train.wdfBlockNo] = train
        return d

    def find_trains(
            self, 
            status: Optional[Literal['active', 'scheduled', 'completed']] = None,
            train_type:           Optional[TrainType] = None, 
            direction:      Optional[Literal["up","down"]] = None, 
            is_special:     Optional[bool] = None, 
            train_number:   Optional[str] = None,
            has_premiumcar: Optional[bool] = None,
            destination:    Optional[StationData] = None,
            next_stop_station:   Optional[StationData] = None,
            min_delay:      Optional[int] = None,
            max_delay:      Optional[int] = None,
            is_stopping:    Optional[bool] = None
            ) -> Sequence[TrainData]:
        """
        条件に合致する列車を検索してリストで返します。
        - type: 列車種別（例: "普通"）
        - direction: 上り線（京都方面）か下り線（大阪方面）か（"up" | "down"）
        - is_special: 臨時列車かどうか
        - train_number: 列車番号（例: "2201"）
        - has_premiumcar: プレミアムカー付きかどうか
        - destination: 行き先駅
        - next_stop_station: 次の停車駅 or 停車中の駅
        """
        trains: list[TrainData|ActiveTrainData] = []
        for train in self.trains.values():
            # 条件に合致するかチェック
            if status is not None       and status != train.status:
                continue
            if train_type is not None   and train.train_type != train_type:
                continue
            if has_premiumcar is not None and train.has_premiumcar != has_premiumcar:
                continue
            if destination is not None  and train.destination != destination:
                continue
            if min_delay is not None    and train.delay_minutes <= min_delay:
                continue
            if max_delay is not None    and train.delay_minutes >= max_delay:
                continue

            if isinstance(train, ActiveTrainData):
                if next_stop_station and train.next_stop_station != next_stop_station:
                    continue
                if direction and train.direction != direction:
                    continue
                if is_special is not None and train.is_special != is_special:
                    continue
                if train_number and train.train_number != train_number:
                    continue
                if is_stopping != None:
                    if train.is_stopping != is_stopping:
                        continue
            
            trains.append(train)
        return trains


    #動的データを更新
    async def fetch_pos(self):
        "列車走行位置を更新します。30秒～数分に一回が適切でしょう。"
        #不変データをダウンロード
        if not self.select_station:
            res = await self.web.get("https://www.keihan.co.jp/zaisen/select_station.json")
            res.raise_for_status()
            self.select_station = SelectStation.model_validate(json.loads(res.text))
            # select_stationから駅データを登録
            for line,line_detail in self.select_station.root.items():
                for number, name in line_detail.stations.items():
                    number = int(number[2:])
                    # 既に登録されているなら路線に追加
                    if number in self.stations:
                        self.stations[number].line.add(line)
                        continue
                    self.stations[number] = StationData(
                                                master = self,
                                                line   = {line},
                                                station_number = number,
                                                station_name = name,
                    )
        if not self.transfer_guide_info:
            res = await self.web.get("https://www.keihan.co.jp/zaisen/transferGuideInfo.json")
            res.raise_for_status()
            self.transfer_guide_info = TransferGuideInfo.model_validate(json.loads(res.text))
            # transferGuideInfoから乗り換え情報を登録
            for number, transfers in self.transfer_guide_info.root.items():
                number = int(number[2:])
                self.stations[number].transfer = transfers
        
        #列車位置を取得
        res = await self.web.get("https://www.keihan.co.jp/zaisen-up/trainPositionList.json")
        res.raise_for_status()
        self.train_position_list = trainPositionList.model_validate(json.loads(res.text))
        del res

        old_wdfs: list[int] = []
        # 前日の列車があれば削除
        for wdf, train in self.trains.items():
            if train.date != self.date:
                old_wdfs.append(wdf)
        for wdf in old_wdfs:
            del self.trains[wdf]

        # もう運行終了したActiveTrainDataをinactive化する
        # 1. 現在アクティブな列車集合を取得
        current_wdfs:set[int] = set()
        for trainlist in self.train_position_list.locationObjects:
            for train in trainlist.trainInfoObjects:
                current_wdfs.add(train.wdfBlockNo)

        # 2. 差集合で もうアクティブでなくなった列車を計算
        wdfs_to_delete = set([train for train in self.trains.keys()]) - current_wdfs
        for wdf in wdfs_to_delete:
            if isinstance(self.trains[wdf], ActiveTrainData):
                self.trains[wdf] = self.active_trains[wdf].inactivate()

        # trainPositionListから列車一覧を取得
        for trainlist in self.train_position_list.locationObjects:
            # 同じ場所に2編成以上ある場合（連結等）があるのでリスト形式
            for train in trainlist.trainInfoObjects:
                wdf = train.wdfBlockNo
                # 存在しない/アクティブでないなら新規作成
                if self.trains.get(wdf) == None or not isinstance(self.trains.get(wdf), ActiveTrainData):
                    self.trains[wdf] = ActiveTrainData(
                        master=self, 
                        wdfBlockNo=wdf,
                        date=self.date,
                        train_number = train.trainNumber,
                        destination = self.stations[train.destStationNumber],
                        train_type = train.trainTypeJp,
                        is_special = train.is_special,
                        cars = train.carsOfTrain,
                        # up:京都方面（京阪本線）、枚方市方面（交野線）、中書島方面（宇治線）
                        # down: 大阪方面、私市方面、宇治方面
                        direction = "up" if trainlist.trainDirection == 0 else "down",
                        location_col = trainlist.locationCol,
                        location_row = trainlist.locationRow,
                        delay_text = MultiLang(
                            ja = train.delayMinutes,
                            en = train.delayMinutesEn,
                            cn = train.delayMinutesZhCn,
                            tw = train.delayMinutesZhTw,
                            kr = train.delayMinutesKo
                        ),
                        delay_minutes = int(re.sub(r"\D","",train.delayMinutes)) if train.delayMinutes != "" else 0
                        )
                elif type(self.trains[wdf]) == ActiveTrainData:
                    # 可変の情報を更新
                    active_train = self.active_trains[wdf]
                    active_train.location_col = trainlist.locationCol
                    active_train.location_row = trainlist.locationRow
                    active_train.delay_text = MultiLang(
                        ja = train.delayMinutes,
                        en = train.delayMinutesEn,
                        cn = train.delayMinutesZhCn,
                        tw = train.delayMinutesZhTw,
                        kr = train.delayMinutesKo
                    )
                    active_train.delay_minutes = int(re.sub(r"\D","",train.delayMinutes)) if train.delayMinutes != "" else 0

                    # lastPassStationを更新
                    if train.lastPassStation != 99 and train.lastPassStation != 0:
                        active_train.lastpass_station = self.stations[train.lastPassStation]
                    else:
                        active_train.lastpass_station = None

        # 日付更新
        if 0 <= self.train_position_list.fileCreatedTime.hour <= 5:
            #深夜帯は-1日することで27時の扱い
            self.date = self.train_position_list.fileCreatedTime.date() - datetime.timedelta(days=1)
        else:
            self.date = self.train_position_list.fileCreatedTime.date()
        
        #ダイア情報を登録
        if self.starttime_list:
            if (datetime.datetime.now(JST)-self.starttime_list.fileCreatedTime) > datetime.timedelta(hours=1):
                await self.regist_dia(True)
            else:
                await self.regist_dia(False)
        else:
            await self.regist_dia(True)

    async def regist_dia(self, download:bool):
        "ダイヤ情報を更新します。更新が必要な際にはfetch_posから自動的に実行されます。"
        if download or self.starttime_list == None:
            res = await self.web.get("https://www.keihan.co.jp/zaisen-up/startTimeList.json")
            res.raise_for_status()
            self.starttime_list = startTimeList.model_validate(json.loads(res.text))
            del res

        # startTimeListからデータを登録
        for train in self.starttime_list.TrainInfo:
            wdf = train.wdfBlockNo
            if not wdf in self.trains:
                self.trains[wdf] = TrainData(
                    master=self,
                    wdfBlockNo=wdf,
                    has_premiumcar=bool(train.premiumCar),
                    train_formation=int(train.trainCar),
                    date=self.date
                )
            
            self.trains[wdf].train_formation = int(train.trainCar)
            self.trains[wdf].has_premiumcar = bool(train.premiumCar)
                
            # ダイヤ登録
            self.trains[wdf].route_stations = []
            for stop_station in (train.diaStationInfoObjects):
                # 3桁の上２桁が駅番号
                if len(stop_station.stationNumber) != 3:
                    continue
                station = self.stations.get(int(stop_station.stationNumber[0:2]))

                # 未登録の駅ならスキップ（寝屋川信号場など）
                if station == None:
                    continue

                # もし出発駅なら-
                if stop_station.stationDepTime == "-":
                    self.trains[wdf].route_stations.append(
                        StopStationData(
                            is_start = True,
                            is_final=False,
                            is_stop = True,
                            station = station,
                            time = None
                            )
                        )
                    continue

                ltime = tuple(map(int,stop_station.stationDepTime.split(":"))) #22:30 -> (22,30)
                # 停車しない駅
                if ltime == (99,99):
                    is_stop:bool = False
                    time = None
                # 停車駅なら
                else:
                    is_stop:bool = True
                    time = datetime.datetime.combine(self.date, datetime.time.min) + datetime.timedelta(hours=ltime[0],minutes=ltime[1])
                    time = time.replace(tzinfo=JST)
                self.trains[wdf].route_stations.append(
                        StopStationData(
                            is_start = False,
                            is_stop = is_stop,
                            is_final = False,
                            station = station,
                            time = time
                            )
                        )
            # 終着駅
            final_station = max(self.trains[wdf].route_stations, key=lambda x:x.time or datetime.datetime.min.replace(tzinfo=JST))
            final_station.is_final = True

        return self

    async def fetch_filelist(self):
        """【未実装】FileList.xmlを取得する。"""
        res = await self.web.get("https://www.keihan.co.jp/tinfo/05-flist/FileList.xml")
        res.raise_for_status()
        root = ET.fromstring(res.text)

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
    def max_delay_train(self) -> TrainData:
        """現在もっとも遅延している運行中の列車"""
        return max(self.trains.values(), key=lambda x:x.delay_minutes if isinstance(x, ActiveTrainData) else 0)

    @property
    def max_delay_minutes(self) -> int:
        """現在の最大遅延分数"""
        return self.max_delay_train.delay_minutes if isinstance(self.max_delay_train, ActiveTrainData) else 0

if __name__ == "__main__":
    tracker = KHTracker()
    print(tracker.trains)