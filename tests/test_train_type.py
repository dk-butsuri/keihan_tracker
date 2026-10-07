"""
停車駅からの種別推定（TrainData.train_type）のテスト。

fixtureの1日分のダイヤ（平日・土休日）と過去のダイヤに含まれる停車パターンをすべて列挙し、期待する種別と照合する。
また、京阪公式の停車駅案内図を OFFICIAL_STOPS として書き写し、推定種別が案内図と矛盾しないことを確かめる。
ダイヤ改正などで未知の停車パターンが現れたら test_all_fixture_patterns_are_known が落ちるので、
そのパターンの正しい種別を確認して STOP_PATTERNS に追加すること。
"""

from typing import Optional

import pytest

from keihan_tracker import ActiveTrainData, KHTracker, TrainData, TrainType

from conftest import MockKeihanAPI, load_fixture_bytes, make_position_list, make_tracker, run

POSITION_PATH = "/zaisen-up/trainPositionList.json"
STARTTIME_PATH = "/zaisen-up/startTimeList.json"

# (停車駅番号を停車順に "-" でつないだもの, 期待する種別)
# 2026-10-06 のダイヤ（tests/fixtures/startTimeList.json.gz）から抽出
# ライナーは停車駅から区別できないため、特急または快速急行として扱う
STOP_PATTERNS: list[tuple[str, TrainType]] = [
    # 普通
    ("1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16", TrainType.LOCAL),
    ("1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21-22-23-24", TrainType.LOCAL),
    ("1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40-41-42", TrainType.LOCAL),
    ("16-15-14-13-12-11-10-9-8-7-6-5-4-3-2-1", TrainType.LOCAL),
    ("16-15-14-13-12-11-10-9-8-7-6-5-4-3-51-52-53-54", TrainType.LOCAL),
    ("17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40-41-42", TrainType.LOCAL),
    ("21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-2-1", TrainType.LOCAL),
    ("21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-51-52-53-54", TrainType.LOCAL),
    ("21-61-62-63-64-65-66-67", TrainType.LOCAL),
    ("24-23-22-21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-2-1", TrainType.LOCAL),
    ("24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40-41-42", TrainType.LOCAL),
    ("27-26-25-24-23-22-21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-51-52-53-54", TrainType.LOCAL),
    ("27-28-29-30-31-32-33-34-35-36-37-38-39-40", TrainType.LOCAL),
    ("27-28-29-30-31-32-33-34-35-36-37-38-39-40-41-42", TrainType.LOCAL),
    ("28-71-72-73-74-75-76-77", TrainType.LOCAL),
    ("40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24", TrainType.LOCAL),
    ("40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-51-52-53-54", TrainType.LOCAL),
    ("40-41-42", TrainType.LOCAL),
    ("42-41-40", TrainType.LOCAL),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27", TrainType.LOCAL),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24", TrainType.LOCAL),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21", TrainType.LOCAL),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17", TrainType.LOCAL),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-15-14-13-12-11", TrainType.LOCAL),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-2-1", TrainType.LOCAL),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-51-52-53-54", TrainType.LOCAL),
    ("54-53-52-51-3-4-5-6-7-8-9-10-11-12-13-14-15-16", TrainType.LOCAL),
    ("54-53-52-51-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21", TrainType.LOCAL),
    ("54-53-52-51-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21-22-23-24", TrainType.LOCAL),
    ("54-53-52-51-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21-22-23-24-25-26-27", TrainType.LOCAL),
    ("54-53-52-51-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40", TrainType.LOCAL),
    ("54-53-52-51-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40-41-42", TrainType.LOCAL),
    ("67-66-65-64-63-62-61-21", TrainType.LOCAL),
    ("77-76-75-74-73-72-71-28", TrainType.LOCAL),
    # 区間急行
    ("1-2-3-4-11-12-13-14-15-16", TrainType.SEMI_EXP),
    ("1-2-3-4-11-12-13-14-15-16-17-18-19-20-21", TrainType.SEMI_EXP),
    ("1-2-3-4-11-12-13-14-15-16-17-18-19-20-21-22-23-24", TrainType.SEMI_EXP),
    ("16-15-14-13-12-11-4-3-2-1", TrainType.SEMI_EXP),
    ("16-15-14-13-12-11-4-3-51-52-53-54", TrainType.SEMI_EXP),
    ("21-20-19-18-17-16-15-14-13-12-11-4-3-2-1", TrainType.SEMI_EXP),
    ("21-20-19-18-17-16-15-14-13-12-11-4-3-51-52-53-54", TrainType.SEMI_EXP),
    ("24-23-22-21-20-19-18-17-16-15-14-13-12-11-4-3-2-1", TrainType.SEMI_EXP),
    ("54-53-52-51-3-4-11-12-13-14-15-16", TrainType.SEMI_EXP),
    ("54-53-52-51-3-4-11-12-13-14-15-16-17-18", TrainType.SEMI_EXP),
    ("54-53-52-51-3-4-11-12-13-14-15-16-17-18-19-20-21", TrainType.SEMI_EXP),
    # 準急
    ("1-2-3-4-11-16-17-18-19-20-21", TrainType.SUB_EXP),
    ("1-2-3-4-11-16-17-18-19-20-21-22-23-24", TrainType.SUB_EXP),
    ("1-2-3-4-11-16-17-18-19-20-21-22-23-24-25-26-27", TrainType.SUB_EXP),
    ("1-2-3-4-11-16-17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40", TrainType.SUB_EXP),
    ("1-2-3-4-11-16-17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40-41-42", TrainType.SUB_EXP),
    ("21-20-19-18-17-16-11-4-3-2-1", TrainType.SUB_EXP),
    ("24-23-22-21-20-19-18-17-16-11-4-3-2-1", TrainType.SUB_EXP),
    ("24-23-22-21-20-19-18-17-16-11-4-3-51-52-53-54", TrainType.SUB_EXP),
    ("27-26-25-24-23-22-21-20-19-18-17-16-11-4-3-2-1", TrainType.SUB_EXP),
    ("40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-11-4-3-2-1", TrainType.SUB_EXP),
    ("40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-11-4-3-51-52-53-54", TrainType.SUB_EXP),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-11-4-3-2-1", TrainType.SUB_EXP),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-11-4-3-51-52-53-54", TrainType.SUB_EXP),
    ("54-53-52-51-3-4-11-16-17-18-19-20-21", TrainType.SUB_EXP),
    ("54-53-52-51-3-4-11-16-17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40-41-42", TrainType.SUB_EXP),
    # 通勤準急
    ("21-20-19-18-17-16-4-3-2-1", TrainType.COMMUTER_SUB_EXP),
    ("21-20-19-18-17-16-4-3-51-52-53-54", TrainType.COMMUTER_SUB_EXP),
    ("24-23-22-21-20-19-18-17-16-4-3-2-1", TrainType.COMMUTER_SUB_EXP),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-4-3-2-1", TrainType.COMMUTER_SUB_EXP),
    # 急行
    ("1-2-3-4-11-17-18-20-21", TrainType.EXPRESS),
    ("1-2-3-4-11-17-18-20-21-24-26-28-30-34-37-38-39-40-41-42", TrainType.EXPRESS),
    ("17-18-20-21-24-26-28-30-34-37-38-39-40-41-42", TrainType.EXPRESS),
    ("24-21-20-18-17-11-4-3-2-1", TrainType.EXPRESS),
    ("27-26-24-21-20-18-17-11-4-3-2-1", TrainType.EXPRESS),
    ("27-28-30-34-37-38-39-40-41-42", TrainType.EXPRESS),
    ("40-39-38-37-34-30-28-27", TrainType.EXPRESS),
    ("42-41-40-39-38-37-34-30-28-26-24", TrainType.EXPRESS),
    ("42-41-40-39-38-37-34-30-28-26-24-21", TrainType.EXPRESS),
    ("42-41-40-39-38-37-34-30-28-26-24-21-20-18-17", TrainType.EXPRESS),
    ("42-41-40-39-38-37-34-30-28-26-24-21-20-18-17-11-4-3-2-1", TrainType.EXPRESS),
    ("42-41-40-39-38-37-34-30-28-27", TrainType.EXPRESS),
    # 快速急行
    ("1-2-3-4-11-17-18-21", TrainType.RAPID_EXP),
    ("1-2-3-4-11-17-18-21-24", TrainType.RAPID_EXP),
    ("1-2-3-4-11-17-18-21-24-28-30-37-39-40-42", TrainType.RAPID_EXP),
    ("21-18-17-11-4-3-2-1", TrainType.RAPID_EXP),
    ("24-21-18-17-11-4-3-2-1", TrainType.RAPID_EXP),
    ("24-21-18-17-11-4-3-51-52-53-54", TrainType.RAPID_EXP),
    ("42-40-39-37-30-28-24-21-18-17-11-4-3-2-1", TrainType.RAPID_EXP),
    ("54-53-52-51-3-4-11-17-18-21", TrainType.RAPID_EXP),
    # 通勤快急
    ("21-18-17-4-3-2-1", TrainType.COMMUTER_RAPID_EXP),
    ("24-21-18-17-4-3-2-1", TrainType.COMMUTER_RAPID_EXP),
    ("42-40-39-37-30-28-24-21-18-17-4-3-2-1", TrainType.COMMUTER_RAPID_EXP),
    ("42-40-39-37-30-28-24-21-18-17-4-3-51-52-53-54", TrainType.COMMUTER_RAPID_EXP),
    # 特急
    ("1-2-3-4-21-24-28-30-37-39-40", TrainType.LTD_EXP),
    ("1-2-3-4-21-24-28-30-37-39-40-42", TrainType.LTD_EXP),
    ("21-4-3-2-1", TrainType.LTD_EXP),
    ("24-21-4-3-2-1", TrainType.LTD_EXP),
    ("40-39-37-30-28-24-21-4-3-2-1", TrainType.LTD_EXP),
    ("42-40-39-37-30-28-24-21-4-3-2-1", TrainType.LTD_EXP),
    # 快速特急 洛楽
    ("1-2-3-4-37-39-40-42", TrainType.RAPID_LTD_EXP),
    ("42-40-39-37-4-3-2-1", TrainType.RAPID_LTD_EXP),
]

