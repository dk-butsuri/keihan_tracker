"""
京阪電車リアルタイム列車位置情報APIのデータを、Pythonで安全かつ型安全に扱うためのPydanticモデル群。

【特徴】
・APIから取得したJSONをバリデート（型検証）することが主目的。
・API仕様に忠実で、余計な変換やロジックを加えず「生に近い」データ構造を維持。
※臨時列車判定は例外的に実装。

【バリデートできるJSON APIと内容】
------------------------------------------------------------

1. 駅ごとの乗り換え情報
   URL: https://www.keihan.co.jp/zaisen/transferGuideInfo.json
   内容: 各駅で接続している他路線（京阪・地下鉄・モノレール等）の情報。駅番号をキーに、接続路線名（多言語対応）などが格納されている。
   対応クラス: TransferGuideInfo > StationConnections > MultiLang_Lines


2. 路線ごとの駅名データ
   URL: https://www.keihan.co.jp/zaisen/select_station.json
   内容: 路線ごとに、駅番号・駅名（多言語対応）などをまとめたデータ。路線名をキーに、各駅の情報が格納されている。
   対応クラス: SelectStation > LineDetail > MultiLang


3. 列車のリアルタイム走行位置
   URL: https://www.keihan.co.jp/zaisen-up/trainPositionList.json
   内容: 現在運行中の列車の位置・種別・遅延情報など。各列車の現在地や行先、種別、遅延分数などが含まれる。
   対応クラス: trainPositionList > LocationObject > trainInfoObject


4. 列車ごとのダイヤ（停車駅・時刻表）
   URL: https://www.keihan.co.jp/zaisen-up/startTimeList.json
   内容: 各列車の停車駅・発車時刻などのダイヤ情報。列車ごとに、どの駅に何時何分に停車するか等が記載されている。
   対応クラス: startTimeList > TrainInfo > diaStationInfoObject
"""

from pydantic import BaseModel, RootModel, Field, model_validator, field_validator
from typing import Optional, Literal
from enum import Enum
import datetime


# 列車種別をEnumで定義
class TrainType(str, Enum):
    LOCAL = "普通"
    SEMI_EXP = "区間急行"
    SUB_EXP = "準急"
    COMMUTER_SEMI_EXP = "通勤準急"
    EXPRESS = "急行"
    COMMUTER_EXP = "通勤急行"
    MIDNIGHT_EXP = "深夜急行"
    RAPID_EXP = "快速急行"
    COMMUTER_RAPID_EXP = "通勤快急"
    LTD_EXP = "特急"
    LINER = "ライナー"
    RAPID_LTD_EXP = "快速特急 洛楽"
    EXTRA_TRAIN = "臨時列車"  # 臨時列車は臨時特急や臨時休校などとは別

LineLiteral = Literal["京阪本線・鴨東線","中之島線","交野線","宇治線"]
    
# 1. 言語別の路線名リストを表現するモデル
class MultiLang_Lines(BaseModel):
    """
    各言語での路線名を保持するモデル。
    """
    ja: list[str] = Field(description="日本語での名称")
    en: list[str] = Field(description="英語での名称")
    cn: list[str] = Field(description="中国語（簡体字）での名称")
    tw: list[str] = Field(description="中国語（繁体字）での名称")
    kr: list[str] = Field(description="韓国語での名称")

# 1. 言語名称モデル
class MultiLang(BaseModel):
    """
    各言語での名称を保持するモデル。
    """
    ja: str = Field(description="日本語での名称")
    en: str = Field(description="英語での名称")
    cn: str = Field(description="中国語（簡体字）での名称")
    tw: str = Field(description="中国語（繁体字）での名称")
    kr: str = Field(description="韓国語での名称")

