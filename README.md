# keihan_tracker

京阪電車 リアルタイム列車位置情報API 非公式Pythonライブラリ

> ⚠️ **注意: 本ライブラリは京阪電車の非公式APIラッパーです。不具合やAPI仕様の変更などの可能性があります。利用は私的かつ自己責任でお願いします。**

---

## 概要
京阪電車公式APIのJSONデータをPydanticモデルでバリデートし、Pythonクラスから駅・路線・列車・ダイヤ情報を直感的に操作できるライブラリです。

「現在どこを走っているか」だけでなく、「これからどのような列車が来るか」というダイヤ情報も統合して扱えるため、駅の発車標のようなアプリケーションも簡単に作成できます。

### 特徴
*   **ダイヤ・位置情報の統合**: 走行中の列車(`ActiveTrainData`)と、これから走る予定の列車(`TrainData`)を統一的に扱えます。
*   **種別・方向の自動推定**: 予定データ（ダイヤ）には本来含まれていない「列車種別」や「進行方向」を、停車駅リストから自動的に推定します。
*   **相互リンク構造**: 「この駅に来る列車」⇔「この列車の次の駅」といった情報を双方向に参照可能。
*   **型安全**: 公式APIのJSONをPydanticで厳密に定義。エディタの補完が効きます。
*   **高度な位置特定**: 路線図上のグリッド座標から「走行中」か「停車中」かを独自ロジックで判定。
*   **バス・遅延情報**: 電車だけでなく、バスの接近情報や関西エリア全体の運行情報も取得可能。

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
               ├── .train_type        ──→ [推定] 停車駅に基づく種別
               └── .direction         ──→ [推定] 始発・終着に基づく方向
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
- beautifulsoup4 (Yahoo!遅延情報取得に使用)
- tzdata (タイムゾーン情報)

## 使い方

### 1. 基本的なデータ取得（電車）
一度だけデータを取得して表示する例です。

```python
import asyncio
from keihan_tracker import KHTracker, TrainType

async def main():
    tracker = KHTracker()
    
    # APIから最新データを取得・更新
    # 駅情報などの静的データは初回のみ、列車位置は毎回更新されます
    # 注意: これを呼ばないとデータは空のままです！
    await tracker.fetch_pos()

    # --- 特定の駅から情報を見る ---
    # 駅番号はKH番号の数字部分です (例: KH01 淀屋橋 -> 1, KH40 三条 -> 40)
    station = tracker.stations[1] 
    print(f"=== {station.station_name.ja}駅 ===")

    print("【次に来る列車】")
    for train, stop_data in station.upcoming_trains:
        # stop_dataにはその駅への到着予定時刻などが含まれます
        time_str = stop_data.time.strftime("%H:%M") if stop_data.time else "不明"
        # 運行予定の列車でも train_type や direction が推定されます
        print(f"  [{time_str}] {train.train_type.value} {train.destination} 行き")


    # --- 条件に合う列車を探す ---
    # 例: 「上り（京都方面）」の「特急」を検索
    up_ltd_exp = tracker.find_trains(
        train_type=TrainType.LTD_EXP, 
        direction="up"
    )
    
    print("\n【走行中の上り特急】")
    for train in up_ltd_exp:
        # ActiveTrainData（走行中）の場合、is_stoppingなどが使えます
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
```

### 4. 運行情報（遅延情報）の取得
遅延情報の取得には2つの方法があります。

#### A. Yahoo!路線情報 (スクレイピング)
関西エリア（デフォルト）の運行情報を取得します。
注：Yahoo!路線情報の運行情報は二次利用を禁止しており、私的利用に限ります。

```python
import asyncio
from keihan_tracker.delay_tracker import get_yahoo_delay

async def main():
    # エリアコードを指定可能（デフォルト6=近畿）
    delays = await get_yahoo_delay()
    
    print("【現在の運行情報 (Yahoo!)】")
    if not delays:
        print("現在、目立った遅延情報はありません。")
    
    for info in delays:
        print(f"・{info.LineName}: {info.status}")
        print(f"  {info.detail}\n")

if __name__ == "__main__":
    asyncio.run(main())
```

#### B. 駅すぱあと API (推奨・公式API)
駅すぱあとのAPIキーをお持ちの場合は、こちらを利用することでより安定して情報を取得できます。
レスキューナウが提供する信頼性の高い運行情報です。

```python
import asyncio
from keihan_tracker.delay_tracker import get_ekispert_delay

async def main():
    # APIキーを指定して取得
    # prefs引数で都道府県コードを指定可能（デフォルトは京都・大阪・兵庫）
    try:
        delays = await get_ekispert_delay(api_key="YOUR_API_KEY")
        
        print("【現在の運行情報 (駅すぱあと)】")
        for info in delays:
             print(f"・{info.LineName}: {info.status}")
             print(f"  {info.detail}\n")
             
    except Exception as e:
        print(f"取得エラー: {e}")

if __name__ == "__main__":
    asyncio.run(main())
```

## 知っておくべき仕様・注意点 (ハマりポイント)

