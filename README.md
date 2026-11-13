# keihan_tracker

京阪電車 リアルタイム列車位置情報API 非公式Pythonライブラリ

> ⚠️ **注意: 本ライブラリは京阪電車の非公式APIラッパーです。不具合やAPI仕様の変更などの可能性があります。利用は私的かつ自己責任でお願いします。**

**注：京阪グループHPの[利用規約](https://www.keihan.co.jp/terms.html)では、著作権法で認められた私的利用の範囲を超える複製・販売・出版や、営業目的での利用が禁止されています。本ライブラリの利用に際しても、必ず[利用規約](https://www.keihan.co.jp/terms.html)をご確認のうえ、私的利用の範囲内でご利用ください。**

---

## 概要

v2.9 からの変更は [移行ガイド](MIGRATION.md) を参照してください。

京阪電車の公式サイトの列車位置ページが使っているJSONデータをPydanticモデルでバリデートし、Pythonクラスから駅・路線・列車・ダイヤ情報を直感的に操作できるライブラリです。

「現在どこを走っているか」だけでなく、「これからどのような列車が来るか」というダイヤ情報も統合して扱えるため、駅の発車標のようなアプリケーションも簡単に作成できます。

本ライブラリでは、公式APIの `wdfBlockNo` を**WDF番号**と呼びます。WDF番号は列車を区別するためにライブラリがキーにしている番号で、列車番号（「1751号」など）や編成番号とは別です。WDF番号は列車ごとに一意ではなく、別の日には同じ番号が別の列車に割り当てられることがあります。ライブラリは営業日が変わると前日の列車を消すため、日をまたいだ混同は起きません。

### 特徴
*   **ダイヤ・位置情報の統合**: 走行中の列車(`ActiveTrainData`)と、これから走る予定の列車(`TrainData`)を統一的に扱えます。
*   **種別・方向の自動推定**: 予定データ（ダイヤ）には本来含まれていない「列車種別」や「進行方向」を、停車駅リストから自動的に推定します。
*   **相互リンク構造**: 「この駅に来る列車」⇔「この列車の次の駅」といった情報を双方向に参照可能。
*   **型安全**: 公式APIのJSONをPydanticで厳密に定義。エディタの補完が効きます。
*   **高度な位置特定**: 路線図上のグリッド座標から「走行中」か「停車中」かを独自ロジックで判定。
*   **バス情報**: 電車だけでなく、京阪バスの接近情報も取得可能。
*   **レートリミット内蔵**: サーバー負荷を考慮し、30秒/reqのレート制限を内蔵。意図しない高頻度取得を防ぎます。

各値がどこまで信頼できるか（公式データそのままか推定か）と、その検証内容は [信頼性ドキュメント](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/RELIABILITY.md) にまとめています。

## データ構造と関係性
本ライブラリは、**全体管理クラス**、**駅クラス**、**列車クラス**が相互に繋がっています。

```text
[KHTracker / KHTrackerSync] (取得 + KHState)
  │
  ├── .stations (全駅: 読み取り専用 Mapping)
  │     └── [StationData] (駅)
  │            ├── .upcoming_trains ──→ 停車中・今後この駅に停車する [TrainData] のリスト
  │            └── .arriving_trains ──→ 停車中・次にこの駅に停車する [TrainData]のリスト
  │
  └── .trains   (全列車: 読み取り専用 Mapping)
        └── [TrainData|ActiveTrainData] (列車)
               ├── .current_or_next_stop ──→ 停車中の駅か、次に止まる [StationData]
               ├── .stop_stations     ──→ 停車駅リスト
               ├── .train_type        ──→ 種別（予定の列車は停車駅から推定）
               ├── .direction         ──→ 方向（予定の列車は始発・終着から推定）
               └── その他多数...
```

## インストール

```bash
pip install keihan-tracker
```

Python 3.10 以上が必要です。依存パッケージ（pydantic・httpx・tzdata）は一緒にインストールされます。

## 使い方

### 1. 基本的なデータ取得（電車）

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

### 2. リアルタイム監視（ポーリング）

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

### 3. 遅延を加味した停車駅の時刻の推定

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
予測の精度を保証するものではありません。signage との比較結果と既知の差は [比較記録](docs/ETA_SIGNAGE.md) を参照してください。

`estimated_delay_minutes` は120分以上の遅れの下限で、到着予測には使いません。
「少なくとも150分遅れ」のような表示に使います（[計算方法と限界](docs/RELIABILITY.md#2時間以上の遅延下限推定)）。

### 4. 京阪バス接近情報の取得
京阪グループ「BUS NAVI」から接近情報を取得します。

**注：利用規約では「商業的二次利用」が禁止されており、私的利用に限ります。**
また使用に際しては、[京阪グループBUS NAVI利用規約](https://busnavi.keihanbus.jp/pc/termsofuse)を必ずご確認ください。

```python
import asyncio
from keihan_tracker import BusClient

async def main():
    async with BusClient(snapshot_dir="snapshots") as client:
        bus_info = await client.get_info("京阪香里園", 1)
        for bus in bus_info.body.busstates:
            print(bus.busstateprms.destination, bus.delay_minutes, bus.delay_text)
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

### 5. 同期版

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

### 6. 通信・パース・状態を分けて使う

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

*   **データの古さ**：取得に失敗し続けても、列車の状態は最後に取得できたときのまま残ります（2026年10月21〜22日には約22時間）。`datetime.now(JST) - tracker.position_created_at` が数分を超えたら（[使い方の2](#2-リアルタイム監視ポーリング)）、「情報が古い」と表示するか、列車の表示を消してください。公式の位置情報は1分ごとに作られるため、1分ごとに取得していれば通常は2分以内です。`last_fetch_pos_datetime` は失敗した取得でも更新されるため、使えません。
*   **推定時刻を出してよいか**：運行が止まっている間、「遅延分数＋ダイヤの時刻」で求めた到着時刻は実際より早くなります。`eta()` が時刻を伏せたときは、`reason` に応じて「どこで何分止まっているか」などを表示してください（[使い方の3](#3-遅延を加味した停車駅到着時刻の推定)）。

## 知っておくべき仕様・注意点 (ハマりポイント)

### 書き方の基本

1.  **非同期版と同期版を選ぶ**
    *   `KHTracker` の取得は `await`、`KHTrackerSync` の取得は通常の呼び出しです。
    *   非同期版は `async with`、同期版は `with` で使うと通信クライアントを閉じられます。

2.  **`fetch_pos()` を呼ばないとデータは空**
    *   `KHTracker()` をインスタンス化した直後は、駅データも列車データも空です。
    *   最初に `fetch_pos()` を呼び出してください（非同期版は await）。列車位置の定期更新には `watch()`、または自分の取得ループを使います。

3.  **駅番号は整数 (int)**
    *   駅番号（ナンバリング）は `KH01` のような文字列ではなく、数字部分の整数 `1` として扱います。辞書のキーも整数です。`station()` は `"KH01"`・`"1"`・日本語名・英語名も受け取ります。

4.  **時刻は日本標準時 (JST)**
    *   ライブラリ内で扱われる `datetime` オブジェクトには、すべてタイムゾーン情報（`Asia/Tokyo`）が付与されています。
    *   現在時刻と比較する場合は、`datetime.now()` ではなく `datetime.now(ZoneInfo("Asia/Tokyo"))` などと比較しないとエラーになります。

### 公式データの癖

1.  **「走行中」と「予定」の列車クラスの違い**
    *   **ActiveTrainData**: 現在APIに位置情報が存在する列車です。`stopped_at` (停車中の駅) や `position` (線路上の位置) などのリアルタイムなプロパティを持ちます。
    *   **TrainData**: ダイヤ上の予定データ、または運行終了したデータです。これらには現在位置情報がないため、`stopped_at` や `position` などの走行中の列車だけが持つプロパティはありません。
    *   `tracker.trains` には両方が混在しています。区別するには `isinstance(train, ActiveTrainData)` を使用してください。
    *   **ダイヤに臨時列車として載っている列車は、位置情報に現れるまで登録されません。** 公式のダイヤには、その日に走らない臨時列車も載っているためです（[信頼性ドキュメント](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/RELIABILITY.md#公式データにない情報確認できていないこと)）。そのため臨時列車は、走り出すまで `trains` / `upcoming_trains` / `find_trains()` に出ません。位置情報に現れた時点で `ActiveTrainData` として登録され、ダイヤの停車駅・時刻も使えるようになります。

2.  **ダイヤに無い列車が走ることがある**
    *   運休・運転整理（2026年4月28日の停電によるダイヤ乱れで確認）や一部の臨時列車では、位置情報にはあるがダイヤ（startTimeList）には無い列車が `ActiveTrainData` として登録されます。`has_dia` で判別できます。
    *   種別・行先・方向・`is_special` は位置情報から取得するため正確ですが、停車駅・時刻・始発駅・プレミアムカー・編成番号は分かりません。`start_station` は `DiaNotFoundError`（`ValueError` のサブクラス）を送出し、`stop_stations` は空、`current_or_next_stop`・`next_stop`・`stopped_at` は `None` になります。
    *   ダイヤに無い列車は、`StationData.arriving_trains` / `upcoming_trains` / `trains` には含まれません。
    *   ダイヤが乱れている間は、ダイヤに載っている列車でも `stop_stations` や `current_or_next_stop` が実際の運行と異なる場合があります。また、停車駅の時刻が不明（`None`）になることがあります。時刻不明の停車は、`StationData.trains` / `upcoming_trains` では時刻順の最後に並びます。

3.  **公式APIが壊れたデータを返すことがある**
    *   末尾にNULが付いたJSONや、途中で切れたJSONが返ることがあります（2026年10月7日の朝に4分間で2回確認）。このとき `fetch_pos()` は `KeihanAPIError`（`ValueError` のサブクラス）を送出します。通信の失敗は `httpx.HTTPError` になります。
    *   位置情報が壊れていた場合、列車の状態は前回の取得のまま変わりません。ダイヤが壊れていた場合は、位置情報だけ更新されています。どちらも次の `fetch_pos()` で取り直すので、ループ内で例外を捕まえて呼び続けてください（[使い方の2](#2-リアルタイム監視ポーリング)の例）。
    *   全列車の行先が「00」（行先なし）で返ることがあります（23時台に確認）。このとき `ActiveTrainData.destination` はダイヤの終着駅で補い、ダイヤも無ければ `None` になります。

4.  **寝屋川市止まりの一部は、着いたあと寝屋川信号場へ回送される**
    *   ダイヤでは寝屋川市の後に寝屋川信号場（駅番号99）が続く列車があり、寝屋川市に着いたあとも同じ列車のまま萱島方向へ走ります（2026年10月8日 0:05に確認）。公式の行先は寝屋川市のままです。
    *   寝屋川信号場は駅として登録していないため、この間は `position.ahead` が萱島、`current_or_next_stop` が `None` になります。萱島の `upcoming_trains` には出ません。

## v2.9 からの移行

v3 は旧名・旧モジュールを削除しています。書き換え例と挙動の変更は [MIGRATION.md](MIGRATION.md) を参照してください。

## クラスリファレンス

### KHTracker (電車)

KHClient（取得）と KHState（状態）を中に持つ管理クラス。駅・列車・停車駅は dataclass、公式JSONのモデルは Pydantic です。

```text
KHTracker(rate_limit: float = 30, snapshot_dir: str | Path | None = None)
```
`rate_limit`: `fetch_pos()` の最小呼び出し間隔（秒）。デフォルトは30秒で、15秒未満を指定すると `ValueError` になる（公式の位置情報は1分ごとにしか更新されないため）。これよりも高頻度で実行すると、取得処理がスキップされる。

`snapshot_dir`: デバッグ用。指定すると、APIから取得したレスポンスをそのまま `snapshot_dir/YYYYMMDD/HHMMSS.mmm_ファイル名.gz` に保存する。前回と内容が同じ場合は保存しない。パースに失敗したレスポンスも保存されるため、表示がおかしかったときに原因のデータを後から確認できる。古いファイルは自動では削除されないので、必要に応じて手動で整理すること。

*   `stations: Mapping[int, StationData]`: 読み取り専用の駅データ。キーは駅番号の整数値（KH01なら1）。
*   `trains: Mapping[int, TrainData | ActiveTrainData]`: 読み取り専用の全列車データ（走行中・予定・終了含む）。キーはWDF番号。
*   `active_trains: dict[int, ActiveTrainData]`: 現在走行中の列車データのみを抽出した辞書
*   `date: datetime.date`: 現在の営業日（0〜4時の深夜帯は前日扱い）
*   `max_delay_train: Optional[ActiveTrainData]`: 最も遅延している列車（0分の場合はNoneを返す）
*   `max_delay_minutes: int`: 現在の最大遅延分数
*   `position_created_at: Optional[datetime]`: 手元の位置情報の作成時刻（`fileCreatedTime`）。取得に失敗しても変わらないため、データの古さはこれで判断する（[表示する側でやること](#表示する側サイネージなどでやること)）
*   `last_fetch_pos_datetime: Optional[datetime]`: 最後に `fetch_pos()` を実行した時刻。失敗した取得でも更新されるため、データの古さの判断には使えない
*   `rate_limit_interval: float`: 現在設定されているレートリミット間隔（秒）

#### 主要メソッド

*   `async fetch_pos() -> FetchResult`: **[重要]** 最新の列車位置・遅延情報をAPIから取得し、インスタンス内のデータを更新します。公式APIが壊れたデータを返すと `KeihanAPIError` を送出します。
*   `find_trains(...)`: 条件に合致する列車をリストで返します。全引数はオプションで、省略した項目は絞り込み対象外となります。
*   `delay_growing_trains(line=None, window=timedelta(minutes=5), ratio=0.9)`: `is_delay_growing(window, ratio)` が `True` の走行中の列車を返します。運行が止まっているかの手がかりになります。`line` を指定すると、今その路線にいる列車だけを返します。実際に止まったときに当てはまった本数は、[信頼性ドキュメント](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/RELIABILITY.md#運行が止まったとき)にあります。

*   `async aclose()` / `async with`: 通信クライアントを閉じます。
*   `watch(interval=60, *, on_error="raise")`: 取得結果を返す非同期イテレーター。
*   `station(key: int | str) -> StationData`: 駅番号・KH番号・日本語名・英語名で検索。大文字小文字と前後の空白は無視します。未取得・不明なら KeyError、同名が複数なら ValueError。
*   `eta(train, station, policy=None) -> EtaResult`: 現在時刻で予測します。
*   `.state: KHState` / `.client: KHClient`: 状態と取得を個別に使えます。

`find_trains(active=True, ...)` の戻り値は `list[ActiveTrainData]` です。その他は `list[TrainData]` で、走行中の列車が混在する場合があります。

`find_trains(...)` の引数：

| 引数 | 型 | 説明 |
|:---|:---|:---|
| `active` | `bool` | 走行中か（`ActiveTrainData` か） |
| `train_type` | `TrainType` | 列車種別 |
| `direction` | `"up" \| "down"` | 進行方向。予定の列車は始発・終着から推定した方向 |
| `is_special` | `bool` | 臨時列車かどうか（走行中の列車のみ。指定すると走行中でない列車は該当しない） |
| `train_number` | `str` | 列車番号（例: `"1051"`）（走行中の列車のみ。指定すると走行中でない列車は該当しない） |
| `has_premiumcar` | `bool` | プレミアムカー連結の有無 |
| `destination` | `StationData` | 行先駅 |
| `current_or_next_stop` | `StationData` | 停車中の駅か次の停車駅（走行中の列車のみ。指定すると走行中でない列車は該当しない） |
| `min_delay` | `int` | この分数を**超える**遅延のみ対象 |
| `max_delay` | `int` | この分数**未満**の遅延のみ対象 |
| `status` | `TrainStatus` | `STOPPED`・`PASSING`・`BETWEEN`・`UNKNOWN`（走行中の列車のみ） |
| `line` | `LineLiteral` | 路線（例: `"宇治線"`）。走行中の列車は今いる路線、走行中でない列車は走ったときの路線か、停車駅から推定した路線 |

### KHTrackerSync / KHState / BusClient

`KHTrackerSync(rate_limit=30, snapshot_dir=None)` は `fetch_pos()`・`watch()`・`close()` と通常の `with` を使います。
`.tracker` は中の KHTracker、`.state`・`.client` はその状態・取得クライアントです。
`trains`・`stations`・`active_trains`・`date`・`position_created_at`・`find_trains`・`delay_growing_trains`・`station`・`eta` を委譲します。

`KHState(date=None)` は通信も時計も持ちません。`apply_stations()` → `apply_positions()` → `apply_timetable()` で更新し、`station()` などで読み取ります。

`BusClient(snapshot_dir=None)` は `get_info(stop_name, stop_num=1)`・`fetch_diagram(dgmpl)`・`aclose()` と `async with` を使います。
クライアントと30秒キャッシュはインスタンスごとです。同じイベントループで使い続けてください。
`get_khbus_info()`・モジュール関数の `fetch_diagram()` は直下からも import でき、共通キャッシュを使いながら通信ごとにクライアントを開閉します。

### 列車データの構造 (TrainData と ActiveTrainData)

本ライブラリでは、列車の状態によって2つのクラスが使われます。
`ActiveTrainData` は `TrainData` を継承しており、**「TrainDataの全情報 ＋ リアルタイム位置情報」** を持っています。

判定する際は`isinstance()`関数を用いると、型厳密に、そしてエディターで入力候補が使用できます。

| 項目 | TrainData (予定・終了) | ActiveTrainData (走行中) | 備考 |
| :--- | :--- | :--- | :--- |
| **基本情報** | ◎ | ◎ | WDF番号, 編成, 行先など |
| **ダイヤ情報** | ◎ | ◎ | 停車駅リスト, 発車時刻 |
| **列車種別** | △ **[推定]** | ◎ | 予定/終了の場合、停車駅から推定、ライナーは識別不可 |
| **進行方向** | △ **[推定]** | ◎ | 予定/終了の場合、始発・終着から推定 |
| **現在位置** | × | **○** **[一部推定]** | 座標, 停車中判定 |
| **遅延情報** | × | **◎ / 推定** | 公式値 `delay_minutes` と、下限推定 `estimated_delay_minutes` |

#### TrainData (基底クラス)
全ての列車のベースとなるクラスです。主にダイヤ情報（静的な予定）を保持します。
*   `master: KHState`: 駅・列車・履歴が所属する状態。通信機能は持ちません
*   `wdf: int`: WDF番号
*   `date: datetime.date`: この列車が属する営業日。0〜4時の深夜帯は前日扱いとハードコーディングされています。
*   `destination: StationData`: 行先駅
*   `start_station: StationData`: 始発駅。ダイヤに無い列車では `DiaNotFoundError` を送出します
*   `has_dia: bool`: ダイヤ（停車駅・時刻）があるか
*   `stop_stations: list[StopStationData]`: 全停車駅のリスト（走行順）
*   `get_stop(station: StationData) -> StopStationData | None`: その駅の停車駅データ。通過・経路外なら None
*   `station in train`: その列車が駅に停車するか。ダイヤなし・通過は False
*   `get_stop_time(station: StationData) -> Optional[datetime]`: その駅の予定発車時刻（公式の stationDepTime）。停車しない駅や、時刻が不明の駅では `None`
*   `route_stations: list[StopStationData]`: 停車・通過駅のリスト（走行順。一部の通過駅のみが含まれる）
*   `has_premiumcar: Optional[bool]`: プレミアムカーがあるか
*   `delay_minutes: int` 遅延分数（`TrainData` では常に0）
*   `train_formation: Optional[int]` 列車編成（3003など）
*   `train_type: TrainType`: **[推定]** 停車駅パターンから推定された種別
*   `direction: Literal["up","down"]`: **[推定]** 始発・終着から推定された方向
*   `line: LineLiteral`: **[推定]** 列車の走行路線（"京阪本線・鴨東線", "宇治線" など）
*   `history: list[TrainHistoryRecord]`: その日、走っている間の位置情報ごとの値（古い順）。走行中なら最新の1件は今の値と一致します。運行を終えて `TrainData` に戻っても残り、一度消えた列車がまた走り出すと続きから加わります
*   `was_active: bool`: その日、走っているのを観測したことがあるか（`history` が空でないか）。走行中の列車も `True` です。運行を終えたことは保証しません（止まったまま位置情報から消えることがあります）。観測を始める前に走った列車や、取得が途切れていた間に走った列車は `False` です

列車の `==`・hash は `(date, wdf)` で比べます。走行中・終了後の別オブジェクトでも、同じ営業日の同じWDF番号なら等しく、set・辞書のキーにできます。
走り出し・運行終了でオブジェクトが変わるため、更新後は `tracker.trains[wdf]` を引き直します。

なお、`ActiveTrainData` が運行終了後に `TrainData` に降格した際、`direction`・`train_type`・`destination`・`line` は引き継がれます。

#### ActiveTrainData (継承クラス)
現在走行中の列車です。`TrainData` に加え、以下の**リアルタイム情報**を持ちます。
*   `status: TrainStatus`: `STOPPED`（停車中）・`PASSING`（通過駅の座標）・`BETWEEN`（駅間）・`UNKNOWN`（停車駅が不明）
*   `stopped_at: Optional[StationData]`: 停車中の駅。駅の位置にいても、その駅を通過する場合や、停車駅が分からない（ダイヤに無い列車など）ときは `None`
*   `next_stop: Optional[StationData]`: この先で次に停車する駅。**停車中の駅は含みません**。終点にいるときなど、分からなければ `None`
*   `current_or_next_stop: Optional[StationData]`: 停車中ならその駅、でなければ次に停車する駅（発車標用）
*   `position: TrainPosition`: 座標から分かる線路上の位置。停車か通過かはダイヤによるため含みません
    *   `station`: 駅の位置座標にいるならその駅（停車中・通過中）。駅間なら `None`
    *   `behind`: 駅間なら後ろの駅。駅にいるなら `None`
    *   `ahead`: 進行方向の次の駅。路線の端の駅にいるなら `None`
    *   `line`: 走っている路線
*   `destination: Optional[StationData]`: 行先駅。位置情報の行先が「00」（行先なし）のときはダイヤの終着駅、ダイヤも無ければ `None`
*   `delay_minutes: int`: 公式の遅延分数（定刻なら0、「2時間以上」なら120）
*   `estimated_delay_minutes: int`: 遅延分数の下限推定。`delay_minutes < 120` なら公式値をそのまま返し、120分以上なら予定発車時刻と位置情報・履歴から求めた候補との最大値を返します。次の停車駅が不明なら公式値を返し、時刻・履歴が足りない候補は使いません。`delay_minutes` や履歴の公式値は変更しません。詳しくは[計算方法と限界](docs/RELIABILITY.md#2時間以上の遅延下限推定)を参照してください
*   `delay_text: MultiLang`: 遅延テキスト（「約5分」など各言語で）
*   `train_number: str`: 列車番号 (例: "1051"（号）など)
*   `cars: int`: 車両数
*   `location_col`, `location_row`: zaisen上のグリッド座標
*   `is_special: bool`: 臨時列車かどうか
*   `is_at_start_station: Optional[bool]`: 現在、始発駅に停車中かどうか（停車駅が分からないときは `None`）
*   `staying_time: datetime.timedelta`: 今の座標に留まっている時間。駅でも駅間でも、座標が変わらなければ増える。最後に取得した位置情報の作成時刻で測るため、取得の間やAPIが止まっている間は増えない。座標が粗いため走行中でも数分は増えることがあり、出現直後はそれ以前の時間を含まない
*   `position_since: Optional[datetime.datetime]`: 今の座標に来た時刻（位置情報の作成時刻）
*   `delay_growth_rate(window=timedelta(minutes=5)) -> Optional[float]`: 遅延の増える速さ（分/分）。最新の位置情報と、それより `window` 以上前で最も新しい位置情報の時点を比べ、遅延の増えを経過時間で割ります。1.0 なら、止まっているのと同じ速さで遅延が増えています。遅れを取り戻しているときはマイナスです。比べられる記録が無い（現れてから `window` が経っていない）ときは `None` です。遅延が「2時間以上」になると `delay_minutes` は120で止まるため、止まったままでも0になります
*   `is_delay_growing(window=timedelta(minutes=5), ratio=0.9) -> bool`: `delay_growth_rate(window)` が `ratio` 以上か。止まっている列車は待たされた時間がそのまま遅延になるため、当てはまりやすくなります

### StationData
駅を表すクラス。

*   `master: KHState`: 所属する状態
*   `station_number: int`: 駅番号 (例: 1)
*   `station_name: MultiLang`: 駅名（.ja, .en 等）
*   `line: set[LineLiteral]`: 所属路線（例: `{"京阪本線・鴨東線", "中之島線"}`）
*   `transfer: StationConnections`: 乗換情報。JR・地下鉄・モノレールなどへの乗り入れ路線を保持します。
*   `arriving_trains: list[ActiveTrainData]`: 停車中、もしくは**次に**停車する走行中列車のリスト
*   `upcoming_trains: list[tuple[TrainData | ActiveTrainData, StopStationData]]`: 停車中、もしくは**今後**停車する全列車とその到着時刻のリスト（時刻順）。
    *   走行中の列車は、停車駅の並びで次の停車駅以降にこの駅があれば含みます。
    *   走行中でない列車は、`was_active` でなく、この駅の予定時刻が、その営業日に最初に位置情報を取れた時刻より後なら含みます（時刻不明なら、その先の時刻が分かっている停車駅で判定し、どれも不明なら含みます）。予定時刻を過ぎても、走り出さないかぎり含み続けます。運転見合わせ中は遅れて来るかもしれず、運休になったかは公式データから分からないためです。予定時刻をどれだけ過ぎたら出さないかは、使う側で決めてください。
*   `fastest_arrivals: dict[StationData, tuple[TrainData | ActiveTrainData, datetime]]`: この駅から乗れる列車（`upcoming_trains`）で、この先の各駅に**最も早く着く列車**とその到着時刻。「〇〇には先着」の表示に使えます。
    *   乗り換えは考えません。ここでの到着時刻も中身は公式の発車時刻です。ダイヤの予定時刻で、遅延を足しません。
    *   時刻不明の停車駅は使いません。到着時刻が同じなら、この駅を先に出る列車を返します。この駅自身はキーに含みません。
    ```python
    station = tracker.stations[18]  # 香里園
    fastest = station.fastest_arrivals
    for train, stop in station.upcoming_trains[:5]:
        first = [s for s, (t, _) in fastest.items() if t is train]
        names = "・".join(str(s) for s in first[:3])
        when = stop.time.strftime("%H:%M") if stop.time else "時刻不明"
        print(when, train.train_type.value, train.destination, f"（{names}に先着）" if names else "")
    ```
*   `trains: list[tuple[TrainData | ActiveTrainData, StopStationData]]`: **過去・現在・未来すべて**の停車列車リスト（時刻順）。`upcoming_trains` が「これから停車する列車」のみを返すのに対し、こちらはすでに通過済みの列車も含みます。発車標ではなく運行履歴や全停車情報が必要な場合に使用します。

いずれもダイヤの停車駅から探すため、ダイヤに無い列車（`has_dia` が `False`）は含まれません。

### StopStationData
列車の停車・通過駅を表すクラス。train.stop_stations や station.upcoming_trains の戻り値に含まれます。
   * station: StationData: 駅
   * time: Optional[datetime]: 公式の stationDepTime（発車時刻）。始発駅が "-" のときや時刻不明なら None。"-" がないダイヤでは先頭駅の既存の時刻を保持します（[始発時刻の調査](docs/START_TIMES.md)）
   * train: Optional[TrainData]: この停車駅を持つ列車への逆参照。ダイヤ登録済みなら現在の列車を指します
   * is_stop: bool: 停車するかどうか（通過駅ならFalse）
   * is_start: bool: この駅が始発駅かどうか
   * is_final: bool: この駅が終着駅かどうか

`StopStationData` の順序比較は時刻だけを比べ、時刻不明ならすべて False です。`==` は同一性で、同時刻の別の停車駅は等しくありません。
`b - a` は時刻差の timedelta（どちらか不明なら None）を返します。

### TrainHistoryRecord
走行中の列車の、ある位置情報の時点での値です。`TrainData.history` の1件です。1日分を残すため、`BaseModel` ではなく `dataclass`（`frozen=True, slots=True`）です。

   * master: KHState: 所属する状態
   * file_created_time: datetime: その位置情報の作成時刻（`fileCreatedTime`。取得した時刻ではありません）
   * location_col, location_row: int: 座標
   * delay_minutes: int: 遅延分数
   * direction: Literal["up","down"]: 方向
   * destination: Optional[StationData]: 行先駅
   * line: LineLiteral: その時点で走っていた路線
   * position: TrainPosition: その時点の線路上の位置（`ActiveTrainData.position` と同じ）

### BusLocationResponse / BusStatePrms (バス)
バス接近情報のレスポンスモデル。

*   `BusLocationResponse.body.busstates`: `BusState` のリスト
*   `BusState.dgmpl: Optional[str]`: この便の `fetch_diagram(dgmpl)` に渡す値。HTML にキーがなければ `None`
*   `BusState.scheduled_time: Optional[str]`: 定刻（`HH:MM`）
*   `BusState.delay_text: Optional[str]`: 見出しの括弧内の表記（「定時運行中」「約1分遅れ」「到着済」）。位置を受信していない便は括弧が無いため `None`
*   `BusState.delay_minutes: Optional[int]`: 遅れ（分）。定時運行中なら0。未受信・到着済、読めない表記（警告を出す）なら `None`。公式の「約N分遅れ」の値で、`busstateprms.timetable` の「到着予定」は定刻にこの遅れを足した時刻
*   `BusState.busstateprms`: 詳細情報オブジェクト
    *   `route: str`: 系統番号 (例: "[22]")
    *   `destination: str`: 行先
    *   `status: str`: 運行状況テキスト
    *   `timetable: str`: 到着予定時刻など
    *   `lat`, `lon`: バスの現在座標

### BusDiagram / BusDiagramStop (バスの発着時刻表)

*   `BusDiagram.dgmpl: str`: 問い合わせに使った値
*   `BusDiagram.stops: list[BusDiagramStop]`: 運行順の停留所。同名の停留所を複数回通る場合も別々に保持
    *   `stop_name: str`: 公式の表示名（`〔京阪バス〕` などを含む）
    *   `arrival_time: Optional[str]`: 着時刻。例 `"17:18"`。始発などで記載がない場合は `None`
    *   `departure_time: Optional[str]`: 発時刻。例 `"17:20"`。終点などで記載がない場合は `None`
    *   `arrival_is_estimated: bool` / `departure_is_estimated: bool`: 公式の時刻に `△` が付いているか

時刻は日付を含まない文字列です。24時以降の表記も保持します。
通信の失敗は `httpx.HTTPError`、発着時刻表がない場合や HTML の形式が想定と違う場合は `KeihanAPIError` です。

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
`<`・`<=`・`>`・`>=` は下の表の順位（低い順）で比較します。臨時列車を含む比較は常に False で、sorted() に混ぜると並びが崩れます。
`==` は従来どおり文字列とも等しく、`TrainType.LTD_EXP == "特急"` は True です。

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

> **注**: ダイヤ上のデータからライナーを識別することは仕様上困難なため、`TrainData.train_type` では `LINER` ではなく `LTD_EXP`（特急）と推定されます。樟葉始発のライナーは、停車駅が同じ `COMMUTER_RAPID_EXP`（通勤快急）と推定されます。`ActiveTrainData` ではAPIから正確な種別が返ります。

## 取得先と負荷への配慮

本ライブラリは、次の公開データを取得します。

| 取得先 | 内容 | 取得する頻度 |
|:---|:---|:---|
| `www.keihan.co.jp/zaisen/select_station.json`, `transferGuideInfo.json` | 駅・路線・乗換 | 最初の `fetch_pos()` のみ |
| `www.keihan.co.jp/zaisen-up/trainPositionList.json` | 列車位置・遅延 | `fetch_pos()` のたび（`rate_limit` 秒に1回まで） |
| `www.keihan.co.jp/zaisen-up/startTimeList.json` | ダイヤ・時刻 | 原則1時間に1回（変わっていなければ `304 Not Modified`） |
| `busnavi.keihanbus.jp/pc/busstateupd` | バスの接近情報 | `get_khbus_info()` のたび（同じバス停は30秒間キャッシュ） |
| `busnavi.keihanbus.jp/pc/linediagram` | 指定したバス1便の発着時刻表 | `fetch_diagram(dgmpl)` を呼んだときだけ（同じ `dgmpl` は30秒間キャッシュ） |

*   **名乗ってアクセスします**：リクエストの User-Agent は `keihan_tracker (+https://github.com/dk-butsuri/keihan_tracker)` です。ブラウザのふりはしません。
*   **取得の間隔に下限があります**：`rate_limit` は既定で30秒、15秒未満は指定できません。公式の位置情報は1分ごとにしか更新されないため、それより短くしても新しい情報は増えません。
*   **読み取りのみです**：ログインや認証を必要とするデータは扱いません。

## 関連プロジェクト

*   [train_operation_info](https://github.com/dk-butsuri/train_operation_info)：各社の公式サイトの運行情報を取得するライブラリです。本ライブラリの運行情報の機能（`get_ekispert_delay`・`DelayLine`）は v2.7.0 で削除したため、運行情報はこちらを使ってください。

## ライセンス
MIT License

Copyright © 2026 dk-butsuri
