# keihan_tracker

京阪電車 リアルタイム列車位置情報API 非公式Pythonライブラリ

> ⚠️ **注意: 本ライブラリは京阪電車の非公式APIラッパーです。不具合やAPI仕様の変更などの可能性があります。利用は自己責任でお願いします。**

---

## 概要
京阪電車公式APIのJSONデータをPydanticモデルでバリデートし、Pythonクラスから駅・路線・列車・ダイヤ情報を直感的に操作できるライブラリです。

**v2.0 Update:**
データ構造を大幅に刷新し、**「現在走行中の列車」だけでなく、「運行予定の列車（ダイヤ情報）」も統合して扱えるようになりました。**
走行前の列車であっても、停車駅リストから**自動的に列車種別（特急・急行など）を推定**し、駅の発車標のように「次に来る列車」としてシームレスに取得可能です。

### 特徴
- **相互リンク構造**: 「この駅に来る列車」⇔「この列車の次の駅」といった情報を双方向に参照可能。
- **型安全**: 公式APIのJSONをPydanticで厳密に定義。エディタの補完が効きます。
- **高度な位置特定**: 路線図上のグリッド座標から「走行中」か「停車中」かを独自ロジックで判定。
- **バス・遅延情報**: 電車だけでなく、バスの接近情報や関西エリア全体の運行情報も取得可能。

## データ構造と関係性
本ライブラリは、**全体管理クラス**、**駅クラス**、**列車クラス**が相互に繋がっています。

```text
[KHTracker] (全体管理)
  │
  ├── .stations (全駅: dict)
  │     └── [StationData] (駅)
  │            ├── .upcoming_trains ──→ この駅に来る [TrainData] のリスト
  │            └── .transfer        ──→ 乗り換え路線情報
  │
  └── .trains   (全列車: dict)
        └── [TrainData] (列車)
               ├── .next_stop_station ──→ 次に止まる [StationData]
               ├── .stop_stations     ──→ 停車駅リスト
               └── .delay_minutes     ──→ 遅延情報
```

## インストール

```bash
pip install git+https://github.com/dk-butsuri/keihan_tracker.git
```

## 依存パッケージ
- Python 3.10+
- pydantic
- httpx
- tabulate
- beautifulsoup4 (遅延情報取得に使用)

## 使い方

本ライブラリは `asyncio` を使用した非同期コードとして記述する必要があります。

### 1. 基本的なデータ取得（電車）
一度だけデータを取得して表示する例です。

```python
import asyncio
from keihan_tracker import KHTracker, TrainType

async def main():
    tracker = KHTracker()
    
    # APIから最新データを取得・更新
    # 駅情報などの静的データは初回のみ、列車位置は毎回更新されます
    await tracker.fetch_pos()

    # --- 特定の駅から情報を見る ---
    # 駅番号はKH番号の数字部分です (例: KH01 淀屋橋 -> 1, KH40 三条 -> 40)
    station = tracker.stations[1] 
    print(f"=== {station.station_name.ja}駅 ===")

    print("【次に来る列車】")
    for train, stop_data in station.upcoming_trains:
        # stop_dataにはその駅への到着予定時刻などが含まれます
        time_str = stop_data.time.strftime("%H:%M") if stop_data.time else "不明"
        print(f"  [{time_str}] {train.train_type.value} {train.destination} 行き")


    # --- 条件に合う列車を探す ---
    # 例: 「上り（京都方面）」の「特急」を検索
    up_ltd_exp = tracker.find_trains(
        type=TrainType.LTD_EXP, 
        direction="up"
    )
    
    print("\n【走行中の上り特急】")
    for train in up_ltd_exp:
        status = "停車中" if train.is_stopping else f"走行中 -> 次は {train.next_station}"
        delay = f"(遅れ: {train.delay_minutes}分)" if train.delay_minutes else ""
        print(f"  {train.train_number}号: {status} {delay}")

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. リアルタイム監視（ポーリング）
定期的に情報を更新して、列車の動きを監視するパターンの例です。
`fetch_pos()` をループ内で呼び出し続けてください。

```python
import asyncio
from keihan_tracker import KHTracker

