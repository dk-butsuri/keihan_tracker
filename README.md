# keihan_tracker

京阪電車 リアルタイム列車位置情報API 非公式Pythonライブラリ

> ⚠️ **注意: 本ライブラリは京阪電車の非公式APIラッパーです。不具合やAPI仕様の変更などの可能性があります。利用は自己責任でお願いします。**

---

## 概要
京阪電車公式APIのJSONデータをPydanticモデルでバリデートし、Pythonクラスから駅・路線・列車・ダイヤ情報を直感的に操作できます。
`httpx` を用いた完全非同期設計で、リアルタイムなアプリケーションに適しています。

- **型安全**: 公式APIのJSONをPydanticでパース・バリデート
- **非同期**: `asyncio` / `httpx` ベースのノンブロッキングな通信
- **高度な位置特定**: 路線図上のグリッド座標から「走行中」か「停車中」かを判定
- **深夜対応**: 0～5時の深夜帯も営業日基準（24時過ぎ）として正しくハンドリング
- **多言語対応**: 駅名・路線名・遅延情報の多言語データを保持

## 制限事項・注意点
- **停車判定は推測です**:
    - APIは「駅ID」ではなく「路線図上の座標(col, row)」しか返しません。本ライブラリは座標から駅と停車状態を逆算するロジック（`position_calculation.py`）を実装していますが、ハードコーディングかつバイブコーディングであり、正確さは保証しません。また、公式サイトのレイアウト変更により判定がズレる可能性があります。
- **運行中の列車のみ**: API仕様上、現在走行中または準備中のアクティブな列車のみ取得できます。
- **到着ホーム・発車時刻**: これらはリアルタイムAPIには含まれていないため、取得できません。（到着時刻は取得可能です。また、一部の駅においてはCol/Rowから計算可能です。）

## インストール

```bash
pip install git+https://github.com/dk-butsuri/keihan_tracker.git
```

## 依存パッケージ
- Python 3.9+
- pydantic
- httpx
- tabulate

## 使い方

本ライブラリは非同期（async/await）で動作します。

```python
import asyncio
from keihan_tracker import KHTracker, TrainType

async def main():
    tracker = KHTracker()
    
    # ダイヤ情報（各駅の到着予定時刻）が必要な場合は実行
    await tracker.fetch_dia(download=True)

    # --- 例1: 特定の駅の情報を取得 ---
    # KH01（淀屋橋駅）
    station = tracker.stations[1]
    print(f"=== {station.station_name.ja}駅 ===")

    # 今後来る列車を表示
    print("【次に来る列車】")
    for train, stop_data in station.upcoming_trains:
        time_str = stop_data.time.strftime("%H:%M") if stop_data.time else "時刻不明"
        print(f"  [{time_str}] {train.train_type.value} {train.destination} 行き")


    # --- 例2: 条件に合う列車を検索 ---
    # 上り（京都方面）の特急を検索
    print("\n【走行中の上り特急】")
    up_ltd_exp = tracker.find_trains(
        type=TrainType.LTD_EXP, 
        direction="up"
    )
    
    for train in up_ltd_exp:
        status = "停車中" if train.is_stopping else f"走行中 -> {train.next_station}"
        delay = f"(遅れ: {train.delay_minutes}分)" if train.delay_minutes else ""
        
        print(f"  {train.train_number}号: {train.destination}行き {status} {delay}")

if __name__ == "__main__":
    asyncio.run(main())
```

## クラスリファレンス

### KHTracker
全体の管理クラス。

*   `stations: dict[int, StationData]`: 駅番号(int)をキーとした駅データの辞書
*   `trains: dict[int, TrainData]`: 列車管理番号(WDF)をキーとした列車データの辞書
*   `date: datetime.date`: 現在扱っているデータの営業日（深夜帯は前日扱い）

#### メソッド
*   `async fetch_pos()`: 列車位置・遅延情報をAPIから取得して更新します。
*   `async fetch_dia(download: bool)`: 列車の各駅予定時刻（ダイヤ）を取得します。
*   `find_trains(...)`: 条件に合致する列車リストを返します。
    *   `type`: `TrainType` (例: `TrainType.EXPRESS`)
    *   `direction`: `"up"` (京都方面) / `"down"` (大阪方面)
    *   `is_special`: 臨時列車かどうか
    *   `min_delay` / `max_delay`: 遅延分数によるフィルタ
    *   他 (`train_number`, `destination`, `has_premiumcar` 等)

### TrainData
個々の列車を表すクラス。

*   `train_number: str`: 列車番号
*   `train_type: TrainType`: 種別 (Enum)
*   `destination: StationData`: 行先駅
*   `direction`: `"up"` | `"down"`
*   `is_stopping: bool`: **[算出プロパティ]** 現在駅に停車中かどうか
*   `next_stop_station: StationData | None`: 次に停車する駅（停車中の場合はその駅）
*   `next_station: StationData | None`: 次に停車・通過する駅（停車中の場合はその駅）
*   `delay_minutes: int`: 遅延分数（正常時は0）
*   `cars: int`: 両数
*   `has_premiumcar: bool`: プレミアムカー有無
*   `stop_stations: list[StopStationData]`: 停車駅リスト
*   `get_stop_time(station) -> datetime`: 指定駅の到着予定時刻を取得

### StationData
駅を表すクラス。

*   `station_number: int`: 駅番号 (例: 1)
*   `station_name: MultiLang`: 駅名
*   `line: set[str]`: 所属路線名
*   `transfer: StationConnections`: 乗り換え路線情報
*   `arriving_trains`: この駅に向かっている（次がこの駅である）列車のリスト
*   `upcoming_trains`: この駅に今後停車するすべての列車のリスト

## 公式APIエンドポイント
本ライブラリは以下の公開JSONを利用しています。
- 駅・路線データ: `select_station.json`, `transferGuideInfo.json`
- リアルタイム位置: `trainPositionList.json`
- ダイヤ・時刻: `startTimeList.json`

## ライセンス
MIT License

Copyright © 2025 dk-butsuri