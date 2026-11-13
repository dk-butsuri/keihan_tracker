"""BUS NAVI の表示用 HTML から、便のキーと発着時刻を読み取る。"""

import re
import warnings
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterator, Optional, Union

from .schemas import BusDiagram, BusDiagramStop


@dataclass
class _Element:
    tag: str
    attrs: dict[str, Optional[str]] = field(default_factory=dict)
    children: list[Union["_Element", str]] = field(default_factory=list)

    def find_all(
        self, *, tag: Optional[str] = None, class_name: Optional[str] = None,
    ) -> Iterator["_Element"]:
        for child in self.children:
            if isinstance(child, _Element):
                if (tag is None or child.tag == tag) and (
                    class_name is None or class_name in (child.attrs.get("class") or "").split()
                ):
                    yield child
                yield from child.find_all(tag=tag, class_name=class_name)

    def text(self) -> str:
        return "".join(child.text() if isinstance(child, _Element) else child for child in self.children)


class _HTML(HTMLParser):
    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Element("")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, dict(attrs))
        self.stack[-1].children.append(element)
        if tag not in self._VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def build_diagram_dgmpl(stop_name: str, stop_num: int, *html_fragments: Optional[str]) -> Optional[str]:
    """公式の .btnDiagram と同じく、bus_key の分数と系統・方向から組み立てる。"""
    for html in html_fragments:
        if not html:
            continue
        inputs = _HTML(html).root.find_all(tag="input", class_name="bus_key")
        key = next(inputs, None)
        if key is None:
            continue
        parts = (key.attrs.get("value") or "").split(":")
        if len(parts) != 4 or not all(part.isascii() and part.isdigit() for part in parts):
            raise ValueError("bus_key の形式が想定と異なります")
        hour, minute = divmod(int(parts[2]), 60)
        # parts[3] と parts[1] は数の加算ではなく、文字列として連結する。
        return f"{stop_name}:{stop_num}:1:{parts[3]}{parts[1]}:{hour}:{minute}"
    return None


@dataclass(frozen=True)
class BusDelay:
    scheduled_time: Optional[str]   # 定刻（HH:MM）
    delay_text: Optional[str]       # 括弧内の表記（「定時運行中」「約1分遅れ」「到着済」）。未受信なら None
    delay_minutes: Optional[int]    # 遅れ（分）。定時運行中なら0。未受信・到着済・読めない表記なら None


def _header(html: str) -> Optional[_Element]:
    root = _HTML(html).root
    # PC 版は div.bsul.first、スマートフォン版は li.busstate_header に定刻と遅れが入る
    return next(root.find_all(tag="div", class_name="first"), None) or next(
        root.find_all(tag="li", class_name="busstate_header"), None)


def _delay_minutes(text: str) -> Optional[int]:
    if text == "定時運行中":
        return 0
    if text == "到着済":
        return None
    if m := re.fullmatch(r"約(?:([0-9]+)時間)?(?:([0-9]+)分)?遅れ", text):
        if m[1] is None and m[2] is None:
            return None
        return int(m[1] or 0) * 60 + int(m[2] or 0)
    warnings.warn(f"バスの遅れの表記を読めませんでした: {text!r}")
    return None


def parse_bus_delay(*html_fragments: Optional[str]) -> BusDelay:
    """接近情報の HTML の見出し（「定刻： 21:11 (約1分遅れ)」）から、定刻と遅れを読む。"""
    for html in html_fragments:
        if not html:
            continue
        header = _header(html)
        if header is None:
            continue
        text = " ".join(header.text().split())
        m = re.search(r"定刻：\s*([0-9]{1,2}:[0-5][0-9])(?:\s*\(([^)]*)\))?", text)
        if m is None:
            continue
        delay_text = m[2]
        return BusDelay(
            scheduled_time=m[1],
            delay_text=delay_text,
            delay_minutes=None if delay_text is None else _delay_minutes(delay_text),
        )
    return BusDelay(None, None, None)


def _time(element: _Element) -> tuple[Optional[str], bool]:
    text = " ".join(element.text().split())
    if text in ("", "-", "－", "―", "—"):
        return None, False
    match = re.fullmatch(r"(△)?\s*([0-9]{1,2}:[0-5][0-9])\s*(△)?\s*[発着]?", text)
    if match is None:
        raise ValueError(f"発着時刻の形式が想定と異なります: {text!r}")
    return match[2], bool(match[1] or match[3])


def parse_diagram(html: str, dgmpl: str) -> BusDiagram:
    root = _HTML(html).root
    stops = []
    for line in root.find_all(tag="ul", class_name="lineul"):
        for row in line.find_all(tag="li", class_name="lines"):
            name = next(row.find_all(tag="p", class_name="eki"), None)
            arrival = next(row.find_all(tag="p", class_name="arr"), None)
            departure = next(row.find_all(tag="p", class_name="dep"), None)
            if name is None or arrival is None or departure is None:
                raise ValueError("発着時刻表の行に停留所名・着時刻・発時刻がそろっていません")
            stop_name = " ".join(name.text().split())
            if not stop_name:
                raise ValueError("発着時刻表の停留所名が空です")
            arrival_time, arrival_estimated = _time(arrival)
            departure_time, departure_estimated = _time(departure)
            if arrival_time is None and departure_time is None:
                raise ValueError(f"{stop_name} に発着時刻がありません")
            stops.append(BusDiagramStop(
                stop_name=stop_name,
                arrival_time=arrival_time,
                departure_time=departure_time,
                arrival_is_estimated=arrival_estimated,
                departure_is_estimated=departure_estimated,
            ))
    if not stops:
        raise ValueError("発着時刻表が見つかりません")
    return BusDiagram(dgmpl=dgmpl, stops=stops)
