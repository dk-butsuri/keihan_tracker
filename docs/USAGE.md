# 使い方

[README](../README.md) の続きです。クラスごとの属性は [リファレンス](REFERENCE.md) にあります。

## 1. 基本的なデータ取得（電車）

`async with` を抜けると通信クライアントを閉じます。同期版は下の「同期版」を参照してください。

```python
import asyncio
from keihan_tracker import KHTracker

async def main():
    async with KHTracker() as tracker:
        result = await tracker.fetch_pos()
        station = tracker.station("寝屋川市")  # 17・"KH17"・英語名でも検索可能
        print(result, station.station_name.ja)
        for train, stop in station.upcoming_trains[:5]:
            when = stop.time.strftime("%H:%M") if stop.time else "時刻不明"
            print(when, train.train_type.value, train.destination)

asyncio.run(main())
```

`fetch_pos()` は `FetchResult.UPDATED`（初回・作成時刻が変化）、`UNCHANGED`（位置情報の作成時刻が同じ）、`SKIPPED`（レート制限）を返します。
`UNCHANGED` でも取得と状態の適用は行います。通信失敗は `httpx.HTTPError`、本文を読めなければ `KeihanAPIError` です。
時刻の元は公式の発車時刻 `stationDepTime` で、`"-"` の始発駅などは `None` です。

## 2. リアルタイム監視（ポーリング）

```python
import asyncio
from keihan_tracker import KHTracker

async def main():
    async with KHTracker() as tracker:
        async for result in tracker.watch(60, on_error="continue"):
            print(result, tracker.position_created_at, len(tracker.active_trains))
            # 終了するときは break

asyncio.run(main())
```

`watch()` は取得開始から次の取得開始までの間隔を保ち、処理時間を待ち時間から引きます。
`interval < rate_limit_interval` は反復開始時に `ValueError` です。
既定の `on_error="raise"` は例外を外に出します。`"continue"` は `httpx.HTTPError`・`KeihanAPIError` を
`keihan_tracker` ロガーへ warning で出し、その回は yield せずに続けます。それ以外の例外は常に外に出します。
失敗が続いている間は yield も止まるため、画面の古さは別のタイマーで `position_created_at` を確認してください。

## 3. 遅延を加味した停車駅の時刻の推定

`tracker.eta(train, station)` は現在時刻（JST）で予測し、`EtaResult(time, reason)` を返します。
`time` が `None` なら、`reason` で伏せた理由を区別できます。元の時刻は到着ではなく公式の発車時刻です。

```python
from keihan_tracker import EtaPolicy

station = tracker.station("香里園")  # tracker は fetch_pos() 済み
for train, stop in station.upcoming_trains[:5]:
    prediction = tracker.eta(train, station)
    if prediction.time is not None:
        print(train.wdf, prediction.time.strftime("%H:%M"))
    else:
        print(train.wdf, prediction.reason.value)

# 係数・閾値を変える場合
policy = EtaPolicy(discount_rate=0, stuck_minutes=20)
prediction = tracker.eta(train, station, policy)
```

推定時刻が今より前になることがあります（まだ通っていない駅でも、公式の遅れが実際より小さいとき）。列車はもうすぐ来るので、表示では「まもなく」などにしてください。

取得を自前で行う場合は `estimate_arrival(train, station, now=タイムゾーン付き日時, policy=EtaPolicy())` を使えます。
関数自身は通信も時計の参照もしません。

既定の補正は、Dを公式の遅れ、Hを「定刻＋D」までの残り分数として、D≤10なら
`D - min(D, 0.1 * max(0, H))` を `round` で分単位に丸め、定刻に足します。D>10ならDをそのまま足します。
遅れが1分以上の列車は「H≤60かつD≤10」または「H≤25かつD≤60」の範囲で予測します。

| reason | 意味 |
|:---|:---|
| `OVER_120` | 公式の遅れが120分以上 |
| `GROWING` | 5分以上前と比べ、経過時間の0.7倍以上の速さで遅れが増えている |
| `STUCK` | 始発駅以外で同じ座標に15分より長くいる |
| `BLOCKED_AHEAD` | 対象駅との間に遅れ増加中・長時間停滞の同方向の列車がいる |
| `OUT_OF_WINDOW` | H・Dが予測範囲外 |
| `NO_TIMETABLE` / `NOT_STOPPING` / `NO_TIME` | ダイヤなし・停車しない駅・時刻不明 |
| `PASSED` / `UNKNOWN_POSITION` | 通過済み・現在の停車駅の位置関係が不明 |