async def watch_loop():
    tracker = KHTracker()
    
    while True:
        try:
            # データを更新
            await tracker.fetch_pos()
            
            # 例: 現在最も遅れている列車を表示
            worst_train = tracker.max_delay_train
            max_delay = tracker.max_delay_minutes

            print(f"\r現在走行中の列車数: {len(tracker.trains)} | 最大遅延: {max_delay}分", end="")
            
            if max_delay > 5:
                 print(f"\n⚠️ {worst_train.train_number}号 ({worst_train.train_type.value}) が {max_delay}分 遅延しています！")

        except Exception as e:
            print(f"更新エラー: {e}")

        # 30秒待機 (サーバー負荷軽減のため短すぎる間隔は避けてください)
        await asyncio.sleep(30)

if __name__ == "__main__":
    try:
        asyncio.run(watch_loop())
    except KeyboardInterrupt:
        print("\n終了します")
```

### 3. 京阪バス接近情報の取得
京阪バスのバスナビシステムから接近情報を取得します。

```python
import asyncio
from keihan_tracker.bus.tracker import get_khbus_info

async def main():
    # バス停名と番号を指定（番号は通常1ですが、のりばや方向によって異なります）
    bus_info = await get_khbus_info("京阪香里園", 1)

    for bus in bus_info.body.busstates:
        prms = bus.busstateprms
        print(f"[{prms.route}] {prms.destination} 行き")
        print(f"  状況: {prms.status} (予定: {prms.timetable})")
        print(f"  現在地: 緯度{prms.lat}, 経度{prms.lon}")

if __name__ == "__main__":
    asyncio.run(main())

# 出力結果例
[[9A]] 枚方市駅南口行 行き
  状況: 約7分後に到着 (予定: 15:53 到着予定)
  現在地: 緯度34.78445057797146, 経度135.63204188354845
[[15]] 星田駅行 行き
  状況: 約17分後に到着 (予定: 16:03 到着予定)
  現在地: 緯度34.7842008070003, 経度135.64362426647654
[[9A]] 枚方市駅南口行 行き
  状況: 約27分後に到着 (予定: 16:13 到着予定)
  現在地: 緯度34.789917128013336, 経度135.65883940632168
...
[[9A]] 枚方市駅南口行 行き
  状況: 約1時間8分後に到着 (予定: 定刻 16:53)
  現在地: 緯度0.0, 経度0.0
[[14]] 津田駅行 行き
  状況: 約1時間18分後に到着 (予定: 定刻 17:03)
  現在地: 緯度0.0, 経度0.0
...

```

### 4. 運行情報（遅延情報）の取得
Yahoo!路線情報から関西エリア（デフォルト）の運行情報をスクレイピングします。

```python
import asyncio
from keihan_tracker.delay_tracker import get_yahoo_delay

async def main():
    # エリアコードを指定可能（デフォルト6=近畿）
    delays = await get_yahoo_delay()
    
    print("【現在の運行情報】")
    if not delays:
        print("現在、目立った遅延情報はありません。")
    
    for info in delays:
        print(f"・{info.LineName}: {info.status}")
        print(f"  {info.detail}\n")

if __name__ == "__main__":
    asyncio.run(main())

# 出力結果例
【現在の運行情報】
・JR京都線: 列車遅延
  13:54頃、琵琶湖線内での線路内点検の影響で、下り線(大阪方面行)の一部列車に遅れが出ています。（12月21日 14時45分掲載）

・湖西線: 運転状況
  荒天予想の影響で、一部列車に運休が出ています。16:00頃から、和邇〜近江今博駅間の運転を見合わせます。（12月21日 14時30分掲載）

・JR神戸線: 列車遅延
  13:54頃、琵琶湖線内での線路内点検の影響で、下り線(姫路方面行)の一部列車に遅れが出ています。（12月21日 15時15分掲載）