# 過去のダイヤにだけある停車パターン
# Wayback Machine に保存された startTimeList.json（2022-11～2026-06）から抽出
# 臨時列車やデータの異常と思われるもの（北浜を通過する普通など）は除外
PAST_STOP_PATTERNS: list[tuple[str, TrainType]] = [
    # 普通
    ("1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21", TrainType.LOCAL),
    ("1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18-19-20-21-22-23-24-25-26-27-28-29-30-31-32-33-34-35-36-37-38-39-40", TrainType.LOCAL),
    ("16-15-14-13-12-11-10-9-8-7-6-5-4-3", TrainType.LOCAL),
    ("24-23-22-21-20-19-18-17-16-15-14-13-12-11-10-9-8-7-6-5-4-3-51-52-53-54", TrainType.LOCAL),
    # 区間急行
    ("54-53-52-51-3-4-11-12-13-14-15-16-17-18-19-20-21-22-23-24", TrainType.SEMI_EXP),
    # 準急
    ("1-2-3-4-11-16", TrainType.SUB_EXP),
    ("21-20-19-18-17-16-11-4-3-51-52-53-54", TrainType.SUB_EXP),
    ("54-53-52-51-3-4-11-16-17-18-19-20-21-22-23-24", TrainType.SUB_EXP),
    # 通勤準急
    ("24-23-22-21-20-19-18-17-16-4-3-51-52-53-54", TrainType.COMMUTER_SUB_EXP),
    ("42-41-40-39-38-37-36-35-34-33-32-31-30-29-28-27-26-25-24-23-22-21-20-19-18-17-16-4-3-51-52-53-54", TrainType.COMMUTER_SUB_EXP),
    # 急行
    ("1-2-3-4-11-17-18-20-21-24", TrainType.EXPRESS),
    ("1-2-3-4-11-17-18-20-21-24-26-27", TrainType.EXPRESS),
    ("1-2-3-4-11-17-18-20-21-24-26-27-28-30-34-37-38-39-40-41-42", TrainType.EXPRESS),  # 淀に臨時停車
    ("21-20-18-17-11-4-3-2-1", TrainType.EXPRESS),
    ("42-41-40-39-38-37-34-30-28-27-26-24-21-20-18-17-11-4-3-2-1", TrainType.EXPRESS),  # 淀に臨時停車
    # 快速急行
    ("1-2-3-4-11-17-18-21-24-27-28-30-37-39-40-42", TrainType.RAPID_EXP),  # 淀に臨時停車
    ("17-18-21-24-28-30-37-39-40-42", TrainType.RAPID_EXP),                # 寝屋川市発出町柳行き
    ("21-18-17-11-4-3-51-52-53-54", TrainType.RAPID_EXP),
    ("42-40-39-37-30-28-27-24-21-18-17-11-4-3-2-1", TrainType.RAPID_EXP),  # 淀に臨時停車
    ("54-53-52-51-3-4-11-17-18-21-24", TrainType.RAPID_EXP),
    ("54-53-52-51-3-4-11-17-18-21-24-28-30-37-39-40-42", TrainType.RAPID_EXP),
    # 通勤快急
    ("24-21-18-17-4-3-51-52-53-54", TrainType.COMMUTER_RAPID_EXP),
    # 特急
    ("42-40-39-37-30-28-24-21", TrainType.LTD_EXP),
]

