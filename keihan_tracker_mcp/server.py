# -*- coding: utf-8 -*-
"""
keihan_tracker_mcp — 京阪電車 リアルタイム位置・ダイヤ情報の薄いMCPサーバー（stdio）。

設計方針:
  - 計算はしない。素のデータ（現在位置・列車ごとの停車パターン・駅時刻表）を返すだけ。
  - 経路探索・通過時刻補間などはMCPクライアント側のLLMがエージェンティックに組み立てる。
  - そのための「予備知識」はサーバーのinstructionsとREADMEリソースで与える。
  - 鮮度管理はライブラリ内蔵の15秒レート制限に委譲（fetch_posは毎回呼んでも安全にno-opする）。
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

from keihan_tracker.keihan_train import KHTracker
from keihan_tracker.keihan_train.tracker import ActiveTrainData, TrainData
from keihan_tracker.keihan_train.schemes import TrainType

JST = ZoneInfo("Asia/Tokyo")
_REPO_ROOT = Path(__file__).resolve().parent.parent
_README = _REPO_ROOT / "README.md"

# ---- LLMに渡す予備知識（実APIの実値から起こしたドメイン辞書） --------------------
INSTRUCTIONS = """\
京阪電車（非公式）のリアルタイム位置情報・当日ダイヤを返す薄いMCPです。
**非公式APIラッパーであり、値は推定を含み無保証です。重要な判断には使わないでください。**

このサーバーは生データを返すだけです。経路探索・最速比較・通過時刻の推定などは、
返ってきたデータと下記の座標系を使ってあなた（クライアント側）が組み立ててください。

## 駅ナンバリング（=地理的な並び順。経路推論の座標系）
- 1〜42 = 京阪本線・鴨東線。1=淀屋橋（大阪側の端）→ 42=出町柳（京都側の端）。
  **駅番号の大小がそのまま並び順**。番号が小さい=大阪寄り、大きい=京都寄り。
- 支線は本線の分岐駅から枝番が振られる:
  - 51〜54 = 中之島線（分岐駅 = 3 天満橋）
  - 61〜67 = 交野線（分岐駅 = 21 枚方市）
  - 71〜77 = 宇治線（分岐駅 = 28 中書島）
- 主要駅: 4=京橋, 11=守口市, 14=古川橋, 16=萱島, 17=寝屋川市, 18=香里園,
  20=枚方公園, 21=枚方市, 24=樟葉, 28=中書島, 30=丹波橋, 37=七条, 40=三条, 42=出町柳。
  全駅は list_stations() で取得可能。

## direction（進行方向）
- "down" = 大阪方面（本線では駅番号が減る向き / 淀屋橋・中之島側）
- "up"   = 京都方面（本線では駅番号が増える向き / 出町柳側）

## 列車の識別子（重要）
- wdfBlockNo = 運用ブロック番号。**列車を一意に指すキー**。get_train はこれで引く。
- train_number（"2301"号 等）は **一意ではない**。種別違いの別列車が同じ番号を持つことがある。
  列車を特定する用途には wdfBlockNo を使うこと。

## 編成番号（train_formation）— 鉄道ファンの「13027f」等の記法
- 「13027f」「6002F」のような **数字＋f/F** は **編成番号**（その車両編成の固有番号。fは formation の意）。
  これは train_formation フィールドに対応する。例: "13027f" → train_formation == 13027。
- 先頭の系列を表す: 13000系=13xxx, 10000系=10xxx, 9000系=9xxx, 7200系=72xx, 7000系=7xxx,
  6000系=6xxx, 2400系=24xx 等。
- **train_formation は走行中(active)列車のみが持つ live 属性**。当日ダイヤ(scheduled)には無い。
  → 編成を探すには get_live_positions() を取得し train_formation で突き合わせる
    （便利ツール find_by_formation も用意）。走っていない編成は特定できない。

## 乗り継ぎ・方向の判定（取り違え注意）
- get_station_schedule の各エントリは terminates_here（当駅止まりか）と
  next_stop_after（当駅の次の停車駅）を持つ。**乗り継ぎ可否はこの2つで判断**する。
