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
*   **バス・遅延情報**: 電車だけでなく、京阪バスの接近情報や関西エリア全体の運行情報も取得可能。
*   **レートリミット内蔵**: サーバー負荷を考慮し、15秒/reqのレート制限を内蔵。意図しない高頻度取得を防ぎます。

## データ構造と関係性
本ライブラリは、**全体管理クラス**、**駅クラス**、**列車クラス**が相互に繋がっています。

```text
[KHTracker] (全体管理)
  │
  ├── .stations (全駅: dict)
  │     └── [StationData] (駅)
  │            ├── .upcoming_trains ──→ 停車中・今後この駅に停車する [TrainData] のリスト
  │            └── .arriving_trains ──→ 停車中・次にこの駅に停車する [TrainData]のリスト
  │
  └── .trains   (全列車: dict)
        └── [TrainData|ActiveTrainData] (列車)
               ├── .next_stop_station ──→ 次に止まる [StationData]
               ├── .stop_stations     ──→ 停車駅リスト
               ├── .train_type        ──→ [推定] 停車駅に基づく種別
               ├── .direction         ──→ [推定] 始発・終着に基づく方向
               └── その他多数...
```

## インストール

```bash
pip install git+https://github.com/dk-butsuri/keihan_tracker.git
```

## 依存パッケージ
```pip install```時にインストールされます。
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
    # 駅情報などの静的データは初回のみ取得、列車位置は毎回更新されます
    # 注意: これを呼ばないとデータは空のままです！
    await tracker.fetch_pos()

    # --- 特定の駅から情報を見る ---
    # 駅番号はKH番号の数字部分です (例: KH01 淀屋橋 -> 1, KH40 三条 -> 40)
    station = tracker.stations[17] 
    print(f"=== {station.station_name.ja}駅 ===")
    
    # 次の列車を取得
    next_train, arrival_time = station.upcoming_trains[0]
    
    if arrival_time.time is not None:
        time = arrival_time.time.strftime("%H時%M分 到着予定")
    else:
        time = ""
    
    print(time)
    print(next_train)

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
        # データを更新
        await tracker.fetch_pos()
        
        # 例: 現在最も遅れている列車を表示
        worst_train = tracker.max_delay_train
        max_delay = tracker.max_delay_minutes

        print(f"\r現在走行中の列車数: {len(tracker.trains)} | 最大遅延: {max_delay}分")
        
        if worst_train:
            print(f"⚠️ {worst_train.train_number}号 ({worst_train.train_type.value}) が {max_delay}分 遅延しています！")

        # 60秒待機 (京阪側の更新頻度は1分間隔)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(watch_loop())
```

### 3. 遅延を加味した停車駅到着時刻の推定

`delay_minutes` と `get_stop_time()` を組み合わせるだけでは、列車が「単に遅延して走っている」のか「途中駅で足止めされている」のかを区別できません。大幅遅延時は `stopping_time` と `is_at_start_station` を使うことで、推定到着時刻の信頼度を判定できます。
遅延分数を停車時刻表に表示する際は、どの駅で何分間停車しているか表示すると良いでしょう。

```python
from datetime import timedelta
from keihan_tracker import KHTracker, ActiveTrainData, StationData

STUCK_THRESHOLD = timedelta(minutes=15)  # この時間以上停車なら足止めと判定

def estimate_arrival(train: ActiveTrainData, station: StationData):
    """
    遅延を加味した推定到着時刻を返す。
    足止めと判定した場合は None を返す（推定不可）。
    """
    scheduled = train.get_stop_time(station)
    if scheduled is None:
        return None

    estimated = scheduled + timedelta(minutes=train.delay_minutes)

    # 始発駅での長時間停車は正常（発車待ち）なので除外
    if train.is_at_start_station:
        return estimated

    # 閾値以上停車中なら足止めの可能性があり推定信頼度が低い
    if train.stopping_time > STUCK_THRESHOLD:
        return None  # 推定不可

    return estimated