ALL_STOP_PATTERNS = STOP_PATTERNS + PAST_STOP_PATTERNS


# ---------------------------------------------------------------- 公式の停車駅案内図

# 列車が走る経路（駅番号の並び）
MAIN_LINE = list(range(1, 43))                         # 淀屋橋～出町柳
NAKANOSHIMA_LINE = [54, 53, 52, 51] + list(range(3, 43))  # 中之島～天満橋～出町柳
KATANO_LINE = [21] + list(range(61, 68))               # 枚方市～私市
UJI_LINE = [28] + list(range(71, 78))                  # 中書島～宇治

_YODOYABASHI = {1, 2, 3, 4}
_NAKANOSHIMA = {54, 53, 52, 51, 3, 4}

# 種別ごとの停車駅（2026年10月 京阪公式の停車駅案内図より）
# 始発駅・終着駅は種別に関係なく停車扱い（例：急行は淀始発・淀ゆきのみ淀に停車）
OFFICIAL_STOPS: dict[TrainType, set[int]] = {
    TrainType.LOCAL: set(MAIN_LINE) | set(NAKANOSHIMA_LINE) | set(KATANO_LINE) | set(UJI_LINE),
    TrainType.SEMI_EXP: _YODOYABASHI | _NAKANOSHIMA | set(range(11, 25)),
    TrainType.SUB_EXP: _YODOYABASHI | _NAKANOSHIMA | {11} | set(range(16, 43)),
    TrainType.COMMUTER_SUB_EXP: _YODOYABASHI | _NAKANOSHIMA | set(range(16, 43)),  # 守口市は通過
    TrainType.EXPRESS: _YODOYABASHI | {11, 17, 18, 20, 21, 24, 26, 28, 30, 34, 37, 38, 39, 40, 41, 42},
    TrainType.RAPID_EXP: _YODOYABASHI | _NAKANOSHIMA | {11, 17, 18, 21, 24, 28, 30, 37, 39, 40, 42},
    TrainType.COMMUTER_RAPID_EXP: _YODOYABASHI | _NAKANOSHIMA | {17, 18, 21, 24, 28, 30, 37, 39, 40, 42},  # 守口市は通過
    TrainType.LTD_EXP: _YODOYABASHI | {21, 24, 28, 30, 37, 39, 40, 42},
    TrainType.RAPID_LTD_EXP: _YODOYABASHI | {37, 39, 40, 42},
}