# 2. 交通手段ごとの接続情報を表現するモデル
class StationConnections(BaseModel):
    """
    駅で接続する交通手段（電車、地下鉄、モノレール）とその路線名を保持するモデル。
    各交通手段は存在しない場合があるため、Optionalとして定義します。
    """
    train:      Optional[MultiLang_Lines] = None
    subway:     Optional[MultiLang_Lines] = None
    monorail:   Optional[MultiLang_Lines] = None

### 3. JSONのパーサー
class TransferGuideInfo(RootModel[dict[str, StationConnections]]):
    pass

# 1. 駅名データ 
class LineDetail(BaseModel):
    lineName: MultiLang
    stations: dict[str, MultiLang]

### 2. JSONのパーサー
class SelectStation(
    RootModel[
        dict[
            LineLiteral,
            LineDetail
        ]
            ]):
    pass

# 1. trainInfoObjectsの要素を表現するモデル

class trainInfoObject(BaseModel):
    """
    個々の電車の詳細情報を保持するモデル。
    """
    wdfBlockNo:         int
    carsOfTrain:        int
    delayMinutes:       str
    delayMinutesEn:     str
    delayMinutesKo:     str
    delayMinutesZhCn:   str
    delayMinutesZhTw:   str
    destStationCode:    int
    destStationNameEn:  str
    destStationNameJp:  str
    destStationNameKo:  str
    destStationNameZhCn:str
    destStationNameZhTw:str
    destStationNumber:  int
    lastPassStation:    int
    trainNumber:        str
    trainTypeEn:        str
    trainTypeIcon:      str
    trainTypeJp:        TrainType
    is_special:         bool
    trainTypeKo:        str
    trainTypeZhCn:      str
    trainTypeZhTw:      str

    @model_validator(mode="before")
    @classmethod
    def check_type_special(cls, d:dict):
        if "臨時" in d["trainTypeJp"]:
            d["is_special"] = True
            d["trainTypeJp"] = d["trainTypeJp"].replace("臨時　　　　", "")
        else:
            d["is_special"] = False
        return d

# 2. locationObjectsの要素を表現するモデル
class LocationObject(BaseModel):
    """
    路線図上での電車の位置と基本情報を保持するモデル。
    """
    delay: str
    delayEn: str
    delayKo: str
    delayZhCn: str
    delayZhTw: str
    locationCol: int
    locationRow: int
    trainDirection: int
    trainIconTypeImageJp: str
    trainInfoObjects: list[trainInfoObject]
    trainTypeVisIconVis: str

### 3. JSONのパーサー
class trainPositionList(BaseModel):
    fileCreatedTime: datetime.datetime
    fileVersion: str
    linkNum: str
    locationObjects: list[LocationObject]

    @field_validator("fileCreatedTime", mode="before")
    def validate_time(cls,value) -> datetime.datetime:
        return datetime.datetime.strptime(value,"%Y%m%d%H%M%S")

# 1.
class diaStationInfoObject(BaseModel):
    stationNumber:  str = Field(description="駅ナンバリング2桁+ホーム番号1桁 ")
    stationDepTime: str = Field(description="00:00の形式。深夜は25時などで表す。出発駅の場合は-（ハイフン）、不明な場合は99:99。")
    stationNameJp:  str
    stationNameEn:  str
    stationNameZhTw:str
    stationNameZhCn:str
    stationNameKo:  str

class TrainInfo(BaseModel):
    wdfBlockNo:int
    extTrain:bool
    premiumCar:int = Field(description="プレミアムカーがあるかどうか",)
    trainCar:str = Field(description="車両番号")
    diaStationInfoObjects:list[diaStationInfoObject]

### 3. JSONのパーサー
class startTimeList(BaseModel):
    fileCreatedTime:  datetime.datetime
    fileVersion:      str
    TrainInfo:        list[TrainInfo]

    @field_validator("fileCreatedTime", mode="before")
    def validate_time(cls,value) -> datetime.datetime:
        return datetime.datetime.strptime(value,"%Y%m%d%H%M%S")