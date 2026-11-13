"""
v2.8 で保存した全列車・全駅の出力（tests/fixtures/equivalence/）と、今の出力が一致すること。
内部を作り直しても推定が変わっていないことを確かめる。保存し直す方法は equivalence.py を参照。
"""

from typing import Any

import pytest

from equivalence import dump, load_golden


def _diff(old: Any, new: Any, path: str = "") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[str] = []
        for key in sorted(old.keys() | new.keys()):
            if key not in new:
                out.append(f"{path}/{key}: 消えた")
            elif key not in old:
                out.append(f"{path}/{key}: 増えた")
            else:
                out += _diff(old[key], new[key], f"{path}/{key}")
        return out
    return [] if old == new else [f"{path}: {old!r} -> {new!r}"]


def _assert_same(name: str, tracker) -> None:
    diffs = _diff(load_golden(name), dump(tracker))
    assert not diffs, f"{name} で {len(diffs)} 件変わりました:\n" + "\n".join(diffs[:30])


def test_saved_tracker_unchanged(saved_tracker, request):
    name = request.node.callspec.id
    _assert_same(name, saved_tracker)


def test_holiday_tracker_unchanged(holiday_tracker):
    _assert_same("holiday", holiday_tracker)
