import pytest

from keihan_tracker.keihan_train import stations_map
from keihan_tracker.keihan_train.position_calculation import calc_position

from conftest import load_fixture

HONSEN = "京阪本線・鴨東線"
NAKANOSHIMA = "中之島線"
UJI = "宇治線"
KATANO = "交野線"


@pytest.mark.parametrize(
    ("col", "row", "expected"),
    [
        # 京阪本線: 1ブロック3行、Top行が停車
        (3, 1, (HONSEN, 42, None)),   # 出町柳
        (3, 2, (HONSEN, 42, None)),   # 出町柳はMid行も停車
        (3, 3, (HONSEN, 42, 41)),
        (3, 4, (HONSEN, 41, None)),   # 神宮丸太町
        (3, 5, (HONSEN, 41, 40)),
        (3, 6, (HONSEN, 41, 40)),
        (3, 64, (HONSEN, 21, None)),  # 枚方市
        (3, 65, (HONSEN, 21, None)),  # 枚方市はMid行も停車
        (3, 66, (HONSEN, 21, 20)),
        (3, 115, (HONSEN, 4, None)),  # 京橋
        (3, 116, (HONSEN, 4, None)),  # 京橋はMid行も停車
        (3, 117, (HONSEN, 4, 3)),
        # 地下区間はすべて停車扱い
        (3, 118, (HONSEN, 3, None)),  # 天満橋
        (3, 120, (HONSEN, 3, None)),
        (3, 121, (HONSEN, 2, None)),  # 北浜
        (3, 126, (HONSEN, 1, None)),  # 淀屋橋
        # 中之島線（col 1,2）
        (1, 118, (NAKANOSHIMA, 3, None)),
        (1, 121, (NAKANOSHIMA, 51, None)),  # なにわ橋
        (2, 124, (NAKANOSHIMA, 52, None)),  # 大江橋
        (1, 127, (NAKANOSHIMA, 53, None)),  # 渡辺橋
        (2, 131, (NAKANOSHIMA, 54, None)),  # 中之島
        # col 1,2 でも117行目までは本線扱い
        (1, 117, (HONSEN, 4, 3)),
        # 宇治線
        (3, 132, (UJI, 28, None)),  # 中書島
        (3, 133, (UJI, 28, None)),
        (3, 134, (UJI, 28, 71)),
        (3, 135, (UJI, 71, None)),  # 観月橋
        (3, 136, (UJI, 71, 72)),
        (3, 150, (UJI, 76, None)),  # 三室戸
        (3, 152, (UJI, 76, 77)),
        (3, 153, (UJI, 77, None)),  # 宇治
        # 交野線
        (3, 154, (KATANO, 21, None)),  # 枚方市
        (3, 155, (KATANO, 21, None)),
        (3, 156, (KATANO, 21, 61)),
        (3, 157, (KATANO, 61, None)),  # 宮之阪
        (3, 158, (KATANO, 61, 62)),
        (3, 172, (KATANO, 66, None)),  # 河内森
        (3, 174, (KATANO, 66, 67)),
        (3, 175, (KATANO, 67, None)),  # 私市
    ],
)
def test_calc_position(col, row, expected):
    assert calc_position(col, row) == expected


@pytest.mark.parametrize(
    ("col", "row"),
    [
        (3, 0),
        (3, 176),
        (0, 50),
        (6, 50),
        (3, 127),  # 本線にRow127以降は無い
    ],
)
def test_calc_position_out_of_range(col, row):
    with pytest.raises(ValueError):
        calc_position(col, row)


# 路線ごとの駅の並び（上り方向）
LINE_ORDER = {
    HONSEN: stations_map.HONNSEN_UP,
    NAKANOSHIMA: stations_map.NAKANOSHIMA_UP,
    UJI: stations_map.UJI_UP,
    KATANO: stations_map.KATANO_UP,
}


def _valid_cells():
    for row in range(1, 176):
        for col in range(1, 6):
            if 127 <= row <= 131 and col not in (1, 2):
                continue  # 本線に存在しない領域
            yield col, row


def test_calc_position_returns_stations_on_the_line():
    """全座標で、返る駅がその路線の駅であり、走行中なら隣り合う2駅であること"""
    for col, row in _valid_cells():
        line, st1, st2 = calc_position(col, row)
        order = LINE_ORDER[line]
        assert st1 in order, (col, row)
        if st2 is not None:
            assert st2 in order, (col, row)
            assert abs(order.index(st1) - order.index(st2)) == 1, (col, row)


def test_calc_position_accepts_all_fixture_locations():
    """実データに含まれる全座標が計算できること"""
    data = load_fixture("trainPositionList.json")
    for loc in data["locationObjects"]:
        calc_position(int(loc["locationCol"]), int(loc["locationRow"]))