def _route(stops: list[int]) -> list[int]:
    for line in (NAKANOSHIMA_LINE, KATANO_LINE, UJI_LINE):
        if set(stops) - set(MAIN_LINE) <= set(line) - set(MAIN_LINE) and set(stops) - set(MAIN_LINE):
            return line
    return MAIN_LINE


def _official_stops(train_type: TrainType, start: int, dest: int, route: list[int]) -> Optional[list[int]]:
    """start から dest まで走る train_type の列車が停車するはずの駅（停車順）"""
    if start not in route or dest not in route:
        return None
    i, j = route.index(start), route.index(dest)
    section = route[i:j + 1] if i < j else route[j:i + 1][::-1]
    if train_type == TrainType.LINER:
        # 特急停車駅と同じ。ただし樟葉始発は香里園・寝屋川市にも停車
        stops = OFFICIAL_STOPS[TrainType.LTD_EXP] | ({18, 17} if start == 24 else set())
    else:
        stops = OFFICIAL_STOPS[train_type]
    return [n for n in section if n in stops or n in (start, dest)]


# 京都競馬場の開催日などに淀へ臨時停車することがある種別（案内図には無い）
YODO_EXTRA_STOP_TYPES = {TrainType.EXPRESS, TrainType.RAPID_EXP}