- terminates_here=true の列車は当駅で運用終了。その先へは行かない（接続列車にできない）。
- 行先 destination はあくまで終点。「destination=当駅」なら当駅止まり＝当駅に到着して終わる列車。
  destination だけ見て進行方向を判断しないこと（実際にどちらへ進むかは next_stop_after で確認）。

## 種別（train_type）
普通 / 準急 / 急行 / 区間急行 / 快速急行 / 特急 / 通勤準急 / 通勤快急 / 快速特急 洛楽。
停車駅は種別だけでは決まらない（同種別でも多数のパターンがある）。
**特定列車の停車駅は get_train の route_stations を見ること**（種別から推測しない）。

## 時刻データ（route_stations）
- route_stations は通過駅も含む全停車順。is_stop=False は通過駅。
- time（ISO8601/JST）は **停車駅にのみ入る**。始発駅と通過駅は null。
- 通過駅の通過時刻が必要なら、前後の停車駅の time から自分で補間して推定すること。

## 鮮度
位置情報は要求時に取得。15秒以内の連続呼び出しは同じスナップショットになる（内蔵レート制限）。
"""


# ---- トラッカー（シングルトン・遅延ロード） ------------------------------------
_tracker: Optional[KHTracker] = None
_dia_date: Optional[datetime.date] = None


def _service_date() -> datetime.date:
    now = datetime.datetime.now(JST)
    d = now.date()
    if 0 <= now.hour <= 5:  # 深夜帯は前日の運行日として扱う
        d -= datetime.timedelta(days=1)
    return d


async def _get_tracker(*, need_dia: bool) -> KHTracker:
    """位置を最新化したトラッカーを返す。need_diaならダイヤも保証（1日1回）。"""
    global _tracker, _dia_date
    if _tracker is None:
        _tracker = KHTracker()
    await _tracker.fetch_pos()  # 15秒以内なら内部でno-op
    if need_dia and _dia_date != _service_date():
        await _tracker.regist_dia(download=True)
        _dia_date = _service_date()
    return _tracker


# ---- シリアライズ（相互参照を断ってJSON可能なdictへ） ---------------------------
def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _ja(station) -> Optional[str]:
    if station is None:
        return None
    return getattr(station.station_name, "ja", None) or str(station.station_name)


def _station_ref(station) -> Optional[dict[str, Any]]:
    if station is None:
        return None
    return {"num": station.station_number, "name": _ja(station)}


def _stop_dto(stop) -> dict[str, Any]:
    return {
        "num": stop.station.station_number,
        "name": _ja(stop.station),
        "is_stop": stop.is_stop,
        "is_start": stop.is_start,
        "is_final": stop.is_final,
        "time": stop.time.isoformat() if stop.time else None,
    }


def _parse_hhmm(s: str) -> Optional[int]:
    """時刻文字列を真夜中からの分に変換する。LLMの表記ゆれに寛容（読めなければNone）。
    例: "19:30" "9:30" "19時30分" "1930" "7:30 PM" "19:30:00" 深夜の "25:10"(=翌1:10) 等。"""
    if s is None:
        return None
    t = str(s).strip().lower()
    pm = "pm" in t or "午後" in t
    am = "am" in t or "午前" in t
    # 「19時半」→「19:30」、全角コロン・「時」「分」など区切りを統一し、数字とコロンだけ残す
    t = t.replace("時半", ":30").replace("：", ":").replace("時", ":").replace("分", "")
    digits_colon = "".join(ch for ch in t if ch.isdigit() or ch == ":")
    digits_colon = digits_colon.strip(":")
    if not digits_colon:
        return None
    try:
        if ":" in digits_colon:
            parts = digits_colon.split(":")
            h, m = int(parts[0]), int(parts[1] or 0)  # 秒以降は無視
        else:  # 区切り無し: "1930"→19:30, "930"→9:30, "19"→19:00
            d = digits_colon
            if len(d) <= 2:
                h, m = int(d), 0
            else:
                h, m = int(d[:-2]), int(d[-2:])
    except (ValueError, IndexError):
        return None
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    # 深夜帯の25時表記等を正規化（0..47時を0..23へ畳む）
    if not (0 <= h <= 47 and 0 <= m <= 59):
        return None
    return (h % 24) * 60 + m


def _circular_diff_minutes(a: int, b: int) -> int:
    """日内の時刻同士の差（深夜越えを考慮した円環距離、単位:分）。"""
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def _next_stop_after(train: TrainData, station_number: int) -> Optional[dict[str, Any]]:
    """route_stations上で指定駅の次に停車する駅を返す（当駅止まりならNone）。"""
    rs = train.route_stations
    for i, s in enumerate(rs):
        if s.station.station_number == station_number:
            for nxt in rs[i + 1:]:
                if nxt.is_stop:
                    return {**_station_ref(nxt.station),
                            "time": nxt.time.isoformat() if nxt.time else None}
            return None
    return None


def _effective_time(train: TrainData, station_number: int, own_time) -> Optional[datetime.datetime]:
    """発車標で使う時刻。停車時刻があればそれ。始発駅など time=None の場合は
    次に時刻を持つ停車駅の時刻で代用する（始発の発車時刻はデータ上nullのため）。"""
    if own_time:
        return own_time
    rs = train.route_stations
    for i, s in enumerate(rs):
        if s.station.station_number == station_number:
            for nxt in rs[i + 1:]:
                if nxt.time:
                    return nxt.time
            break
    return None


def _train_summary(t: TrainData) -> dict[str, Any]:
    """位置や停車パターンを含まない軽量サマリ。"""
    return {
        "wdfBlockNo": t.wdfBlockNo,
        "train_number": getattr(t, "train_number", None),
        "train_formation": getattr(t, "train_formation", None),
        "train_type": _safe(lambda: t.train_type.value),
        "direction": _safe(lambda: t.direction),
        "destination": _safe(lambda: _station_ref(t.destination)),
        "delay_minutes": _safe(lambda: t.delay_minutes, 0),
        "status": _safe(lambda: t.status),
        "has_premiumcar": t.has_premiumcar,
        "line": _safe(lambda: t.line),
        "is_active": isinstance(t, ActiveTrainData),
    }


def _live_dto(t: ActiveTrainData) -> dict[str, Any]:
    """走行中列車の現在位置情報。"""
    stopping = _safe(lambda: t.is_stopping)
    return {
        "is_stopping": stopping,
        "at_or_next_station": _safe(lambda: _station_ref(t.next_station)),
        "next_stop_station": _safe(lambda: _station_ref(t.next_stop_station)),
        "last_passed_station": _safe(lambda: _station_ref(t.lastpass_station)),
        "grid": [t.location_col, t.location_row],
        "is_special": getattr(t, "is_special", None),
    }


# ---- MCPサーバー定義 -----------------------------------------------------------
mcp = FastMCP("keihan-tracker", instructions=INSTRUCTIONS)


@mcp.resource("keihan://readme")
def readme() -> str:
    """ライブラリのREADME（ドメイン知識・データ構造の詳細）。"""
    return _README.read_text(encoding="utf-8") if _README.exists() else "README.md not found."


@mcp.tool()
async def get_domain_guide() -> str:
    """京阪のデータを正しく読むための前提知識（駅ナンバリングの座標系、direction の意味、
    wdfBlockNo と編成番号「13027f」記法、種別、時刻データの仕様、乗り継ぎ判定）を返す。
    **他のツールを使う前に最初に1回呼ぶこと。** 駅番号の大小=地理的並び順、といった
    回答の前提がここに全部書いてある。"""
    return INSTRUCTIONS


@mcp.tool()
async def list_stations() -> list[dict[str, Any]]:
    """全駅の一覧（駅番号・駅名・所属路線）。駅名↔駅番号の解決や座標系の確認に使う。
    駅番号は地理的な並び順（1=淀屋橋〜42=出町柳、支線は枝番）。詳細は get_domain_guide。"""
    tr = await _get_tracker(need_dia=False)
    return [
        {"num": num, "name": _ja(s), "lines": sorted(s.line)}
        for num, s in sorted(tr.stations.items())
    ]


@mcp.tool()
async def get_live_positions() -> list[dict[str, Any]]:
    """現在走行中の全列車のスナップショット（位置・種別・行先・遅延・編成番号 train_formation）。
    direction は "up"=京都方面/"down"=大阪方面。各列車は wdfBlockNo で一意（train_number は非一意）。
    用語・座標系の前提は get_domain_guide を参照。"""
    tr = await _get_tracker(need_dia=False)
    out = []
    for t in tr.active_trains.values():
        d = _train_summary(t)
        d["live"] = _live_dto(t)
        out.append(d)
    return out


@mcp.tool()
async def get_train(wdf_block_no: int) -> dict[str, Any]:
    """指定したwdfBlockNoの列車の詳細。停車パターン(route_stations: 通過駅含む全駅順・停車駅のみ時刻)と、
    走行中なら現在位置(live)を返す。経路や通過時刻の推定はこのroute_stationsを使って行うこと。"""
    tr = await _get_tracker(need_dia=True)
    t = tr.trains.get(wdf_block_no)
    if t is None:
        return {"error": f"wdfBlockNo {wdf_block_no} は見つかりません。", "available_count": len(tr.trains)}
    d = _train_summary(t)
    d["route_stations"] = [_stop_dto(s) for s in t.route_stations]
    if isinstance(t, ActiveTrainData):
        d["live"] = _live_dto(t)
    return d


@mcp.tool()
async def get_station_schedule(
    station_number: int,
    direction: Optional[str] = None,
    around_time: Optional[str] = None,
    window_minutes: Optional[int] = None,
) -> dict[str, Any]:
    """指定駅の時刻表（当日ダイヤ全体を源にした発車標）。time窓で絞り込む（件数ではなく時間で絞る）。
    源は station.trains（過去・現在・未来すべての停車列車）なので、過去の時刻も引ける。
    - around_time を省略: 「今から到着する列車」モード。現在時刻から window_minutes 分先まで
      （デフォルト60分）を返す（過去は出さない）。
    - around_time="09:30" のように指定: 「09:30ごろの列車」モード。その時刻を中心に
      ±window_minutes/2 分（デフォルト window_minutes=30、つまり±15分）の範囲を返す。
      朝の列車など現在より前の時刻でも当日ダイヤから取得できる。
    注意: 停車列車のみ（通過列車は含まない）。通過時刻は get_train の route_stations を見ること。
    direction("up"/"down")で進行方向を絞り込み可。
    各エントリ: time(当駅の時刻), departs_from_here(当駅始発か), time_estimated(timeが発車目安か),
    terminates_here(当駅止まりか), next_stop_after(当駅の次の停車駅), is_stopping_now, train(軽量サマリ)。
    始発駅（淀屋橋・出町柳・中之島など）は発車時刻がデータ上null。その場合 time には次駅基準の
    発車目安を入れ time_estimated=true で示す（±数分の近似）。
    乗り継ぎ判定は terminates_here と next_stop_after を必ず見ること
    （当駅止まりの列車はその先へ行かない。行先destinationだけで方向を判断しない）。"""
    tr = await _get_tracker(need_dia=True)
    station = tr.stations.get(station_number)
    if station is None:
        return {"error": f"駅番号 {station_number} は存在しません。"}

    # station.trains = 過去・現在・未来すべての停車列車（時刻順）。upcoming_trains はこれを
    # 「未来だけ」に絞った派生なので、全日を源にして around_time 窓で自前に絞る。
    day_trains = _safe(lambda: station.trains, [])
    if direction:
        day_trains = [(tr_, st) for tr_, st in day_trains if _safe(lambda: tr_.direction) == direction]

    # 各列車の発車標時刻を確定（始発駅は time=None なので次駅時刻で代用）
    timed = []
    for t, s in day_trains:
        eff = _effective_time(t, station_number, s.time)
        if eff is not None:
            timed.append((t, s, eff))

    now = datetime.datetime.now(tz=JST)
    if around_time is None:
        window = window_minutes if window_minutes is not None else 60
        now_min = now.hour * 60 + now.minute
        filtered = [
            (t, s, eff) for t, s, eff in timed
            if 0 <= _circular_diff_minutes(eff.hour * 60 + eff.minute, now_min) <= window
            and eff >= now
        ]
        mode = {"type": "from_now", "window_minutes": window}
    else:
        window = window_minutes if window_minutes is not None else 30
        target_min = _parse_hhmm(around_time)
        if target_min is None:
            return {
                "error": f'around_time="{around_time}" を時刻として解釈できませんでした。'
                         '"19:30" のような24時間表記で渡してください。',
                "station": _station_ref(station),
            }
        filtered = [
            (t, s, eff) for t, s, eff in timed
            if _circular_diff_minutes(eff.hour * 60 + eff.minute, target_min) <= window / 2
        ]
        mode = {"type": "around_time", "around_time": around_time, "window_minutes": window}

    filtered.sort(key=lambda x: x[2])
    entries = []
    for train, stop, eff in filtered:
        entries.append({
            "time": eff.isoformat(),
            "departs_from_here": stop.is_start,  # Trueなら当駅始発
            "time_estimated": stop.time is None,  # 始発で次駅時刻を代用した場合True（発車目安）
            "terminates_here": stop.is_final,  # Trueなら当駅止まり（この先へ行かない）
            "next_stop_after": _safe(lambda: _next_stop_after(train, station_number)),
            "is_stopping_now": _safe(lambda: train.is_stopping) if isinstance(train, ActiveTrainData) else False,
            "train": _train_summary(train),
        })
    return {
        "station": _station_ref(station),
        "direction": direction,
        "mode": mode,
        "returned": len(entries),
        "trains": entries,
    }


@mcp.tool()
async def find_trains(
    train_type: Optional[str] = None,
    direction: Optional[str] = None,
    destination_number: Optional[int] = None,
    status: Optional[str] = None,
    min_delay: Optional[int] = None,
) -> list[dict[str, Any]]:
    """条件で当日ダイヤの列車を絞り込み、軽量サマリのリストを返す。
    train_type は日本語ラベル（例 "特急" "普通"）。direction は "up"=京都方面/"down"=大阪方面。
    status は "active"/"scheduled"/"completed"。注意: direction/is_special フィルタは
    走行中(active)列車にしか効かない。用語・座標系は get_domain_guide を参照。"""
    tr = await _get_tracker(need_dia=True)
    tt = TrainType(train_type) if train_type else None
    dest = tr.stations.get(destination_number) if destination_number is not None else None
    trains = tr.find_trains(
        status=status, train_type=tt, direction=direction,
        destination=dest, min_delay=min_delay,
    )
    return [_train_summary(t) for t in trains]


@mcp.tool()
async def find_by_formation(formation: int) -> dict[str, Any]:
    """編成番号（鉄道ファンの「13027f」等の数字部分）で走行中の列車を探す。
    例: "13027f" を探すなら formation=13027。一致すれば現在位置(live)と停車パターンを返す。
    編成番号は走行中列車のみが持つため、運用に入っていない編成は見つからない。"""
    tr = await _get_tracker(need_dia=False)
    for t in tr.active_trains.values():
        if t.train_formation == formation:
            d = _train_summary(t)
            d["route_stations"] = [_stop_dto(s) for s in t.route_stations]
            d["live"] = _live_dto(t)
            return d
    running = sorted(x.train_formation for x in tr.active_trains.values() if x.train_formation)
    return {
        "found": False,
        "message": f"編成 {formation}f は現在走行中ではありません（運用外、または本日未充当）。",
        "running_formations": running,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
