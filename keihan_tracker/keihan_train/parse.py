"""
公式APIの本文（JSON）を読み込む。通信はしない。
壊れた本文（壊れたJSON・想定外の形式）は KeihanAPIError にする。
"""

from .schemes import TransferGuideInfo, SelectStation, startTimeList, trainPositionList
from pydantic import BaseModel
import warnings
from typing import TypeVar
import json
import re

SELECT_STATION_URL = "https://www.keihan.co.jp/zaisen/select_station.json"
TRANSFER_GUIDE_URL = "https://www.keihan.co.jp/zaisen/transferGuideInfo.json"
START_TIME_LIST_URL = "https://www.keihan.co.jp/zaisen-up/startTimeList.json"
TRAIN_POSITION_URL = "https://www.keihan.co.jp/zaisen-up/trainPositionList.json"


class KeihanAPIError(ValueError):
    """
    公式APIが読み込めないデータ（壊れたJSON・想定外の形式）を返したことを表す例外。
    末尾にNULが付く、本文が途中で切れる、などが実際に起きている。
    列車の状態は読み込めた分まで更新されているので、次の取得で再試行すればよい。
    """


_Model = TypeVar("_Model", bound=BaseModel)


def _parse(model: type[_Model], text: str, url: str) -> _Model:
    try:
        return model.model_validate(json.loads(text))
    except ValueError as e: # json.JSONDecodeError, pydantic.ValidationError
        raise KeihanAPIError(f"{url} を読み込めませんでした: {e}") from e


def parse_select_station(text: str, url: str = SELECT_STATION_URL) -> SelectStation:
    """select_station.json（路線ごとの駅名）を読み込む。url はエラーメッセージに入れる"""
    return _parse(SelectStation, text, url)


def parse_transfer_guide(text: str, url: str = TRANSFER_GUIDE_URL) -> TransferGuideInfo:
    """transferGuideInfo.json（駅ごとの乗り入れ）を読み込む。url はエラーメッセージに入れる"""
    return _parse(TransferGuideInfo, text, url)


def parse_timetable(text: str, url: str = START_TIME_LIST_URL) -> startTimeList:
    """startTimeList.json（列車ごとのダイヤ）を読み込む。url はエラーメッセージに入れる"""
    return _parse(startTimeList, text, url)


def parse_positions(text: str, url: str = TRAIN_POSITION_URL) -> trainPositionList:
    """trainPositionList.json（列車の位置・遅延）を読み込む。url はエラーメッセージに入れる"""
    return _parse(trainPositionList, text, url)


def _parse_delay_minutes(text: str) -> int:
    """
    delayMinutes（"3分遅れ"、"2時間以上"、""）を分に直す。
    120分以上の遅れは "2時間以上" としか来ないので、下限の120分とする。
    見たことのない表記は、取得を止めないよう警告を出して0とする。
    """
    if text == "":
        return 0
    if text == "2時間以上":
        return 120
    if m := re.fullmatch(r"(\d+)分遅れ", text):
        return int(m[1])
    warnings.warn(f"遅延の表記を読めませんでした: {text!r}")
    return 0