初めて使う際に躓きやすいポイントをまとめました。

1.  **非同期処理が必須 (`async`/`await`)**
    *   本ライブラリは `httpx` を使用した非同期設計です。`tracker.fetch_pos()` などのメソッドは必ず `await` する必要があります。
    *   通常の `def main():` ではなく、`async def main():` と書き、`asyncio.run(main())` で実行してください。

2.  **`fetch_pos()` を呼ばないとデータは空**
    *   `KHTracker()` をインスタンス化した直後は、駅データも列車データも空です。
    *   必ず最初に `await tracker.fetch_pos()` を呼び出してください。また、列車位置は自動更新されないため、最新位置を知るには定期的にこのメソッドを呼ぶ必要があります。

3.  **「走行中」と「予定」の列車クラスの違い**
    *   **ActiveTrainData**: 現在APIに位置情報が存在する列車です。`is_stopping` (停車中か) や `location_col` (座標) などのリアルタイムなプロパティを持ちます。
    *   **TrainData**: ダイヤ上の予定データ、または運行終了したデータです。これらには現在位置情報がないため、`is_stopping` などのプロパティにアクセスするとエラーにはなりませんが、常にFalseなどのデフォルト値であったり、意味を持たない場合があります。
    *   `tracker.trains` には両方が混在しています。区別するには `isinstance(train, ActiveTrainData)` を使用してください。

4.  **駅番号は整数 (int)**
    *   駅番号（ナンバリング）は `KH01` のような文字列ではなく、数字部分の整数 `1` として扱います。辞書のキーも整数です。

5.  **時刻は日本標準時 (JST)**
    *   ライブラリ内で扱われる `datetime` オブジェクトには、すべてタイムゾーン情報（`Asia/Tokyo`）が付与されています。
    *   現在時刻と比較する場合は、`datetime.now()` ではなく `datetime.now(ZoneInfo("Asia/Tokyo"))` などと比較しないとエラーになる場合があります。

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

### 列車データの構造 (TrainData と ActiveTrainData)

本ライブラリでは、列車の状態によって2つのクラスが使われます。
`ActiveTrainData` は `TrainData` を継承しており、**「TrainDataの全情報 ＋ リアルタイム位置情報」** を持っています。

| 項目 | TrainData (予定・終了) | ActiveTrainData (走行中) | 備考 |
| :--- | :--- | :--- | :--- |
| **基本情報** | ○ | ○ | ID, 編成, 行先など |
| **ダイヤ情報** | ○ | ○ | 停車駅リスト, 時刻表 |
| **列車種別** | △ **[推定]** | ◎ **[確定]** | 予定データは停車駅から推定 |
| **進行方向** | △ **[推定]** | ◎ **[確定]** | 予定データは始発・終着から推定 |
| **現在位置** | × | **○** | 座標, 停車中判定 |
| **遅延情報** | × | **○** | 遅延分数 |

#### TrainData (基底クラス)
全ての列車のベースとなるクラスです。主にダイヤ情報（静的な予定）を保持します。
*   `wdfBlockNo: int`: 列車管理番号
*   `destination: StationData`: 行先駅
*   `stop_stations: list[StopStationData]`: 全停車駅のリスト
*   `train_type`: **[推定]** 停車駅パターンから推定された種別
*   `direction`: **[推定]** 始発・終着から推定された方向 ("up"|"down")

#### ActiveTrainData (継承クラス)
現在走行中の列車です。`TrainData` に加え、以下の**リアルタイム情報**を持ちます。
*   `is_stopping: bool`: **[重要]** 現在、駅に停車中かどうか
*   `next_stop_station: StationData`: **[重要]** 次に停車する駅（停車中の場合はその駅）
*   `delay_minutes: int`: 遅延分数（定刻なら0）
*   `train_number: str`: 列車番号 (例: "A1201A")
*   `cars: int`: 両数
*   `location_col`, `location_row`: 路線図上のグリッド座標

### StationData (電車)
駅を表すクラス。

*   `station_number: int`: 駅番号 (例: 1)
*   `station_name: MultiLang`: 駅名（.ja, .en 等）
*   `arriving_trains: list[TrainData]`: 「次はこの駅に止まる」という状態の列車リスト。
*   `upcoming_trains: list[tuple[TrainData, StopStationData]]`: この駅に今後停車するすべての列車とその予定時刻等のリスト（時刻順）。

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
Yahoo!路線情報または駅すぱあとから取得した遅延情報モデル。

*   `LineName: str`: 路線名 (例: "京阪本線")
*   `status: str`: 状態概要 (例: "遅延")
*   `detail: str`: 詳細な遅延理由や状況のテキスト

## 公式APIエンドポイント
本ライブラリは以下の公開JSONを利用しています。
- 駅・路線データ: `select_station.json`, `transferGuideInfo.json`
- リアルタイム位置: `trainPositionList.json`
- ダイヤ・時刻: `startTimeList.json`

## ライセンス
MIT License

Copyright © 2025 dk-butsuri
