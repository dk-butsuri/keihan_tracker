# リファレンス

各クラスの属性とメソッドです。使い方の例は [使い方](USAGE.md) にあります。

## KHTracker (電車)

KHClient（取得）と KHState（状態）を中に持つ管理クラス。駅・列車・停車駅は dataclass、公式JSONのモデルは Pydantic です。

```text
KHTracker(rate_limit: float = 30, snapshot_dir: str | Path | None = None)
```
`rate_limit`: `fetch_pos()` の最小呼び出し間隔（秒）。デフォルトは30秒で、15秒未満を指定すると `ValueError` になる（公式の位置情報は1分ごとにしか更新されないため）。これよりも高頻度で実行すると、取得処理がスキップされる。

`snapshot_dir`: デバッグ用。指定すると、APIから取得したレスポンスをそのまま `snapshot_dir/YYYYMMDD/HHMMSS.mmm_ファイル名.gz` に保存する。前回と内容が同じ場合は保存しない。パースに失敗したレスポンスも保存されるため、表示がおかしかったときに原因のデータを後から確認できる。古いファイルは自動では削除されないので、必要に応じて手動で整理すること。

- `stations: Mapping[int, StationData]`: 読み取り専用の駅データ。キーは駅番号の整数値（KH01なら1）。
- `trains: Mapping[int, TrainData | ActiveTrainData]`: 読み取り専用の全列車データ（走行中・予定・終了含む）。キーはWDF番号。
- `active_trains: dict[int, ActiveTrainData]`: 現在走行中の列車データのみを抽出した辞書
- `date: datetime.date`: 現在の営業日（0〜4時の深夜帯は前日扱い）
- `max_delay_train: Optional[ActiveTrainData]`: 最も遅延している列車（0分の場合はNoneを返す）
- `max_delay_minutes: int`: 現在の最大遅延分数
- `position_created_at: Optional[datetime]`: 手元の位置情報の作成時刻（`fileCreatedTime`）。取得に失敗しても変わらないため、データの古さはこれで判断する（[表示する側でやること](USAGE.md#表示する側サイネージなどでやること)）
- `last_fetch_pos_datetime: Optional[datetime]`: 最後に `fetch_pos()` を実行した時刻。失敗した取得でも更新されるため、データの古さの判断には使えない
- `rate_limit_interval: float`: 現在設定されているレートリミット間隔（秒）

### 主要メソッド

- `async fetch_pos() -> FetchResult`: **[重要]** 最新の列車位置・遅延情報をAPIから取得し、インスタンス内のデータを更新します。公式APIが壊れたデータを返すと `KeihanAPIError` を送出します。
- `find_trains(...)`: 条件に合致する列車をリストで返します。全引数はオプションで、省略した項目は絞り込み対象外となります。
- `delay_growing_trains(line=None, window=timedelta(minutes=5), ratio=0.9)`: `is_delay_growing(window, ratio)` が `True` の走行中の列車を返します。運行が止まっているかの手がかりになります。`line` を指定すると、今その路線にいる列車だけを返します。実際に止まったときに当てはまった本数は、[信頼性ドキュメント](RELIABILITY.md#運行が止まったとき)にあります。

- `async aclose()` / `async with`: 通信クライアントを閉じます。
- `watch(interval=60, *, on_error="raise")`: 取得結果を返す非同期イテレーター。
- `station(key: int | str) -> StationData`: 駅番号・KH番号・日本語名・英語名で検索。大文字小文字と前後の空白は無視します。未取得・不明なら KeyError、同名が複数なら ValueError。
- `eta(train, station, policy=None) -> EtaResult`: 現在時刻で予測します。
- `.state: KHState` / `.client: KHClient`: 状態と取得を個別に使えます。

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

## KHTrackerSync / KHState / BusClient

`KHTrackerSync(rate_limit=30, snapshot_dir=None)` は `fetch_pos()`・`watch()`・`close()` と通常の `with` を使います。
`.tracker` は中の KHTracker、`.state`・`.client` はその状態・取得クライアントです。
`trains`・`stations`・`active_trains`・`date`・`position_created_at`・`find_trains`・`delay_growing_trains`・`station`・`eta` を委譲します。

`KHState(date=None)` は通信も時計も持ちません。`apply_stations()` → `apply_positions()` → `apply_timetable()` で更新し、`station()` などで読み取ります。

`BusClient(snapshot_dir=None)` は `arrivals(stop_name, stop_num=1)`・`get_info(stop_name, stop_num=1)`・`fetch_diagram(dgmpl)`・`aclose()` と `async with` を使います。
クライアントと30秒キャッシュはインスタンスごとです。同じイベントループで使い続けてください。
`get_khbus_info()`・モジュール関数の `fetch_diagram()` は直下からも import でき、共通キャッシュを使いながら通信ごとにクライアントを開閉します。

## 列車データの構造 (TrainData と ActiveTrainData)

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

### TrainData (基底クラス)
全ての列車のベースとなるクラスです。主にダイヤ情報（静的な予定）を保持します。
- `master: KHState`: 駅・列車・履歴が所属する状態。通信機能は持ちません
- `wdf: int`: WDF番号
- `date: datetime.date`: この列車が属する営業日。0〜4時の深夜帯は前日扱いとハードコーディングされています。
- `destination: StationData`: 行先駅
- `start_station: StationData`: 始発駅。ダイヤに無い列車では `DiaNotFoundError` を送出します
- `has_dia: bool`: ダイヤ（停車駅・時刻）があるか
- `stop_stations: list[StopStationData]`: 全停車駅のリスト（走行順）
- `get_stop(station: StationData) -> StopStationData | None`: その駅の停車駅データ。通過・経路外なら None
- `station in train`: その列車が駅に停車するか。ダイヤなし・通過は False
- `get_stop_time(station: StationData) -> Optional[datetime]`: その駅の予定発車時刻（公式の stationDepTime）。停車しない駅や、時刻が不明の駅では `None`
- `route_stations: list[StopStationData]`: 停車・通過駅のリスト（走行順。一部の通過駅のみが含まれる）
- `has_premiumcar: Optional[bool]`: プレミアムカーがあるか
- `delay_minutes: int` 遅延分数（`TrainData` では常に0）
- `train_formation: Optional[int]` 列車編成（3003など）
- `train_type: TrainType`: **[推定]** 停車駅パターンから推定された種別
- `direction: Literal["up","down"]`: **[推定]** 始発・終着から推定された方向
- `line: LineLiteral`: **[推定]** 列車の走行路線（"京阪本線・鴨東線", "宇治線" など）
- `history: list[TrainHistoryRecord]`: その日、走っている間の位置情報ごとの値（古い順）。走行中なら最新の1件は今の値と一致します。運行を終えて `TrainData` に戻っても残り、一度消えた列車がまた走り出すと続きから加わります
- `was_active: bool`: その日、走っているのを観測したことがあるか（`history` が空でないか）。走行中の列車も `True` です。運行を終えたことは保証しません（止まったまま位置情報から消えることがあります）。観測を始める前に走った列車や、取得が途切れていた間に走った列車は `False` です

列車の `==`・hash は `(date, wdf)` で比べます。走行中・終了後の別オブジェクトでも、同じ営業日の同じWDF番号なら等しく、set・辞書のキーにできます。
走り出し・運行終了でオブジェクトが変わるため、更新後は `tracker.trains[wdf]` を引き直します。

なお、`ActiveTrainData` が運行終了後に `TrainData` に降格した際、`direction`・`train_type`・`destination`・`line` は引き継がれます。

### ActiveTrainData (継承クラス)
現在走行中の列車です。`TrainData` に加え、以下の**リアルタイム情報**を持ちます。
- `status: TrainStatus`: `STOPPED`（停車中）・`PASSING`（通過駅の座標）・`BETWEEN`（駅間）・`UNKNOWN`（停車駅が不明）
- `stopped_at: Optional[StationData]`: 停車中の駅。駅の位置にいても、その駅を通過する場合や、停車駅が分からない（ダイヤに無い列車など）ときは `None`
- `next_stop: Optional[StationData]`: この先で次に停車する駅。**停車中の駅は含みません**。終点にいるときなど、分からなければ `None`
- `current_or_next_stop: Optional[StationData]`: 停車中ならその駅、でなければ次に停車する駅（発車標用）
- `position: TrainPosition`: 座標から分かる線路上の位置。停車か通過かはダイヤによるため含みません
  - `station`: 駅の位置座標にいるならその駅（停車中・通過中）。駅間なら `None`
  - `behind`: 駅間なら後ろの駅。駅にいるなら `None`
  - `ahead`: 進行方向の次の駅。路線の端の駅にいるなら `None`
  - `line`: 走っている路線
- `destination: Optional[StationData]`: 行先駅。位置情報の行先が「00」（行先なし）のときはダイヤの終着駅、ダイヤも無ければ `None`
- `delay_minutes: int`: 公式の遅延分数（定刻なら0、「2時間以上」なら120）
- `estimated_delay_minutes: int`: 遅延分数の下限推定。`delay_minutes < 120` なら公式値をそのまま返し、120分以上なら予定発車時刻と位置情報・履歴から求めた候補との最大値を返します。次の停車駅が不明なら公式値を返し、時刻・履歴が足りない候補は使いません。`delay_minutes` や履歴の公式値は変更しません。詳しくは[計算方法と限界](RELIABILITY.md#2時間以上の遅延下限推定)を参照してください
- `delay_text: MultiLang`: 遅延テキスト（「約5分」など各言語で）
- `train_number: str`: 列車番号 (例: "1051"（号）など)
- `cars: int`: 車両数
- `location_col`, `location_row`: zaisen上のグリッド座標
- `is_special: bool`: 臨時列車かどうか
- `is_at_start_station: Optional[bool]`: 現在、始発駅に停車中かどうか（停車駅が分からないときは `None`）
- `staying_time: datetime.timedelta`: 今の座標に留まっている時間。駅でも駅間でも、座標が変わらなければ増える。最後に取得した位置情報の作成時刻で測るため、取得の間やAPIが止まっている間は増えない。座標が粗いため走行中でも数分は増えることがあり、出現直後はそれ以前の時間を含まない
- `position_since: Optional[datetime.datetime]`: 今の座標に来た時刻（位置情報の作成時刻）
- `delay_growth_rate(window=timedelta(minutes=5)) -> Optional[float]`: 遅延の増える速さ（分/分）。最新の位置情報と、それより `window` 以上前で最も新しい位置情報の時点を比べ、遅延の増えを経過時間で割ります。1.0 なら、止まっているのと同じ速さで遅延が増えています。遅れを取り戻しているときはマイナスです。比べられる記録が無い（現れてから `window` が経っていない）ときは `None` です。遅延が「2時間以上」になると `delay_minutes` は120で止まるため、止まったままでも0になります
- `is_delay_growing(window=timedelta(minutes=5), ratio=0.9) -> bool`: `delay_growth_rate(window)` が `ratio` 以上か。止まっている列車は待たされた時間がそのまま遅延になるため、当てはまりやすくなります

## StationData
駅を表すクラス。

- `master: KHState`: 所属する状態
- `station_number: int`: 駅番号 (例: 1)
- `station_name: MultiLang`: 駅名（.ja, .en 等）
- `line: set[LineLiteral]`: 所属路線（例: `{"京阪本線・鴨東線", "中之島線"}`）
- `transfer: StationConnections`: 乗換情報。JR・地下鉄・モノレールなどへの乗り入れ路線を保持します。
- `arriving_trains: list[ActiveTrainData]`: 停車中、もしくは**次に**停車する走行中列車のリスト
- `upcoming_trains: list[tuple[TrainData | ActiveTrainData, StopStationData]]`: 停車中、もしくは**今後**停車する全列車とその到着時刻のリスト（時刻順）。
  - 走行中の列車は、停車駅の並びで次の停車駅以降にこの駅があれば含みます。
  - 走行中でない列車は、`was_active` でなく、この駅の予定時刻が、その営業日に最初に位置情報を取れた時刻より後なら含みます（時刻不明なら、その先の時刻が分かっている停車駅で判定し、どれも不明なら含みます）。予定時刻を過ぎても、走り出さないかぎり含み続けます。運転見合わせ中は遅れて来るかもしれず、運休になったかは公式データから分からないためです。予定時刻をどれだけ過ぎたら出さないかは、使う側で決めてください。
- `fastest_arrivals: dict[StationData, tuple[TrainData | ActiveTrainData, datetime]]`: この駅から乗れる列車（`upcoming_trains`）で、この先の各駅に**最も早く着く列車**とその到着時刻。「〇〇には先着」の表示に使えます。
  - 乗り換えは考えません。ここでの到着時刻も中身は公式の発車時刻です。ダイヤの予定時刻で、遅延を足しません。
  - 時刻不明の停車駅は使いません。到着時刻が同じなら、この駅を先に出る列車を返します。この駅自身はキーに含みません。
    ```python
    station = tracker.stations[18]  # 香里園
    fastest = station.fastest_arrivals
    for train, stop in station.upcoming_trains[:5]:
        first = [s for s, (t, _) in fastest.items() if t is train]
        names = "・".join(str(s) for s in first[:3])
        when = stop.time.strftime("%H:%M") if stop.time else "時刻不明"
        print(when, train.train_type.value, train.destination, f"（{names}に先着）" if names else "")
    ```
- `trains: list[tuple[TrainData | ActiveTrainData, StopStationData]]`: **過去・現在・未来すべて**の停車列車リスト（時刻順）。`upcoming_trains` が「これから停車する列車」のみを返すのに対し、こちらはすでに通過済みの列車も含みます。発車標ではなく運行履歴や全停車情報が必要な場合に使用します。

いずれもダイヤの停車駅から探すため、ダイヤに無い列車（`has_dia` が `False`）は含まれません。

## StopStationData
列車の停車・通過駅を表すクラス。train.stop_stations や station.upcoming_trains の戻り値に含まれます。
- station: StationData: 駅
- time: Optional[datetime]: 公式の stationDepTime（発車時刻）。始発駅が "-" のときや時刻不明なら None。"-" がないダイヤでは先頭駅の既存の時刻を保持します（[始発時刻の調査](START_TIMES.md)）
- train: Optional[TrainData]: この停車駅を持つ列車への逆参照。ダイヤ登録済みなら現在の列車を指します
- is_stop: bool: 停車するかどうか（通過駅ならFalse）
- is_start: bool: この駅が始発駅かどうか
- is_final: bool: この駅が終着駅かどうか

`StopStationData` の順序比較は時刻だけを比べ、時刻不明ならすべて False です。`==` は同一性で、同時刻の別の停車駅は等しくありません。
`b - a` は時刻差の timedelta（どちらか不明なら None）を返します。

## TrainHistoryRecord
走行中の列車の、ある位置情報の時点での値です。`TrainData.history` の1件です。1日分を残すため、`BaseModel` ではなく `dataclass`（`frozen=True, slots=True`）です。

- master: KHState: 所属する状態
- file_created_time: datetime: その位置情報の作成時刻（`fileCreatedTime`。取得した時刻ではありません）
- location_col, location_row: int: 座標
- delay_minutes: int: 遅延分数
- direction: Literal["up","down"]: 方向
- destination: Optional[StationData]: 行先駅
- line: LineLiteral: その時点で走っていた路線
- position: TrainPosition: その時点の線路上の位置（`ActiveTrainData.position` と同じ）

## BusArrival / BusStatus (バス)

`BusClient.arrivals(stop_name, stop_num=1)` と `to_arrivals(response)`（通信なし）が返す、バス停に来る1便です。`frozen=True` の dataclass です。

- `route: str`: 系統番号（公式の `"[22]"` の括弧を外した `"22"`）。`"22B"`・`"直Ｑ"`・`"くるり"`・`"---"` のように数字でないこともある
- `destination: str`: 行先（例: `"京阪香里園行"`）
- `via: str`: 経由
- `scheduled: Optional[datetime]`: 定刻（JST）
- `expected: Optional[datetime]`: 到着予定（公式の「HH:MM 到着予定」）。位置を受信していない便は `None`
- `delay_minutes: Optional[int]`: 遅れ（分）。定時運行中なら0。未受信・到着済・読めない表記なら `None`
- `status: BusStatus`: `TRACKED`（位置を受信中）・`ARRIVED`（到着済）・`NOT_TRACKED`（未受信）・`UNKNOWN`（読めない表記）
- `order: int`: 接近順位
- `lat`, `lon`: バスの位置
- `dgmpl: Optional[str]`: `fetch_diagram()` に渡す値
- `raw: BusState`: 元の公式の値

日付は接近情報の作成時刻（`datetimeStr`）に最も近い日を付けます（23:50 の接近情報にある 00:10 の便は翌日）。作成時刻が読めなければ時刻は `None` です。

## BusLocationResponse / BusStatePrms (バス)
バス接近情報のレスポンスモデル。

- `BusLocationResponse.body.busstates`: `BusState` のリスト
- `BusState.dgmpl: Optional[str]`: この便の `fetch_diagram(dgmpl)` に渡す値。HTML にキーがなければ `None`
- `BusState.scheduled_time: Optional[str]`: 定刻（`HH:MM`）
- `BusState.delay_text: Optional[str]`: 見出しの括弧内の表記（「定時運行中」「約1分遅れ」「到着済」）。位置を受信していない便は括弧が無いため `None`
- `BusState.delay_minutes: Optional[int]`: 遅れ（分）。定時運行中なら0。未受信・到着済、読めない表記（警告を出す）なら `None`。公式の「約N分遅れ」の値で、`busstateprms.timetable` の「到着予定」は定刻にこの遅れを足した時刻
- `BusState.busstateprms`: 詳細情報オブジェクト
  - `route: str`: 系統番号 (例: "[22]")
  - `destination: str`: 行先
  - `status: str`: 運行状況テキスト
  - `timetable: str`: 到着予定時刻など
  - `lat`, `lon`: バスの現在座標

## BusDiagram / BusDiagramStop (バスの発着時刻表)

- `BusDiagram.dgmpl: str`: 問い合わせに使った値
- `BusDiagram.stops: list[BusDiagramStop]`: 運行順の停留所。同名の停留所を複数回通る場合も別々に保持
  - `stop_name: str`: 公式の表示名（`〔京阪バス〕` などを含む）
  - `arrival_time: Optional[str]`: 着時刻。例 `"17:18"`。始発などで記載がない場合は `None`
  - `departure_time: Optional[str]`: 発時刻。例 `"17:20"`。終点などで記載がない場合は `None`
  - `arrival_is_estimated: bool` / `departure_is_estimated: bool`: 公式の時刻に `△` が付いているか

時刻は日付を含まない文字列です。24時以降の表記も保持します。
通信の失敗は `httpx.HTTPError`、発着時刻表がない場合や HTML の形式が想定と違う場合は `KeihanAPIError` です。

## MultiLang
多言語対応の文字列型。`station_name` や `delay_text` などで使われます。

- `ja: str`: 日本語
- `en: str`: 英語
- `cn: str`: 簡体字中国語
- `tw: str`: 繁体字中国語
- `kr: str`: 韓国語

## StationConnections
駅の乗換情報を保持するクラス。`StationData.transfer` から参照できます。各フィールドは `MultiLang_Lines` 型（各言語の路線名リスト）で、乗換先がない場合は `None` です。

- `train: Optional[MultiLang_Lines]`: JRなど鉄道の乗換情報
- `subway: Optional[MultiLang_Lines]`: 地下鉄の乗換情報
- `monorail: Optional[MultiLang_Lines]`: モノレールの乗換情報

## TrainType
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
