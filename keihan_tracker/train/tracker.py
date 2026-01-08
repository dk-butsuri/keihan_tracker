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
from pydantic import BaseModel
from typing import Optional, Literal
from httpx import AsyncClient
import json
import xml.etree.ElementTree as ET
from tabulate import tabulate
import datetime
import re

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
        return [train for train in self.master.trains.values() if train.next_stop_station == self]

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

        trains.sort(key = lambda t:t[1].time or datetime.datetime.min)
        return trains
    
    @property
    def upcoming_trains(self) -> list[tuple["TrainData","StopStationData"]]:
        """
        この駅に 今後停車する or 停車中 のすべての列車を返す。
        列車のnext_stop_stationの停車時刻とこの駅に停車する時刻を比較する。
        """
        trains:list[tuple[TrainData, StopStationData]] = []

        #全ての列車から
        for train, stop in self.trains:
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
    wdfBlockNo: int                 # 列車管理番号
    train_formation:Optional[int] = None       # 編成番号（）
    train_number:   str             # 列車番号（1051号など）
    train_type:     TrainType
    is_special:     bool            # 臨時か
    has_premiumcar: Optional[bool] = None           
    lastpass_station:   Optional[StationData] = None
    cars :          int             # 車両数
    destination:    StationData     # 行先駅
    delay_minutes:  int             # 遅延時間
    delay_text:     Optional[MultiLang] = None       # 遅延時間（テキスト）
    direction:      Literal["up","down"]    #方向（up:京都方面、down:大阪方面）
    route_stations: list[StopStationData] = []      # 経路にある駅リスト

    # サイト上で列車位置を表示するときのグリッド座標
    location_col: int
    location_row: int

    # 停車駅リスト
    @property
    def stop_stations(self) -> list[StopStationData]:
        """停車する駅のリストを返します。"""
        stops = [station for station in self.route_stations if station.is_stop == True]
        stops.sort(key = lambda x:x.time or datetime.datetime.min)
        return stops

    # 始発駅（is_startがTrueの停車駅のうち1番目を返す）
    @property
    def start_station(self) -> StationData:
        """列車の始発駅"""
        return [station for station in self.stop_stations if station.is_start][0].station
    
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
            line.reverse()

        if not next_station in line:
            raise IndexError(f"{self.next_station} is not in {self.line}")
        
        # next_stationが停車駅なら
        if next_station in stop_stations:
            return self.master.stations[next_station]
        else:
            index = line.index(next_station)
            for i in range(100):
                if line[index+i] in stop_stations:
                    return self.master.stations[line[index+i]]
                else:
                    continue
            raise IndexError()
        

    def get_stop_time(self, station:StationData) -> Optional[datetime.datetime]:
        """駅に停車する時刻を返します。"""
        for stop in self.stop_stations:
            if stop.station == station:
                return stop.time
        return None

    # 整形して文字列化
    def __str__(self) -> str:
        text = f'[{"上り線" if self.direction == "up" else "下り線"}] {"臨時" if self.is_special else ""}{self.train_type.value}{self.train_number}号: {self.destination or "不明"}行き {self.train_formation}系/{self.cars}両編成\n'\
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
        self.date: datetime.date = datetime.datetime.now().date()    # 日度（始発から終電までを1日とする日付）
        self.web = AsyncClient()

        # wdfBlockNo:TrainData
        ## 現在アクティブな列車リスト
        self.trains:dict[int,TrainData] = {}
        ## 駅リスト
        self.stations:dict[int,StationData] = {}


    def find_trains(
            self, 
            type:           Optional[TrainType] = None, 
            direction:      Optional[Literal["up","down"]] = None, 
            is_special:     Optional[bool] = None, 
            train_number:   Optional[str] = None,
            has_premiumcar: Optional[bool] = None,
            destination:    Optional[StationData] = None,
            next_stop_station:   Optional[StationData] = None,
            min_delay:      Optional[int] = None,
            max_delay:      Optional[int] = None,
            is_stopping:    Optional[bool] = None
            ) -> list[TrainData]:
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
        trains: list[TrainData] = []
        for train in self.trains.values():
            # 条件に合致するかチェック
            if type and train.train_type != type:
                continue
            if direction and train.direction != direction:
                continue
            if is_special is not None and train.is_special != is_special:
                continue
            if train_number and train.train_number != train_number:
                continue
            if has_premiumcar is not None and train.has_premiumcar != has_premiumcar:
                continue
            if destination and train.destination != destination:
                continue
            if next_stop_station and train.next_stop_station != next_stop_station:
                continue
            if min_delay and train.delay_minutes <= min_delay:
                continue
            if max_delay and train.delay_minutes >= max_delay:
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
        
        #古い車両データを削除する
        current_wdfs:set[int] = set()
        for trainlist in self.train_position_list.locationObjects:
            for train in trainlist.trainInfoObjects:
                current_wdfs.add(train.wdfBlockNo)

        # 2. self.trainsに存在するが、今回のJSONにない古いwdfを削除する
        #    (setの差集合を利用する)
        wdfs_to_delete = set(self.trains.keys()) - current_wdfs
        for wdf in wdfs_to_delete:
            del self.trains[wdf]

        # trainPositionListから列車一覧を取得
        for trainlist in self.train_position_list.locationObjects:
            # 同じ場所に2車両以上ある場合（連結等）があるのでリスト形式
            for train in trainlist.trainInfoObjects:
                # 列車識別番号
                wdf = train.wdfBlockNo
                # 存在しないなら新規作成
                if not wdf in self.trains:
                    self.trains[wdf] = TrainData(
                        master=self, 
                        wdfBlockNo=wdf,
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
                        delay_minutes = int(re.sub(r"\D","",train.delayMinutes)) if train.delayMinutes != "" else 0
                        )
                
                # 次に停車する駅
                if train.lastPassStation != 99 and train.lastPassStation != 0:
                    self.trains[wdf].lastpass_station = self.stations[train.lastPassStation]

                self.trains[wdf].delay_text = MultiLang(
                    ja = train.delayMinutes,
                    en = train.delayMinutesEn,
                    cn = train.delayMinutesZhCn,
                    tw = train.delayMinutesZhTw,
                    kr = train.delayMinutesKo
                )

        # 日付更新
        if 0 <= self.train_position_list.fileCreatedTime.hour <= 5:
            #深夜帯は-1日することで27時の扱い
            self.date = self.train_position_list.fileCreatedTime.date() - datetime.timedelta(days=1)
        else:
            self.date = self.train_position_list.fileCreatedTime.date()
        
        #ダイア情報を登録
        if self.starttime_list:
            if (self.starttime_list.fileCreatedTime-datetime.timedelta(hours=5)).date() != self.date:
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
                # self.trains[wdf] = TrainData(wdfBlockNo=wdf)
                # もし存在しなかったらスキップ
                continue

            self.trains[wdf].train_formation = int(train.trainCar)
            self.trains[wdf].has_premiumcar = bool(train.premiumCar)
            # ダイヤ登録
            self.trains[wdf].route_stations = []
            
            for stop_station in train.diaStationInfoObjects:
                # 3桁の上２桁が駅番号
                if len(stop_station.stationNumber) != 3:
                    continue
                station = self.stations.get(int(stop_station.stationNumber[0:2]))
                # platform = stop_station.stationNumber[-1]

                # 未登録の駅ならスキップ（寝屋川信号場など）
                if station == None:
                    continue

                # もし出発駅なら-
                if stop_station.stationDepTime == "-":
                    self.trains[wdf].route_stations.append(
                        StopStationData(
                            is_start = True,
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
                    is_final = False
                    time = None
                # 停車駅なら
                else:
                    is_stop:bool = True
                    time = datetime.datetime.combine(self.date, datetime.time.min) + datetime.timedelta(hours=ltime[0],minutes=ltime[1])
                    is_final = self.trains[wdf].destination == station

                self.trains[wdf].route_stations.append(
                        StopStationData(
                            is_start = False,
                            is_stop = is_stop,
                            is_final = is_final,
                            station = station,
                            time = time
                            )
                        )
                
        return self

    async def fetch_filelist(self):
        """【未実装】FileList.xmlを取得する。"""
        res = await self.web.get("https://www.keihan.co.jp/tinfo/05-flist/FileList.xml")
        res.raise_for_status()
        root = ET.fromstring(res.text)

        # 時刻設定
        t = root.findtext("time")
        if t:
            time = datetime.datetime.strptime(t,"%Y%m%d%H%M%S")
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
        """現在もっとも遅延している列車"""
        return max(self.trains.values(), key=lambda x:x.delay_minutes)

    @property
    def max_delay_minutes(self) -> int:
        """現在の最大遅延分数"""
        return self.max_delay_train.delay_minutes

if __name__ == "__main__":
    tracker = KHTracker()
    print(tracker.trains)