def official_train_types(stops: list[int]) -> set[TrainType]:
    """停車駅の並びが、停車駅案内図のどの種別と一致するか（区間運転を考慮）"""
    route = _route(stops)
    without_yodo = [n for n in stops if n != 27 or n in (stops[0], stops[-1])]
    return {
        t for t in [*OFFICIAL_STOPS, TrainType.LINER]
        if _official_stops(t, stops[0], stops[-1], route) == stops
        or (t in YODO_EXTRA_STOP_TYPES and _official_stops(t, stops[0], stops[-1], route) == without_yodo)
    }


def _stops(pattern: str) -> list[int]:
    return [int(n) for n in pattern.split("-")]


def _dia(station_number: int, dep_time: str) -> dict:
    return {
        "stationNumber": f"{station_number:02}1",
        "stationDepTime": dep_time,
        "stationNameJp": "",
        "stationNameEn": "",
        "stationNameZhTw": "",
        "stationNameZhCn": "",
        "stationNameKo": "",
    }


def _train_info(wdf: int, pattern: str) -> dict:
    dias = [_dia(n, "-" if i == 0 else f"12:{i:02}") for i, n in enumerate(_stops(pattern))]
    return {
        "wdfBlockNo": str(wdf),
        "extTrain": "0",
        "premiumCar": "0",
        "trainCar": "07003",
        "diaStationInfoObjects": dias,
    }


@pytest.fixture(scope="module")
def pattern_tracker() -> KHTracker:
    """ALL_STOP_PATTERNS の停車パターンを1列車ずつ（wdfBlockNo = 添字+1）投入した KHTracker"""
    api = MockKeihanAPI()
    api.set_json(POSITION_PATH, make_position_list("20261006120000"))
    api.set_json(STARTTIME_PATH, {
        "fileCreatedTime": "20261006112206",
        "fileVersion": "1.0.0",
        "TrainInfo": [_train_info(i + 1, pattern) for i, (pattern, _) in enumerate(ALL_STOP_PATTERNS)],
    })
    tracker = make_tracker(api)
    run(tracker.fetch_pos())
    return tracker


@pytest.fixture(scope="module")
def holiday_tracker() -> KHTracker:
    """土休日ダイヤ（2026-06-14 日曜、Wayback Machine より）を読み込んだ KHTracker"""
    api = MockKeihanAPI()
    api.responses[STARTTIME_PATH] = load_fixture_bytes("startTimeList_holiday.json")
    api.set_json(POSITION_PATH, make_position_list("20260614133525"))
    tracker = make_tracker(api)
    run(tracker.fetch_pos())
    return tracker


@pytest.mark.parametrize(("tracker_name", "expected"), [
    ("tracker", 2),          # 平日
    ("holiday_tracker", 8),  # 土休日
])
def test_rapid_ltd_exp_count(request, tracker_name, expected):
    """快速特急 洛楽の本数（fixtureのダイヤ時点で平日2本、土休日8本。ダイヤ改正で fixture を更新したら見直すこと）"""
    tracker = request.getfixturevalue(tracker_name)
    trains = [t for t in tracker.trains.values() if t.train_type == TrainType.RAPID_LTD_EXP]
    assert len(trains) == expected


def test_holiday_patterns_are_known(holiday_tracker):
    """土休日ダイヤに、ALL_STOP_PATTERNS に無い停車パターンが含まれていないこと"""
    known = {pattern for pattern, _ in ALL_STOP_PATTERNS}
    unknown = {}  # 停車パターン: 例となる wdfBlockNo
    for train in holiday_tracker.trains.values():
        pattern = "-".join(str(s.station.station_number) for s in train.stop_stations)
        if pattern not in known:
            unknown.setdefault(pattern, train.wdfBlockNo)
    assert not unknown, f"未知の停車パターン {{停車駅: wdfBlockNo}}: {unknown}"


def test_stop_patterns_unique():
    patterns = [pattern for pattern, _ in ALL_STOP_PATTERNS]
    assert len(patterns) == len(set(patterns))


@pytest.mark.parametrize(
    ("index", "pattern", "expected"),
    [(i, pattern, expected) for i, (pattern, expected) in enumerate(ALL_STOP_PATTERNS)],
    ids=[f"{expected.name}:{pattern}" for pattern, expected in ALL_STOP_PATTERNS],
)
def test_train_type_from_stop_pattern(pattern_tracker, index, pattern, expected):
    train = pattern_tracker.trains[index + 1]
    assert [s.station.station_number for s in train.stop_stations] == _stops(pattern)
    assert train.train_type == expected