```

### 4. 京阪バス接近情報の取得
京阪グループ「BUS NAVI」から接近情報を取得します。

**注：利用規約では「商業的二次利用」が禁止されており、私的利用に限ります。**
また使用に際しては、[京阪グループBUS NAVI利用規約](https://busnavi.keihanbus.jp/pc/termsofuse)を必ずご確認ください。

```python
import asyncio
from keihan_tracker.bus.tracker import get_khbus_info

async def main():
    # バス停名と番号を指定（番号はのりばや方向によって異なります）
    bus_info = await get_khbus_info("京阪香里園", 1)

    for bus in bus_info.body.busstates:
        prms = bus.busstateprms
        print(f"[{prms.route}] {prms.destination} 行き")
        print(f"  状況: {prms.status} (予定: {prms.timetable})")
        print(f"  現在地: 緯度{prms.lat}, 経度{prms.lon}")
        print("\n")

if __name__ == "__main__":
    asyncio.run(main())
```

### 5. 運行情報（遅延情報）の取得
遅延情報の取得には2つの方法があります。
* Yahoo!路線情報 (スクレイピング)
* 駅すぱあと 運行情報API（要契約）
#### A. Yahoo!路線情報 (スクレイピング)
関西エリア（デフォルト）の運行情報を取得します。 

**注：Yahoo!路線情報の運行情報は[二次利用を禁止しており](https://support.yahoo-net.jp/PccTransit/s/article/H000007493)、私的利用に限ります。**
利用に際しては、[LINEヤフー共通利用規約](https://www.lycorp.co.jp/ja/company/terms/)も必ずご覧ください。

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

#### B. 駅すぱあと API
駅すぱあとのAPIキーをお持ちの場合は、こちらを利用することでより安定して情報を取得できます。
レスキューナウから提供されている信頼性の高い運行情報を使用できます。

```python
import asyncio
from keihan_tracker.delay_tracker import get_ekispert_delay

async def main():
    # APIキーを指定して取得
    # prefs引数で都道府県コードを指定可能（デフォルトは京都・大阪・兵庫）
    delays = await get_ekispert_delay(api_key="YOUR_API_KEY")
    
    print("【現在の運行情報 (駅すぱあと)】")
    for info in delays:
        print(f"・{info.LineName}: {info.status}")
        print(f"  {info.detail}\n")

if __name__ == "__main__":
    asyncio.run(main())
