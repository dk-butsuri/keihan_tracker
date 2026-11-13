"""
列車・駅の状態を管理する。通信も時計も持たない。
パース済みのモデルを apply_stations → apply_positions → apply_timetable の順に渡して更新する。
（臨時列車は走行中のものだけダイヤに登録するため、ダイヤは位置情報の後に渡す）
営業日は、位置情報の fileCreatedTime だけから決める。
"""

from __future__ import annotations

from .schemas import (TransferGuideInfo,
                      SelectStation,
                      startTimeList,
                      TrainInfo,
                      trainPositionList,
                      MultiLang,
                      TrainType,
                      LineLiteral,
                      )
from .parse import _parse_delay_minutes
import warnings
from typing import Mapping, Optional, Literal, TYPE_CHECKING, overload
from types import MappingProxyType
import datetime
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    # tracker.py が state.py を import するため、実行時には関数の中で import する
    from .tracker import StationData, TrainData, ActiveTrainData, StopStationData, TrainStatus

JST = ZoneInfo("Asia/Tokyo")
# 営業日が変わる時刻。終電（2時ごろまで）と、始発前に列車が現れる4時半ごろの間
DATE_CHANGE_TIME = 4


class KHState:
    """
    京阪電車の列車・駅の状態。
    - stations: 全駅の辞書（駅番号:StationData）。読み取り専用
    - trains: 全列車の辞書（WDF番号:TrainData）。読み取り専用
    - apply_stations, apply_positions, apply_timetable にパース済みのデータを渡して更新する
    """
    def __init__(self, date: Optional[datetime.date] = None) -> None:
        #パースしたJSONデータ（BaseModel）
        self.transfer_guide_info: Optional[TransferGuideInfo] = None # 駅ごとの乗り入れデータ
        self.select_station: Optional[SelectStation] = None          # 路線ごとの駅名データ
        self.starttime_list: Optional[startTimeList] = None          # 列車ごとの駅到着時刻データ
        self.train_position_list: Optional[trainPositionList] = None # 列車の種別・位置・遅延情報データ
        # 日度（始発から終電までを1日とする日付）。apply_positions が位置情報から決める。
        # apply_positions の前に apply_timetable を呼ぶときは、ここで渡しておく
        self.date: Optional[datetime.date] = date
        self.dia_confirmed_date: Optional[datetime.date] = None # ダイヤが今日のものだと確かめた営業日
        self._first_observed_date: Optional[datetime.date] = None
        self._first_observed_time: Optional[datetime.datetime] = None # その営業日に最初に位置情報を取れた時刻（fileCreatedTime）
        # 外からは trains・stations（読み取り専用）で見せる。索引とずれないよう、書き換えは apply_* だけが行う
        ## WDF番号:TrainData
        self._trains:dict[int, TrainData|ActiveTrainData] = {}
        ## 駅番号:StationData
        self._stations:dict[int, StationData] = {}

        # 以下はダイヤの差分更新と、駅→停車駅の索引のための内部状態（apply_timetable の docstring を参照）
        # テスト用：True なら、差分更新をせず、毎回全列車のダイヤを登録し直す
        self._always_full_timetable: bool = False
        self._registered_timetable: Optional[startTimeList] = None      # 最後に全登録したダイヤ
        self._registered_date: Optional[datetime.date] = None           # 最後に全登録したときの営業日
        self._timetable_by_wdf: dict[int, TrainInfo] = {}                # 最後に全登録したダイヤの、WDF番号:列車
        self._unregistered_wdfs: set[int] = set()                        # apply_positions で新しく作り、まだダイヤを登録していない列車
        self._stops_by_station: dict[int, dict[int, list[StopStationData]]] = {} # 駅番号:{WDF番号:停車する停車駅（走行順）}

    @property
    def trains(self) -> Mapping[int, TrainData|ActiveTrainData]:
        """全列車（WDF番号:TrainData）。読み取り専用（書き換えると TypeError）"""
        return MappingProxyType(self._trains)

    @property
    def stations(self) -> Mapping[int, StationData]:
        """全駅（駅番号:StationData）。読み取り専用（書き換えると TypeError）"""
        return MappingProxyType(self._stations)

    @property
    def position_created_at(self) -> Optional[datetime.datetime]:
        """手元の位置情報の作成時刻（fileCreatedTime）。取得に失敗しても変わらないので、データの古さの判断に使う"""
        if self.train_position_list is None:
            return None
        return self.train_position_list.fileCreatedTime

    @property
    def active_trains(self) -> dict[int, ActiveTrainData]:
        from .tracker import ActiveTrainData
        d:dict[int, ActiveTrainData] = {}
        for train in self._trains.values():
            if isinstance(train, ActiveTrainData):
                d[train.wdf] = train
        return d

    def apply_stations(
            self,
            select_station: Optional[SelectStation] = None,
            transfer_guide_info: Optional[TransferGuideInfo] = None,
            ) -> None:
        """
        駅の情報を登録する。渡されたものだけ登録する（どちらも登録すると、駅名と乗り換えがそろう）。
        乗り換えは駅名の登録後でないと登録できない。
        """
        from .tracker import StationData
        if select_station is not None:
            self.select_station = select_station
            # select_stationから駅データを登録
            for line,line_detail in self.select_station.root.items():
                for number, name in line_detail.stations.items():
                    number = int(number[2:])
                    # 既に登録されているなら路線に追加
                    if number in self._stations:
                        self._stations[number].line.add(line)
                        continue
                    self._stations[number] = StationData(
                                                master = self,
                                                line   = {line},
                                                station_number = number,
                                                station_name = name,
                    )
        if transfer_guide_info is not None:
            self.transfer_guide_info = transfer_guide_info
            # transferGuideInfoから乗り換え情報を登録
            for number, transfers in self.transfer_guide_info.root.items():
                number = int(number[2:])
                self._stations[number].transfer = transfers

    def apply_positions(self, train_position_list: trainPositionList) -> None:
        """列車の位置情報を反映する。営業日は fileCreatedTime から決める。"""
        from .tracker import ActiveTrainData
        self.train_position_list = train_position_list

        # 日付更新
        if 0 <= self.train_position_list.fileCreatedTime.hour < DATE_CHANGE_TIME:
            #深夜帯は-1日することで27時の扱い
            self.date = self.train_position_list.fileCreatedTime.date() - datetime.timedelta(days=1)
        else:
            self.date = self.train_position_list.fileCreatedTime.date()
        if self._first_observed_date != self.date:
            self._first_observed_date = self.date
            self._first_observed_time = self.train_position_list.fileCreatedTime

        # 前日の列車があれば削除
        old_wdfs: list[int] = []
        for wdf, train in self._trains.items():
            if train.date != self.date:
                old_wdfs.append(wdf)
        for wdf in old_wdfs:
            self._index_remove(wdf)
            del self._trains[wdf]

        # もう運行終了したActiveTrainDataをinactive化する
        # 1. 現在アクティブな列車集合を取得
        current_wdfs:set[int] = set()
        for trainlist in self.train_position_list.locationObjects:
            for train in trainlist.trainInfoObjects:
                current_wdfs.add(train.wdfBlockNo)

        # 2. 差集合で もうアクティブでなくなった列車を計算
        wdfs_to_delete = set([train for train in self._trains.keys()]) - current_wdfs
        for wdf in wdfs_to_delete:
            train = self._trains[wdf]
            if isinstance(train, ActiveTrainData):
                self._replace_train(wdf, train.inactivate())

        # trainPositionListから列車一覧を取得
        for trainlist in self.train_position_list.locationObjects:
            # 同じ場所に2編成以上ある場合（連結等）があるのでリスト形式
            for train in trainlist.trainInfoObjects:
                wdf = train.wdfBlockNo
                # 存在しない/アクティブでないなら新規作成
                if self._trains.get(wdf) == None or not isinstance(self._trains.get(wdf), ActiveTrainData):
                    # 一度消えてまた走り出した列車は、それまでの履歴と、ダイヤから登録した値（停車駅・編成・プレミアムカー）を引き継ぐ
                    old = self._trains.get(wdf)
                    new_active_train = ActiveTrainData(
                        master=self,
                        history = old.history if old else [],
                        route_stations = old.route_stations if old else [],
                        has_premiumcar = old.has_premiumcar if old else None,
                        train_formation = old.train_formation if old else None,
                        wdf=wdf,
                        date=self.date,
                        train_number = train.trainNumber,
                        destination = self._stations.get(train.destStationNumber),
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
                        delay_minutes = _parse_delay_minutes(train.delayMinutes),
                        position_since = self.train_position_list.fileCreatedTime,
                        )
                    self._replace_train(wdf, new_active_train)
                    if old is None:
                        # 初めて現れた列車は、まだダイヤを登録していない（臨時列車のこともある）
                        self._unregistered_wdfs.add(wdf)
                    new_active_train._record_history(self.train_position_list.fileCreatedTime)

                elif type(self._trains[wdf]) == ActiveTrainData:
                    # 可変の情報を更新
                    active_train = self._trains[wdf]
                    old_coordinate = (active_train.location_row, active_train.location_col)
                    new_coordinate = (trainlist.locationRow, trainlist.locationCol)
                    active_train.location_row = new_coordinate[0]
                    active_train.location_col = new_coordinate[1]
                    active_train.delay_text = MultiLang(
                        ja = train.delayMinutes,
                        en = train.delayMinutesEn,
                        cn = train.delayMinutesZhCn,
                        tw = train.delayMinutesZhTw,
                        kr = train.delayMinutesKo
                    )
                    active_train.delay_minutes = _parse_delay_minutes(train.delayMinutes)

                    # 行先を更新（運転整理で変わることがある）。行先なし(00)の間は直前の値を保つ
                    if train.destStationNumber in self._stations:
                        active_train.active_destination = self._stations[train.destStationNumber]

                    # 方向を更新（出現直後の1分だけ誤った値になることがある）
                    # ただし終点に停車中は次の運行の向きに変わるため、そのままにする
                    station = active_train.position.station
                    at_terminal = station is not None and station == active_train.destination
                    if not at_terminal:
                        active_train.active_direction = "up" if trainlist.trainDirection == 0 else "down"

                    # 座標が変わったら、今の座標に来た時刻を更新する
                    if old_coordinate != new_coordinate:
                        active_train.position_since = self.train_position_list.fileCreatedTime

                    active_train._record_history(self.train_position_list.fileCreatedTime)

    def _replace_train(self, wdf: int, train: TrainData) -> None:
        """
        self._trains[wdf] を置き換える（辞書の中の位置は変わらない）。
        停車駅は新しい列車が引き継ぐため、停車駅の逆参照（train）を付け替える。索引の中身（停車駅）は変わらない。
        """
        self._trains[wdf] = train
        for stop in train.route_stations:
            stop.train = train

    # --- 駅→停車駅の索引 ---

    def _index_add(self, train: TrainData) -> None:
        """train の停車駅（stop_stations）を索引に加える"""
        for stop in train.route_stations:
            if stop.is_stop:
                self._stops_by_station.setdefault(stop.station.station_number, {}).setdefault(train.wdf, []).append(stop)

    def _index_remove(self, wdf: int) -> None:
        """WDF番号が wdf の列車の停車駅を、索引から外す"""
        for by_wdf in self._stops_by_station.values():
            by_wdf.pop(wdf, None)

    def _rebuild_index(self) -> None:
        """
        索引を self._trains から作り直す。停車駅の逆参照（train）も合わせ直す。
        列車の route_stations の中身を書き換えたときは、これを呼んで合わせる。
        """
        self._stops_by_station = {}
        for train in self._trains.values():
            for stop in train.route_stations:
                stop.train = train
            self._index_add(train)

    def _stops_at(self, station: StationData) -> list[StopStationData]:
        """
        station に停車する停車駅を、self._trains の並び順（同じ列車の中では走行順）で返す。
        索引は駅ごとの辞書のため、並び順は self._trains のキーの順位で決め直す。
        """
        by_wdf = self._stops_by_station.get(station.station_number)
        if not by_wdf:
            return []
        rank = {wdf: i for i, wdf in enumerate(self._trains)}
        stops: list[StopStationData] = []
        for wdf in sorted(by_wdf, key=rank.__getitem__):
            stops += by_wdf[wdf]
        return stops

    def _dia_matches_positions(self) -> Optional[bool]:
        """
        走行中の列車の公式の行先と、ダイヤの終着駅が1本も食い違わないか。前の日のダイヤを使っていると食い違う。
        比べられる列車（行先があり、ダイヤもある走行中の列車）がいなければ None。
        """
        checked = False
        for train in self.active_trains.values():
            if train.active_destination is None or not train.has_dia:
                continue
            final = next(stop.station for stop in train.route_stations if stop.is_final)
            if final != train.active_destination:
                return False
            checked = True
        return True if checked else None

    def apply_timetable(self, starttime_list: startTimeList) -> None:
        """
        ダイヤを反映する。ダイヤの日付は self.date（apply_positions で決まる営業日）とする。
        臨時列車は走行中のものだけ登録するため、apply_positions の後に呼ぶ。

        毎回全列車を登録し直すと重いため、変わっていなければ作り直さない。
        - 全部作り直す：新しいダイヤ（starttime_list が最後に全登録したものと別のオブジェクト）、
          営業日が最後に全登録したときと違う（時刻は営業日から作るため。前の日の列車は apply_positions が消している）
        - 1本だけ登録する：それ以外のとき、apply_positions で初めて現れた列車（臨時列車が走り出したときなど）だけ
        - 何もしない：ほかの列車。停車駅・編成・プレミアムカーは、ダイヤの全登録から変わっていない。
          走り終えた列車と、また走り出した列車は、apply_positions が停車駅などを引き継ぐ
        """
        if self.date is None:
            raise ValueError("営業日が決まっていません。先に apply_positions を呼ぶか、KHState(date) で渡してください。")
        self.starttime_list = starttime_list

        if (self._always_full_timetable
                or starttime_list is not self._registered_timetable
                or self.date != self._registered_date):
            # startTimeListから全列車を登録
            self._timetable_by_wdf = {train.wdfBlockNo: train for train in starttime_list.TrainInfo}
            for train in starttime_list.TrainInfo:
                self._register_train(train)
            self._registered_timetable = starttime_list
            self._registered_date = self.date
            self._rebuild_index()
        else:
            for wdf in sorted(self._unregistered_wdfs):
                train = self._timetable_by_wdf.get(wdf)
                if train is not None and wdf in self._trains:
                    self._index_remove(wdf)
                    self._register_train(train)
                    self._index_add(self._trains[wdf])
        self._unregistered_wdfs.clear()

    def _register_train(self, train: TrainInfo) -> None:
        """ダイヤの1本分を、self._trains の列車に登録する（列車が無ければ作る）。索引は触らない"""
        from .tracker import TrainData, StopStationData
        assert self.date is not None
        # 臨時列車の場合
        if train.extTrain:
            # ActiveTrainDataがある場合、当日の便で確定するので登録
            if train.wdfBlockNo in self._trains.keys():
                pass
            # 当日の便でない可能性があるため登録スキップ
            else:
                return

        wdf = train.wdfBlockNo
        if not wdf in self._trains:
            self._trains[wdf] = TrainData(
                master=self,
                wdf=wdf,
                has_premiumcar=bool(train.premiumCar),
                train_formation=int(train.trainCar),
                date=self.date
            )

        self._trains[wdf].train_formation = int(train.trainCar)
        self._trains[wdf].has_premiumcar = bool(train.premiumCar)

        # ダイヤ登録
        self._trains[wdf].route_stations = []
        for stop_station in (train.diaStationInfoObjects):
            # 3桁の上２桁が駅番号。ほかの形は見たことがないので、仕様の変更に気付けるよう警告する
            if len(stop_station.stationNumber) != 3:
                warnings.warn(f"[WDF{wdf}] 駅番号の形式を読めないため、この駅を飛ばしました: {stop_station.stationNumber!r}")
                continue
            station = self._stations.get(int(stop_station.stationNumber[0:2]))

            # 未登録の駅ならスキップ（寝屋川信号場など）
            if station == None:
                continue

            # もし出発駅なら-
            if stop_station.stationDepTime == "-":
                self._trains[wdf].route_stations.append(
                    StopStationData(
                        is_start = True,
                        is_final=False,
                        is_stop = True,
                        station = station,
                        time = None,
                        train = self._trains[wdf],
                        )
                    )
                continue

            # 3桁目が0なら通過、1・2なら停車
            # 99:99 は通過のほか、停車するが時刻不明の場合もある（ダイヤ乱れ時の運転整理など）
            is_stop:bool = stop_station.stationNumber[2] != "0"
            ltime = tuple(map(int,stop_station.stationDepTime.split(":"))) #22:30 -> (22,30)
            if ltime == (99,99):
                time = None
            else:
                time = datetime.datetime.combine(self.date, datetime.time.min) + datetime.timedelta(hours=ltime[0],minutes=ltime[1])
                time = time.replace(tzinfo=JST)
            self._trains[wdf].route_stations.append(
                    StopStationData(
                        is_start = False,
                        is_stop = is_stop,
                        is_final = False,
                        station = station,
                        time = time,
                        train = self._trains[wdf],
                        )
                    )

        # まれに始発駅に時刻が登録される場合あり
        # https://web.archive.org/web/20260124090959/https://www.keihan.co.jp/zaisen-up/startTimeList.json?6f4653a2-5a0a-4a65-810d-f1917026b14b
        if len([stop for stop in self._trains[wdf].route_stations if stop.is_start]) == 0:
            start_station = min(self._trains[wdf].route_stations, key=lambda x:x.time or datetime.datetime.max.replace(tzinfo=JST))
            start_station.is_start = True

        # 終着駅（ダイヤの並び順は走行順。時刻不明の停車駅もあるため、時刻ではなく並び順で最後の停車駅とする）
        final_station = [stop for stop in self._trains[wdf].route_stations if stop.is_stop][-1]
        final_station.is_final = True

    @overload
    def find_trains(
            self,
            active:         Literal[True],
            train_type:     Optional[TrainType] = None,
            direction:      Optional[Literal["up","down"]] = None,
            is_special:     Optional[bool] = None,
            train_number:   Optional[str] = None,
            has_premiumcar: Optional[bool] = None,
            destination:    Optional[StationData] = None,
            min_delay:      Optional[int] = None,
            max_delay:      Optional[int] = None,
            status:         Optional[TrainStatus] = None,
            line:           Optional[LineLiteral] = None,
            current_or_next_stop: Optional[StationData] = None,
            ) -> list[ActiveTrainData]: ...
    @overload
    def find_trains(
            self,
            active:         Optional[bool] = None,
            train_type:     Optional[TrainType] = None,
            direction:      Optional[Literal["up","down"]] = None,
            is_special:     Optional[bool] = None,
            train_number:   Optional[str] = None,
            has_premiumcar: Optional[bool] = None,
            destination:    Optional[StationData] = None,
            min_delay:      Optional[int] = None,
            max_delay:      Optional[int] = None,
            status:         Optional[TrainStatus] = None,
            line:           Optional[LineLiteral] = None,
            current_or_next_stop: Optional[StationData] = None,
            ) -> list[TrainData]: ...
    def find_trains(
            self,
            active:         Optional[bool] = None,
            train_type:     Optional[TrainType] = None,
            direction:      Optional[Literal["up","down"]] = None,
            is_special:     Optional[bool] = None,
            train_number:   Optional[str] = None,
            has_premiumcar: Optional[bool] = None,
            destination:    Optional[StationData] = None,
            min_delay:      Optional[int] = None,
            max_delay:      Optional[int] = None,
            status:         Optional[TrainStatus] = None,
            line:           Optional[LineLiteral] = None,
            current_or_next_stop: Optional[StationData] = None,
            ) -> list[TrainData]:
        """
        条件に合致する列車を検索してリストで返します。
        - active: 走行中か。True を渡すと戻り値の型が list[ActiveTrainData] になる
        - type: 列車種別（例: "普通"）
        - direction: 上り線（京都方面）か下り線（大阪方面）か（"up" | "down"）。予定の列車は始発・終着から推定した方向
        - is_special: 臨時列車かどうか
        - train_number: 列車番号（例: "2201"）
        - has_premiumcar: プレミアムカー付きかどうか
        - destination: 行き先駅
        - status: 停車中・通過中・駅間・不明（TrainStatus）
        - current_or_next_stop: 停車中の駅 or 次の停車駅
        - line: 路線。走行中の列車は今いる路線、走行中でない列車は TrainData.line（走ったことがあればそのときの路線、なければ停車駅から推定した路線）
        is_special・train_number・current_or_next_stop・status は走行中の列車だけが持つ値のため、
        指定すると走行中でない列車は当てはまりません。
        """
        from .tracker import ActiveTrainData
        trains: list[TrainData|ActiveTrainData] = []
        for train in self._trains.values():
            # 条件に合致するかチェック
            if active is not None       and isinstance(train, ActiveTrainData) != active:
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
            if direction is not None    and train.direction != direction:
                continue
            if line is not None         and train.line != line:
                continue

            # 以下は走行中の列車だけが持つ値。指定されたら、走行中でない列車は当てはまらない
            active_only = (current_or_next_stop, is_special, train_number, status)
            if not isinstance(train, ActiveTrainData):
                if any(v is not None for v in active_only):
                    continue
            else:
                if current_or_next_stop is not None and train.current_or_next_stop != current_or_next_stop:
                    continue
                if is_special is not None and train.is_special != is_special:
                    continue
                if train_number is not None and train.train_number != train_number:
                    continue
                if status is not None and train.status is not status:
                    continue

            trains.append(train)
        return trains

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
        from .tracker import ActiveTrainData
        # active=True なので全部 ActiveTrainData。isinstance は型を絞るため
        return [
            train for train in self.find_trains(active=True, line=line)
            if isinstance(train, ActiveTrainData) and train.is_delay_growing(window, ratio)
        ]

    @property
    def max_delay_train(self) -> Optional[ActiveTrainData]:
        """現在もっとも遅延している運行中の列車"""
        from .tracker import ActiveTrainData
        most_delay_train = max(
            self.active_trains.values(),
            key=lambda x:x.delay_minutes if isinstance(x, ActiveTrainData) else 0,
            default=None
            )
        if most_delay_train:
            if most_delay_train.delay_minutes == 0:
                return None
        return most_delay_train

    @property
    def max_delay_minutes(self) -> int:
        """現在の最大遅延分数"""
        return self.max_delay_train.delay_minutes if self.max_delay_train else 0
