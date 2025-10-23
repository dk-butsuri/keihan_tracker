

# keihan_tracker

京阪電車 リアルタイム列車位置情報API 非公式Pythonライブラリ

> ⚠️ **注意: 本ライブラリは京阪電車の非公式APIラッパーです。不具合やAPI仕様の変更などの可能性があります。利用は自己責任でお願いします。**

---

## 概要
京阪電車公式APIのJSONデータをPydanticモデルでバリデートし、それらをKHTrackerクラスから駅・路線・列車・ダイヤ情報を直感的に操作できます。
深夜帯（0～3時）の時刻も正しく扱える設計です。

- 公式APIのJSONを型安全にパース・バリデート
- 駅・路線・列車位置・ダイヤ情報をPythonクラスで操作
- 多言語対応（駅名・路線名、遅延情報）
- 最小限な依存パッケージ

## 制限事項・注意点
- **停車中かどうかの判定はできません。**
    - 各列車の `next_station` 属性で「停車中または次に停車する駅」が分かりますが、「今まさに停車中かどうか」はAPI仕様上取得が困難です。（`locationCol`・`locationRow`の値から頑張れば割り出せますが、実装がとてもめんどくさいです。）
    - そのため、列車が駅に停車しているかどうかの判定はできません。
    - また、列車の到着ホーム、始発駅からの出発時刻も同様にAPI仕様上取得が困難です。
- API仕様上、運行中のアクティブな列車のみ取得できます。

## クラス構成

### KHTracker
京阪電車のリアルタイム列車位置情報を管理するメインクラス。

* `stations: dict[int, StationData]` ・・・駅番号→駅データ
* `trains: dict[int, TrainData]` ・・・列車管理番号→列車データ
* `fetch_pos()` ・・・列車位置情報をAPIから取得・更新
* `fetch_dia(download=False)` ・・・ダイヤ情報をAPIから取得・更新
* `find_trains(type: Optional[TrainType]=None, direction: Optional[Literal["up","down"]]=None, ...)` ・・・条件に合致する列車を検索

#### `find_trains` メソッド
指定された条件にすべて合致する列車を検索します。

**引数:**
    - `type`: 列車種別（`TrainType` Enum, 例: `TrainType.LOCAL`）
    - `direction`: 上り/下り（`"up"` or `"down"`）
    - `is_special`: 臨時列車かどうか（bool）
    - `train_number`: 列車番号（例: `"2201"`）
    - `has_premiumcar`: プレミアムカー付きかどうか（bool）
    - `destination`: 行先（`StationData`オブジェクト）
    - `next_station`: 次の停車駅 or 停車中の駅（`StationData`オブジェクト）
**返り値:**
    - 条件に合致した `TrainData` のリスト


### StationData
駅情報を表すクラス。
- `station_number: int` ・・・駅番号（例: KH01 = 淀屋橋）
- `station_name: MultiLang` ・・・多言語駅名
- `line: set[str]` ・・・所属路線
- `transfer: StationConnections` ・・・乗り換え情報
- `arriving_trains: list[TrainData]` ・・・次に停車する/停車中の列車
- `trains: list[tuple["TrainData","StopStationData"]]` ・・・停車する/停車した全列車
- `upcoming_trains: list[tuple["TrainData","StopStationData"]]` ・・・今後停車する/停車中の列車

### TrainData
列車情報を表すクラス。
* `wdfBlockNo: int` ・・・列車管理番号
* `train_number: str` ・・・列車番号
* `train_type: TrainType` ・・・列車種別（Enum型, 例: `TrainType.LOCAL`）
* `is_special: bool` ・・・臨時列車かどうか
* `cars: int` ・・・車両数
* `direction: "up"|"down"` ・・・上り/下り（京都方面/大阪方面）
* `destination: StationData` ・・・行先
* `delay_minutes: None` ・・・遅延分数（未実装）
* `delay_text: MultiLang|None` ・・・遅延情報（多言語テキスト）
* `next_station: StationData|None` ・・・次の停車駅 or 通過駅 or 停車中の駅
* `location_col: int|None` ・・・列車の現在位置インデックス（APIのlocationCol値）
* `location_row: int|None` ・・・列車の現在位置インデックス（APIのlocationRow値）
* `route_stations: list[StopStationData]` ・・・経路にある駅
* `stop_stations: list[StopStationData]` ・・・停車駅のみ
* `start_station: StationData` ・・・始発駅
* `get_stop_time(station)` ・・・指定駅に停車する時刻(datetime)を返す

