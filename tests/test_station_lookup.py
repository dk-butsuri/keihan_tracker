"""駅番号・駅名での検索。"""

import dataclasses

import pytest

from keihan_tracker import KHState


def test_all_station_names_and_numbers(tracker):
    for number, station in tracker.stations.items():
        for key in (number, str(number), f"KH{number:02}", f"kh{number}",
                    station.station_name.ja, f"  {station.station_name.en.upper()}  "):
            assert tracker.station(key) is station
            assert tracker.state.station(key) is station


@pytest.mark.parametrize("key", [0, 43, "KH99", "unknown", ""])
def test_unknown_station_includes_original_key(tracker, key):
    with pytest.raises(KeyError) as error:
        tracker.station(key)
    assert error.value.args == (key,)


def test_station_before_loading():
    with pytest.raises(KeyError):
        KHState().station("KH18")


def test_duplicate_name_is_an_error(tracker):
    state = KHState()
    station = tracker.station("KH18")
    state._stations[18] = station
    state._stations[19] = dataclasses.replace(station, station_number=19)
    with pytest.raises(ValueError, match="重複"):
        state.station(station.station_name.ja)