前詰まりの判定は、本線・中之島線の走行列車と、京橋〜出町柳（駅番号4〜42）の対象駅に限ります。
120分以上の遅れだけでは前詰まりの原因に数えません。複数の理由の優先順は関数の docstring を参照してください。
`EtaPolicy` の既定値は `discount_rate=0.1`・`discount_max_delay=10`・`windows=((60, 10), (25, 60))`・
`stuck_minutes=15`・`growing_ratio=0.7`・`check_blocked_ahead=True` です。変更できるのは係数と閾値です。
予測の精度を保証するものではありません。signage との比較結果と既知の差は [比較記録](ETA_SIGNAGE.md) を参照してください。

`estimated_delay_minutes` は120分以上の遅れの下限で、到着予測には使いません。
「少なくとも150分遅れ」のような表示に使います（[計算方法と限界](RELIABILITY.md#2時間以上の遅延下限推定)）。

## 4. 京阪バス接近情報の取得
京阪グループ「BUS NAVI」から接近情報を取得します。

**注：利用規約では「商業的二次利用」が禁止されており、私的利用に限ります。**
また使用に際しては、[京阪グループBUS NAVI利用規約](https://busnavi.keihanbus.jp/pc/termsofuse)を必ずご確認ください。

```python
import asyncio
from keihan_tracker import BusClient, BusStatus

async def main():
    async with BusClient() as client:
        for bus in await client.arrivals("京阪香里園", 1):   # バス停名と、のりば番号
            when = bus.expected or bus.scheduled           # 到着予定（定刻＋遅れ）、分からなければ定刻
            print(bus.route, bus.destination, when and when.strftime("%H:%M"), bus.status.value, bus.delay_minutes)

asyncio.run(main())
```

`arrivals()` は接近順の `BusArrival` のリストを返します。時刻は日付つきの `datetime`（JST）です。

| `status` | 意味 | `expected` | `delay_minutes` |
|:---|:---|:---|:---|
| `TRACKED` | 位置を受信している（定時運行中・約N分遅れ） | 到着予定 | 遅れ（定時なら0） |
| `ARRIVED` | 到着済 | 到着予定だった時刻 | `None` |
| `NOT_TRACKED` | 位置を受信していない（定刻だけ分かる） | `None` | `None` |
| `UNKNOWN` | 見たことのない表記（警告を出す） | 公式に到着予定があればその時刻 | `None` |

公式の生の値は `bus.raw`（`BusState`）にあります。`get_info()` で公式の形のまま受け取ることもできます。
便ごとの時刻表は `dgmpl` を `fetch_diagram()` に渡して取ります。

```python
async def main():
    async with BusClient(snapshot_dir="snapshots") as client:
        for bus in await client.arrivals("京阪香里園", 1):
            if bus.dgmpl is not None:
                diagram = await client.fetch_diagram(bus.dgmpl)
                for stop in diagram.stops:
                    print(stop.stop_name, stop.arrival_time, stop.departure_time)

asyncio.run(main())
```

各便の `dgmpl` は、接近情報の HTML 内の `bus_key` から公式ページのボタンと同じ方法で組み立てます。
キーがない場合は `None` です。接近情報を取るだけでは、発着時刻表への追加の通信は発生しません。
`fetch_diagram()` は同じ `dgmpl` の結果を30秒間キャッシュします。
時刻は公式の発着時刻表の値で、遅延を加算しません。`△` が付く推定時刻はフラグで区別します。

`get_khbus_info()` と `fetch_diagram()` も、`KHTracker` と同じく `snapshot_dir` を指定すると、取得した応答をそのまま保存します（デバッグ用）。
ファイル名は `snapshot_dir/YYYYMMDD/HHMMSS.mmm_busstateupd_バス停名_のりば.json.gz` と `..._linediagram_dgmpl.html.gz` です（`dgmpl` の `:` などファイル名に使えない文字は `_` にします）。
前回と内容が同じ場合や、30秒以内のキャッシュを返した場合は保存しません。パースに失敗した応答も保存します。

```python
bus_info = await get_khbus_info("京阪香里園", 1, snapshot_dir="snapshots")
```

## 5. 同期版

```python
from keihan_tracker import KHTrackerSync

with KHTrackerSync() as tracker:
    print(tracker.fetch_pos())
    station = tracker.station("KH18")
    for train in tracker.find_trains(active=True):
        print(train.train_number, train.position)
```

`tracker.watch(60)` は通常の `for` で使えます。同期版は専用のイベントループを保持し、`with` を抜けると閉じます。
Jupyter や `async def` の中では `KHTracker` を `await` で使ってください。
同期版は `.tracker`・`.state`・`.client` と、よく使う読み取り用の属性・検索・予測を明示的に公開しています。
細かな設定は `.tracker` へ渡します（例：`tracker.tracker.rate_limit_interval`）。

## 6. 通信・パース・状態を分けて使う

| 役割 | 公開API / モジュール |
|:---|:---|
| 本文の取得 | `KHClient`（`train.client`）。`get(url)`・`get_if_modified(url)`・`aclose()` |
| JSONの検証 | `parse_select_station`・`parse_transfer_guide`・`parse_positions`・`parse_timetable`（`train.parse`） |
| 公式JSONのモデル | `train.schemas`・`bus.schemas`（Pydantic） |
| 通信・時計なしの状態 | `KHState`（`train.state`） |
| バスのパースだけ | `parse_busstate(text, stop_name, stop_num)`・`parse_diagram(html, dgmpl)`（`bus.parse`） |

自前で取得した本文を使う場合は、次の順に適用します。臨時列車は走行中のものだけ登録するため、位置情報をダイヤより先に渡します。

```python
from keihan_tracker import KHState, parse_select_station, parse_positions, parse_timetable

state = KHState()
state.apply_stations(parse_select_station(stations_text))
state.apply_positions(parse_positions(positions_text))
state.apply_timetable(parse_timetable(timetable_text))
station = state.station("香里園")
```

ここで `*_text` は取得済みの本文（str）です。営業日は位置情報の `fileCreatedTime` から決まり、実時刻は参照しません。
`KHClient` は使い終えたら `await client.aclose()` してください。

## 表示する側（サイネージなど）でやること

ライブラリは公式値と予測結果を返します。画面に何を残すかは、表示する側で判断してください。

- **データの古さ**：取得に失敗し続けても、列車の状態は最後に取得できたときのまま残ります（2026年10月21〜22日には約22時間）。`datetime.now(JST) - tracker.position_created_at` が数分を超えたら（[2. リアルタイム監視](#2-リアルタイム監視ポーリング)）、「情報が古い」と表示するか、列車の表示を消してください。公式の位置情報は1分ごとに作られるため、1分ごとに取得していれば通常は2分以内です。`last_fetch_pos_datetime` は失敗した取得でも更新されるため、使えません。
- **推定時刻を出してよいか**：運行が止まっている間、「遅延分数＋ダイヤの時刻」で求めた到着時刻は実際より早くなります。`eta()` が時刻を伏せたときは、`reason` に応じて「どこで何分止まっているか」などを表示してください（[3. 時刻の推定](#3-遅延を加味した停車駅の時刻の推定)）。

## 注意点

### 書き方の基本

1. **非同期版と同期版を選ぶ**
  - `KHTracker` の取得は `await`、`KHTrackerSync` の取得は通常の呼び出しです。
  - 非同期版は `async with`、同期版は `with` で使うと通信クライアントを閉じられます。

2. **`fetch_pos()` を呼ばないとデータは空**
  - `KHTracker()` をインスタンス化した直後は、駅データも列車データも空です。
  - 最初に `fetch_pos()` を呼び出してください（非同期版は await）。列車位置の定期更新には `watch()`、または自分の取得ループを使います。

3. **駅番号は整数 (int)**
  - 駅番号（ナンバリング）は `KH01` のような文字列ではなく、数字部分の整数 `1` として扱います。辞書のキーも整数です。`station()` は `"KH01"`・`"1"`・日本語名・英語名も受け取ります。

4. **時刻は日本標準時 (JST)**
  - ライブラリ内で扱われる `datetime` オブジェクトには、すべてタイムゾーン情報（`Asia/Tokyo`）が付与されています。
  - 現在時刻と比較する場合は、`datetime.now()` ではなく `datetime.now(ZoneInfo("Asia/Tokyo"))` などと比較しないとエラーになります。

### 公式データの癖

1. **「走行中」と「予定」の列車クラスの違い**
  - **ActiveTrainData**: 現在APIに位置情報が存在する列車です。`stopped_at` (停車中の駅) や `position` (線路上の位置) などのリアルタイムなプロパティを持ちます。
  - **TrainData**: ダイヤ上の予定データ、または運行終了したデータです。これらには現在位置情報がないため、`stopped_at` や `position` などの走行中の列車だけが持つプロパティはありません。
  - `tracker.trains` には両方が混在しています。区別するには `isinstance(train, ActiveTrainData)` を使用してください。
  - **ダイヤに臨時列車として載っている列車は、位置情報に現れるまで登録されません。** 公式のダイヤには、その日に走らない臨時列車も載っているためです（[信頼性ドキュメント](RELIABILITY.md#公式データにない情報確認できていないこと)）。そのため臨時列車は、走り出すまで `trains` / `upcoming_trains` / `find_trains()` に出ません。位置情報に現れた時点で `ActiveTrainData` として登録され、ダイヤの停車駅・時刻も使えるようになります。

2. **ダイヤに無い列車が走ることがある**
  - 運休・運転整理（2026年4月28日の停電によるダイヤ乱れで確認）や一部の臨時列車では、位置情報にはあるがダイヤ（startTimeList）には無い列車が `ActiveTrainData` として登録されます。`has_dia` で判別できます。
  - 種別・行先・方向・`is_special` は位置情報から取得するため正確ですが、停車駅・時刻・始発駅・プレミアムカー・編成番号は分かりません。`start_station` は `DiaNotFoundError`（`ValueError` のサブクラス）を送出し、`stop_stations` は空、`current_or_next_stop`・`next_stop`・`stopped_at` は `None` になります。
  - ダイヤに無い列車は、`StationData.arriving_trains` / `upcoming_trains` / `trains` には含まれません。
  - ダイヤが乱れている間は、ダイヤに載っている列車でも `stop_stations` や `current_or_next_stop` が実際の運行と異なる場合があります。また、停車駅の時刻が不明（`None`）になることがあります。時刻不明の停車は、`StationData.trains` / `upcoming_trains` では時刻順の最後に並びます。

3. **公式APIが壊れたデータを返すことがある**
  - 末尾にNULが付いたJSONや、途中で切れたJSONが返ることがあります（2026年10月7日の朝に4分間で2回確認）。このとき `fetch_pos()` は `KeihanAPIError`（`ValueError` のサブクラス）を送出します。通信の失敗は `httpx.HTTPError` になります。
  - 位置情報が壊れていた場合、列車の状態は前回の取得のまま変わりません。ダイヤが壊れていた場合は、位置情報だけ更新されています。どちらも次の `fetch_pos()` で取り直すので、ループ内で例外を捕まえて呼び続けてください（[2. リアルタイム監視](#2-リアルタイム監視ポーリング)の例）。
  - 全列車の行先が「00」（行先なし）で返ることがあります（23時台に確認）。このとき `ActiveTrainData.destination` はダイヤの終着駅で補い、ダイヤも無ければ `None` になります。

4. **寝屋川市止まりの一部は、着いたあと寝屋川信号場へ回送される**
  - ダイヤでは寝屋川市の後に寝屋川信号場（駅番号99）が続く列車があり、寝屋川市に着いたあとも同じ列車のまま萱島方向へ走ります（2026年10月8日 0:05に確認）。公式の行先は寝屋川市のままです。
  - 寝屋川信号場は駅として登録していないため、この間は `position.ahead` が萱島、`current_or_next_stop` が `None` になります。萱島の `upcoming_trains` には出ません。

## 取得先と負荷への配慮

本ライブラリは、次の公開データを取得します。

| 取得先 | 内容 | 取得する頻度 |
|:---|:---|:---|
| `www.keihan.co.jp/zaisen/select_station.json`, `transferGuideInfo.json` | 駅・路線・乗換 | 最初の `fetch_pos()` のみ |
| `www.keihan.co.jp/zaisen-up/trainPositionList.json` | 列車位置・遅延 | `fetch_pos()` のたび（`rate_limit` 秒に1回まで） |
| `www.keihan.co.jp/zaisen-up/startTimeList.json` | ダイヤ・時刻 | 原則1時間に1回（変わっていなければ `304 Not Modified`） |
| `busnavi.keihanbus.jp/pc/busstateupd` | バスの接近情報 | `get_khbus_info()` のたび（同じバス停は30秒間キャッシュ） |
| `busnavi.keihanbus.jp/pc/linediagram` | 指定したバス1便の発着時刻表 | `fetch_diagram(dgmpl)` を呼んだときだけ（同じ `dgmpl` は30秒間キャッシュ） |

- **名乗ってアクセスします**：リクエストの User-Agent は `keihan_tracker (+https://github.com/dk-butsuri/keihan_tracker)` です。ブラウザのふりはしません。
- **取得の間隔に下限があります**：`rate_limit` は既定で30秒、15秒未満は指定できません。公式の位置情報は1分ごとにしか更新されないため、それより短くしても新しい情報は増えません。
- **読み取りのみです**：ログインや認証を必要とするデータは扱いません。