```
## クラスリファレンス

### KHTracker (電車)
ライブラリのルートとなる管理クラス。

*   `stations: dict[int, StationData]`: 駅データ。キーは駅番号の整数値（KH01なら1）。
*   `trains: dict[int, TrainData | ActiveTrainData]`: 全列車データ（走行中・予定・終了含む）。キーは内部管理番号(WDF)。
*   `active_trains: dict[int, ActiveTrainData]`: 現在走行中の列車データのみを抽出した辞書。
*   `date: datetime.date`: 現在の営業日（深夜帯は前日扱い）。

#### 主要メソッド
*   `async fetch_pos()`: **[重要]** 最新の列車位置・遅延情報をAPIから取得し、インスタンス内のデータを更新します。
*   `find_trains(...)`: 条件（種別、方向、遅延有無など）に合致する列車をリストで返します。

### TrainData (電車)
列車情報を表す基底クラス。運行予定や運行終了済みの列車もこれに含まれます。

*   `wdfBlockNo: int`: 列車管理番号
*   `train_formation: Optional[int]`: 編成番号
*   `has_premiumcar: Optional[bool]`: プレミアムカー有無
*   `destination: StationData`: 行先駅
*   `status`: 運行状況 ("active"(走行中), "scheduled"(予定), "completed"(終了))
*   `stop_stations: list[StopStationData]`: 全停車駅のリスト
*   `start_station: StationData`: 始発駅

### ActiveTrainData (電車)
`TrainData` を継承した、現在走行中の列車を表すクラス。

*   `train_number: str`: 列車番号
*   `train_type: TrainType`: 種別 (LTD_EXP, EXPRESS, LOCAL 等)
*   `direction`: "up"(京都方面) / "down"(大阪方面)
*   `is_stopping: bool`: **[算出]** 現在、駅に停車中かどうか
*   `next_stop_station: StationData`: 次の停車駅（停車中の場合はその駅）
*   `delay_minutes: int`: 遅延分数（定刻なら0）
*   `cars: int`: 車両数

### StationData (電車)
駅を表すクラス。

*   `station_number: int`: 駅番号 (例: 1)
*   `station_name: MultiLang`: 駅名（.ja, .en 等）
*   `arriving_trains: list[TrainData]`: 「次はこの駅に止まる」という状態の列車リスト。
*   `upcoming_trains: list[tuple[TrainData, StopStationData]]`: この駅に今後停車するすべての列車とその予定時刻等のリスト。

### BusLocationResponse / BusStatePrms (バス)
バス接近情報のレスポンスモデル。

*   `BusLocationResponse.body.busstates`: `BusState` のリスト
*   `BusState.busstateprms`: 詳細情報オブジェクト
    *   `route: str`: 系統番号 (例: "[22]")
    *   `destination: str`: 行先
    *   `status: str`: 運行状況テキスト
    *   `timetable: str`: 到着予定時刻など
    *   `lat`, `lon`: バスの現在座標

### DelayLine (遅延情報)
Yahoo!路線情報から取得した遅延情報モデル。

*   `LineName: str`: 路線名 (例: "京阪本線")
*   `status: str`: 状態概要 (例: "遅延")
*   `detail: str`: 詳細な遅延理由や状況のテキスト

## 制限事項・注意点
- **停車判定の仕様**: APIは「駅ID」ではなく「路線図上の座標(col, row)」しか返しません。本ライブラリは座標から駅と停車状態を逆算する独自のロジックを使用しています。公式サイトのレイアウト変更等により、判定がずれる可能性があります。
- **取得できる列車**: `fetch_pos()` では現在走行中または準備中のアクティブな列車情報を更新します。ダイヤ情報 (`startTimeList`) により、当日の運行予定列車も `TrainData` として取得可能ですが、リアルタイムな位置情報はアクティブな列車 (`ActiveTrainData`) のみが持ちます。
- **バス・遅延情報**: これらは外部サイト（京阪バスナビ、Yahoo!路線情報）の構造変更により、予告なく取得できなくなる可能性があります。

## 公式APIエンドポイント
本ライブラリは以下の公開JSONを利用しています。
- 駅・路線データ: `select_station.json`, `transferGuideInfo.json`
- リアルタイム位置: `trainPositionList.json`
- ダイヤ・時刻: `startTimeList.json`

## ライセンス
MIT License

Copyright © 2025 dk-butsuri