def test_all_fixture_patterns_are_known(tracker):
    """fixtureのダイヤに、STOP_PATTERNS に無い停車パターンが含まれていないこと"""
    known = {pattern for pattern, _ in STOP_PATTERNS}
    unknown = {}  # 停車パターン: 例となる wdfBlockNo
    for train in tracker.trains.values():
        pattern = "-".join(str(s.station.station_number) for s in train.stop_stations)
        if pattern not in known:
            unknown.setdefault(pattern, train.wdfBlockNo)
    assert not unknown, f"未知の停車パターン {{停車駅: wdfBlockNo}}: {unknown}"


@pytest.mark.parametrize(
    ("pattern", "expected"),
    ALL_STOP_PATTERNS,
    ids=[f"{expected.name}:{pattern}" for pattern, expected in ALL_STOP_PATTERNS],
)
def test_stop_patterns_follow_official_chart(pattern, expected):
    """ALL_STOP_PATTERNS の期待値が、公式の停車駅案内図と矛盾しないこと"""
    assert expected in official_train_types(_stops(pattern))


@pytest.mark.parametrize("tracker_name", ["tracker", "holiday_tracker"])
def test_fixture_trains_follow_official_chart(request, tracker_name):
    """fixtureの全列車について、停車駅が案内図のいずれかの種別と一致し、推定種別・実際の種別がそれに含まれること"""
    tracker = request.getfixturevalue(tracker_name)
    errors = []
    for train in tracker.trains.values():
        stops = [s.station.station_number for s in train.stop_stations]
        candidates = official_train_types(stops)
        inferred = TrainData(
            master=tracker,
            wdfBlockNo=train.wdfBlockNo,
            date=train.date,
            has_premiumcar=None,
            train_formation=None,
            route_stations=train.route_stations,
        ).train_type
        actual = train.train_type if isinstance(train, ActiveTrainData) else None  # APIの種別
        if not candidates or inferred not in candidates or (actual and actual not in candidates):
            errors.append((train.wdfBlockNo, "-".join(map(str, stops)), inferred.name, actual and actual.name,
                           sorted(t.name for t in candidates)))
    assert not errors, "(wdfBlockNo, 停車駅, 推定, 実際, 案内図上の候補): " + repr(errors)


@pytest.mark.parametrize(("stops", "expected"), [
    ([1, 2, 3, 4, 21, 24, 28, 30, 37, 39, 40, 42], {TrainType.LTD_EXP, TrainType.LINER}),
    ([24, 21, 18, 17, 4, 3, 2, 1], {TrainType.COMMUTER_RAPID_EXP, TrainType.LINER}),  # 樟葉始発ライナー
    ([1, 2, 3, 4, 11, 17, 18, 20, 21, 24, 26, 28, 30, 34, 37, 38, 39, 40, 41, 42], {TrainType.EXPRESS}),
    ([27, 28, 30, 34, 37, 38, 39, 40, 41, 42], {TrainType.EXPRESS}),  # 淀始発
    ([1, 2, 3, 4, 11, 17, 18, 20, 21, 24, 26, 27, 28], {TrainType.EXPRESS}),  # 淀に臨時停車
    ([1, 2, 3, 4, 21, 24, 27, 28, 30, 37, 39, 40, 42], set()),         # 特急は淀に停車しない
    ([17, 18, 21, 24, 28, 30, 37, 39, 40, 42], {TrainType.RAPID_EXP, TrainType.COMMUTER_RAPID_EXP}),
    ([1, 2, 3, 4, 17, 18, 21, 24, 28, 30, 37, 39, 40, 42], {TrainType.COMMUTER_RAPID_EXP}),
    ([54, 53, 52, 51, 3, 4, 21, 24, 28, 30, 37, 39, 40, 42], set()),  # 特急は中之島線に乗り入れない
    ([40, 41, 42], {TrainType.LOCAL, TrainType.EXPRESS, TrainType.SUB_EXP, TrainType.COMMUTER_SUB_EXP}),
])
def test_official_train_types(stops, expected):
    """案内図から停車駅を判定するテスト用ヘルパー自体のテスト"""
    assert official_train_types(stops) == expected