### StopStationData
停車駅情報を表すクラス。
- `is_start: bool` ・・・始発駅か
- `is_stop: bool` ・・・停車駅か
- `station: StationData` ・・・駅データ
- `time: datetime|None` ・・・停車時刻（始発駅や通過駅はNone）

### MultiLang
駅名・路線名の多言語表現。
- `ja`, `en`, `cn`, `tw`, `kr`: str


### StationConnections
駅で接続する交通手段（電車・地下鉄・モノレール）とその路線名（多言語対応）を表すクラス。
- `train: MultiLang_Lines|None` ・・・接続する鉄道路線名リスト（多言語）
- `subway: MultiLang_Lines|None` ・・・接続する地下鉄路線名リスト（多言語）
- `monorail: MultiLang_Lines|None` ・・・接続するモノレール路線名リスト（多言語）

#### MultiLang_Lines
複数言語での路線名リストを保持するクラス。
- `ja: list[str]` ・・・日本語名
- `en: list[str]` ・・・英語名
- `cn: list[str]` ・・・中国語（簡体字）名
- `tw: list[str]` ・・・中国語（繁体字）名
- `kr: list[str]` ・・・韓国語名

## インストール
```pip install git+https://github.com/dk-butsuri/keihan_tracker.git```

## 使い方
```python
from keihan_tracker import KHTracker
tracker = KHTracker()
await tracker.fetch_pos()
await tracker.fetch_dia()

# KH01（淀屋橋駅）
station = tracker.stations[1]

# 淀屋橋駅から発車する列車
for train, stop_data in station.upcoming_trains:
    if stop_data.time:
        print(f"{stop_data.time.hour:02}:{stop_data.time.minute:02} [{train.train_type}] {train.destination} 行き")
    else:
        # 始発駅、通過駅は時刻がNoneとなる
        print(f"時刻不明 [{train.train_type}] {train.destination} 行き")

出力例：
時刻不明 [準急] 出町柳 行き
時刻不明 [特急] 出町柳 行き
13:50 [快速急行] 淀屋橋 行き

# 上り特急を検索
up_lmtexp_trains = tracker.find_trains(type="特急", direction="up")
for train in up_lmtexp_trains:
    print(f"  {train.train_type}{train.train_number}号 {train.destination}行き (次の駅: {train.next_station})")

出力例：
  特急1301号 出町柳行き (次の駅: 北浜)
  特急1303号 出町柳行き (次の駅: 天満橋)



# KH03（天満橋）の路線（集合）を取得
print(tracker.stations[3].line)
出力：{'京阪本線・鴨東線', '中之島線'}

# 列車位置を更新（数分ごとに実行されるべき）
tracker.fetch_pos()
# ダイヤ情報を更新（数時間～1日毎に実行されるべき）
tracker.fetch_dia(download = True)
```

## 依存パッケージ
- pydantic
- httpx
- tabulate

## 公式APIエンドポイント

- 駅名乗り換えデータ: https://www.keihan.co.jp/zaisen/transferGuideInfo.json
- 駅データ: https://www.keihan.co.jp/zaisen/select_station.json
- 列車走行位置情報: https://www.keihan.co.jp/zaisen-up/trainPositionList.json
- 標準到着時間のJSON: https://www.keihan.co.jp/zaisen-up/startTimeList.json

## ライセンス
MIT License

Copyright © 2025 dk-butsuri

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.