```

## 知っておくべき仕様・注意点 (ハマりポイント)

このライブラリを使用する際の注意点を以下に挙げます。

1.  **非同期処理が必須 (`async`/`await`)**
    *   本ライブラリは `httpx` を使用した非同期設計です。`tracker.fetch_pos()` などのメソッドは必ず `await` する必要があります。
    *   通常の `def main():` ではなく、`async def main():` と書き、`asyncio.run(main())` で実行してください。

2.  **`fetch_pos()` を呼ばないとデータは空**
    *   `KHTracker()` をインスタンス化した直後は、駅データも列車データも空です。
    *   必ず最初に `await tracker.fetch_pos()` を呼び出してください。また、列車位置は自動更新されないため、最新位置を知るには定期的（30秒から数分間隔が目安）にこのメソッドを呼ぶ必要があります。

3.  **「走行中」と「予定」の列車クラスの違い**
    *   **ActiveTrainData**: 現在APIに位置情報が存在する列車です。`is_stopping` (停車中か) や `location_col` (座標) などのリアルタイムなプロパティを持ちます。
    *   **TrainData**: ダイヤ上の予定データ、または運行終了したデータです。これらには現在位置情報がないため、`is_stopping` などのプロパティにアクセスするとエラーにはなりませんが、常にFalseなどのデフォルト値であったり、意味を持たない場合があります。
    *   `tracker.trains` には両方が混在しています。区別するには `isinstance(train, ActiveTrainData)` を使用してください。
    *   **臨時列車 (`is_special=True`) は必ず `ActiveTrainData`** です。走行が確認された時点で `ActiveTrainData` として登録され、同時にダイヤ情報も登録されます。走行が確認されるまではダイヤ登録をスキップするため、当日運転しない臨時列車のゴースト登録を防いでいます。（2026GWで運行された臨時列車でゴースト現象を確認済み）

4.  **駅番号は整数 (int)**
    *   駅番号（ナンバリング）は `KH01` のような文字列ではなく、数字部分の整数 `1` として扱います。辞書のキーも整数です。

5.  **時刻は日本標準時 (JST)**
    *   ライブラリ内で扱われる `datetime` オブジェクトには、すべてタイムゾーン情報（`Asia/Tokyo`）が付与されています。
    *   現在時刻と比較する場合は、`datetime.now()` ではなく `datetime.now(ZoneInfo("Asia/Tokyo"))` などと比較しないとエラーになります。

## クラスリファレンス

### KHTracker (電車)
ライブラリのルートとなる管理クラス。

```python
KHTracker(rate_limit: float = 15)
```
`rate_limit`: `fetch_pos()` の最小呼び出し間隔（秒）。デフォルトは15秒。これよりも高頻度で実行すると、取得処理がスキップされる。

*   `stations: dict[int, StationData]`: 駅データ。キーは駅番号の整数値（KH01なら1）。
*   `trains: dict[int, TrainData | ActiveTrainData]`: 全列車データ（走行中・予定・終了含む）。キーは内部管理番号(WDF)。
*   `active_trains: dict[int, ActiveTrainData]`: 現在走行中の列車データのみを抽出した辞書
*   `date: datetime.date`: 現在の営業日（24-5時の深夜帯は前日扱い）
*   `max_delay_train: Optional[ActiveTrainData]`: 最も遅延している列車（0分の場合はNoneを返す）
*   `max_delay_minutes: int`: 現在の最大遅延分数
*   `last_fetch_pos_datetime: Optional[datetime]`: 最後に `fetch_pos()` を実行した時刻
*   `rate_limit_interval: float`: 現在設定されているレートリミット間隔（秒）

#### 主要メソッド
*   `async fetch_pos()`: **[重要]** 最新の列車位置・遅延情報をAPIから取得し、インスタンス内のデータを更新します。
*   `async regist_dia(download: bool)`: ダイヤ情報を更新します。通常は `fetch_pos()` から自動的に呼び出されるため、実行する必要はありません。
*   `find_trains(...)`: 条件に合致する列車をリストで返します。全引数はオプションで、省略した項目は絞り込み対象外となります。

`find_trains(...)` の引数：

| 引数 | 型 | 説明 |
|:---|:---|:---|
| `status` | `"active" \| "scheduled" \| "completed"` | 運行状態。`status` 参照 |
| `train_type` | `TrainType` | 列車種別 |
| `direction` | `"up" \| "down"` | 進行方向（ActiveTrainData のみ有効） |
| `is_special` | `bool` | 臨時列車かどうか（ActiveTrainData のみ有効） |
| `train_number` | `str` | 列車番号（例: `"1051"`）（ActiveTrainData のみ有効） |
| `has_premiumcar` | `bool` | プレミアムカー連結の有無 |
| `destination` | `StationData` | 行先駅 |
| `next_stop_station` | `StationData` | 次の停車駅（ActiveTrainData のみ有効） |
| `min_delay` | `int` | この分数を**超える**遅延のみ対象 |
| `max_delay` | `int` | この分数**未満**の遅延のみ対象 |
| `is_stopping` | `bool` | 停車中かどうか（ActiveTrainData のみ有効） |

### 列車データの構造 (TrainData と ActiveTrainData)

本ライブラリでは、列車の状態によって2つのクラスが使われます。
`ActiveTrainData` は `TrainData` を継承しており、**「TrainDataの全情報 ＋ リアルタイム位置情報」** を持っています。

判定する際は`isinstance()`関数を用いると、型厳密に、そしてエディターで入力候補が使用できます。

| 項目 | TrainData (予定・終了) | ActiveTrainData (走行中) | 備考 |
| :--- | :--- | :--- | :--- |
| **基本情報** | ◎ | ◎ | ID, 編成, 行先など |
| **ダイヤ情報** | ◎ | ◎ | 停車駅リスト, 到着時刻 |
| **列車種別** | △ **[推定]** | ◎ | 予定/終了の場合、停車駅から推定、ライナーは識別不可 |
| **進行方向** | △ **[推定]** | ◎ | 予定/終了の場合、始発・終着から推定 |
| **現在位置** | × | **○** **[一部推定]** | 座標, 停車中判定 |
| **遅延情報** | × | **◎** | 遅延分数 |

#### TrainData (基底クラス)
全ての列車のベースとなるクラスです。主にダイヤ情報（静的な予定）を保持します。
*   `master`: KHTrackerインスタンス
*   `wdfBlockNo: int`: 列車管理番号
*   `date: datetime.date`: この列車が属する営業日。0〜5時の深夜帯は前日扱いとハードコーディングされています。
*   `destination: StationData`: 行先駅
*   `start_station: StationData`: 始発駅
*   `stop_stations: list[StopStationData]`: 全停車駅のリスト
*   `route_stations: list[StopStationData]`: 停車・通過駅のリスト（一部の通過駅のみが含まれる）
*   `has_premiumcar: Optional[bool]`: プレミアムカーがあるか
*   `delay_minutes: int` 遅延分数（`TrainData` では常に0）
*   `train_formation: Optional[int]` 列車編成（3003など）
*   `train_type: TrainType`: **[推定]** 停車駅パターンから推定された種別
*   `direction: Literal["up","down"]`: **[推定]** 始発・終着から推定された方向
*   `line: LineLiteral`: **[推定]** 列車の走行路線（"京阪本線・鴨東線", "宇治線" など）
*   `status: Literal["active","scheduled","completed"]`: 運行状態。**`"active"` のみ正確**。`"scheduled"` / `"completed"` はダイヤ上の予定時刻から推定するため精度は保証されません。`find_trains(status="active")` などと組み合わせて使います。
*   `is_completed: bool`: 運行完了フラグ。`status` 同様、精度は保証されません。

なお、`ActiveTrainData` が運行終了後に `TrainData` に降格した際、`direction` と `train_type` は引き継がれます。

#### ActiveTrainData (継承クラス)
現在走行中の列車です。`TrainData` に加え、以下の**リアルタイム情報**を持ちます。
*   `is_stopping: bool`: 現在、駅に停車中かどうか
*   `next_stop_station: StationData`: 次に停車する駅（**停車中の場合はその駅**）
*   `next_station: StationData`: 次に停車・**通過**する駅
*   `delay_minutes: int`: 遅延分数（定刻なら0）
*   `delay_text: MultiLang`: 遅延テキスト（「約5分」など各言語で）
*   `train_number: str`: 列車番号 (例: "1051"（号）など)
*   `cars: int`: 車両数
*   `location_col`, `location_row`: zaisen上のグリッド座標
*   `is_special: bool`: 臨時列車かどうか
*   `is_at_start_station: bool`: 現在、始発駅に停車中かどうか
*   `stopping_time: datetime.timedelta`: 現在の駅に停車している時間。走行中は `timedelta(0)` を返す
*   `lastpass_station: Optional[StationData]`: ⚠️ **使用非推奨**。基本的に停車中 or 次に停車する駅。公式zaisenページの列車詳細における停車時刻表示の開始基準として使われる内部値であり、「最後に通過した駅」として厳密に管理されているわけではありません。例えば萱島～京橋を走る各駅列車でも常に萱島が設定されるなど、路線・区間によって信頼性の低い値が返ります。

### StationData
駅を表すクラス。

*   `station_number: int`: 駅番号 (例: 1)
*   `station_name: MultiLang`: 駅名（.ja, .en 等）
*   `line: set[LineLiteral]`: 所属路線（例: `{"京阪本線・鴨東線", "中之島線"}`）
*   `transfer: StationConnections`: 乗換情報。JR・地下鉄・モノレールなどへの乗り入れ路線を保持します。
*   `arriving_trains: list[ActiveTrainData]`: 停車中、もしくは**次に**停車する走行中列車のリスト
*   `upcoming_trains: list[tuple[TrainData | ActiveTrainData, StopStationData]]`: 停車中、もしくは**今後**停車する全列車とその到着時刻のリスト（時刻順）。
*   `trains: list[tuple[TrainData | ActiveTrainData, StopStationData]]`: **過去・現在・未来すべて**の停車列車リスト（時刻順）。`upcoming_trains` が「これから停車する列車」のみを返すのに対し、こちらはすでに通過済みの列車も含みます。発車標ではなく運行履歴や全停車情報が必要な場合に使用します。

### StopStationData
列車の停車・通過駅を表すクラス。train.stop_stations や station.upcoming_trains の戻り値に含まれます。
   * station: StationData: 駅
   * time: Optional[datetime]: 到着/出発時刻（始発・終着・通過駅などでNoneの場合あり）
   * is_stop: bool: 停車するかどうか（通過駅ならFalse）
   * is_start: bool: この駅が始発駅かどうか
   * is_final: bool: この駅が終着駅かどうか

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
*   `AnnouncedTime: datetime`: 運行情報の発表時刻

### MultiLang
多言語対応の文字列型。`station_name` や `delay_text` などで使われます。

*   `ja: str`: 日本語
*   `en: str`: 英語
*   `cn: str`: 簡体字中国語
*   `tw: str`: 繁体字中国語
*   `kr: str`: 韓国語

### StationConnections
駅の乗換情報を保持するクラス。`StationData.transfer` から参照できます。各フィールドは `MultiLang_Lines` 型（各言語の路線名リスト）で、乗換先がない場合は `None` です。

*   `train: Optional[MultiLang_Lines]`: JRなど鉄道の乗換情報
*   `subway: Optional[MultiLang_Lines]`: 地下鉄の乗換情報
*   `monorail: Optional[MultiLang_Lines]`: モノレールの乗換情報

### TrainType
列車種別を表す列挙型（`str` のサブクラス）。`.value` で日本語文字列を取得できます。

| 定数名 | 値 |
|:---|:---|
| `TrainType.LOCAL` | `"普通"` |
| `TrainType.SEMI_EXP` | `"区間急行"` |
| `TrainType.SUB_EXP` | `"準急"` |
| `TrainType.COMMUTER_SUB_EXP` | `"通勤準急"` |
| `TrainType.EXPRESS` | `"急行"` |
| `TrainType.COMMUTER_EXP` | `"通勤急行"` |
| `TrainType.MIDNIGHT_EXP` | `"深夜急行"` |
| `TrainType.RAPID_EXP` | `"快速急行"` |
| `TrainType.COMMUTER_RAPID_EXP` | `"通勤快急"` |
| `TrainType.LTD_EXP` | `"特急"` |
| `TrainType.LINER` | `"ライナー"` |
| `TrainType.RAPID_LTD_EXP` | `"快速特急 洛楽"` |
| `TrainType.EXTRA_TRAIN` | `"臨時列車"` |

> **注**: ダイヤ上のデータからライナーを識別することは仕様上困難なため、`TrainData.train_type` では `LINER` ではなく `RAPID_EXP` または `LTD_EXP` と推定されます。`ActiveTrainData` ではAPIから正確な種別が返ります。

## 公式APIエンドポイント
本ライブラリは以下の公開JSONを利用しています。
- 駅・路線データ: `select_station.json`, `transferGuideInfo.json`
- リアルタイム位置: `trainPositionList.json`
- ダイヤ・時刻: `startTimeList.json`

## ライセンス
MIT License

Copyright © 2025 dk-butsuri
