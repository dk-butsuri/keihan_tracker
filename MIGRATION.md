# v2.9 → v3.0 移行ガイド

v3.0 は、取得・パース・状態を分け、同期版と到着予測を追加した版です。Python 3.10 以上という条件は変わりません。
v2.9 で非推奨だった旧名は削除し、旧モジュールの互換口も作っていません。
`from keihan_tracker import KHTracker, TrainData, TrainType` のような直下からの import は引き続き使えます。

## モジュール名

| v2.9 | v3.0 |
|:---|:---|
| `keihan_tracker.keihan_train` | `keihan_tracker.train` |
| `keihan_tracker.keihan_train.tracker` | `keihan_tracker.train.tracker` |
| `keihan_tracker.keihan_train.schemes` | `keihan_tracker.train.schemas` |
| `keihan_tracker.keihan_train.client` / `state` / `parse` / `position_calculation` / `stations_map` | `keihan_tracker.train` 配下の同名モジュール |
| `keihan_tracker.bus.schemes` | `keihan_tracker.bus.schemas` |
| `keihan_tracker.bus.parsing` | `keihan_tracker.bus.parse` |

```python
# v2.9
from keihan_tracker.keihan_train.schemes import TrainType
from keihan_tracker.bus.parsing import parse_diagram

# v3.0
from keihan_tracker import TrainType, parse_diagram
# モデルを直接使う場合
from keihan_tracker.train.schemas import trainPositionList
from keihan_tracker.bus.schemas import BusLocationResponse
```

## 位置と停車駅

列車の直下は「停車する駅」、`position` は「線路上の位置」を表します。
旧名へのアクセスは `AttributeError`、削除したキーワード引数は `TypeError` になります。

| v2.9で非推奨の名前 | v3.0 |
|:---|:---|
| `train.next_station` | `train.position.station or train.position.ahead` |
| `train.next_stop_station` | `train.current_or_next_stop` |
| `train.is_stopping` | `train.stopped_at is not None`、または `train.status` |
| `train.is_at_station` | `train.position.station is not None` |
| `record.stopping_station` | `record.position.station` |
| `record.is_at_station` | `record.position.station is not None` |
| `train.wdfBlockNo`（コンストラクターの別名も含む） | `train.wdf` / `wdf=` |
| `find_trains(next_stop_station=...)` | `find_trains(current_or_next_stop=...)` |
| `find_trains(is_stopping=...)` | `find_trains(status=TrainStatus.STOPPED)` など |

```python
# v2.9
print(train.next_station, train.next_stop_station, train.wdfBlockNo)
stopped = tracker.find_trains(is_stopping=True)
moving = tracker.find_trains(is_stopping=False)

# v3.0
from keihan_tracker import TrainStatus

print(train.position.station or train.position.ahead, train.current_or_next_stop, train.wdf)
stopped = tracker.find_trains(status=TrainStatus.STOPPED)
moving = [t for t in tracker.find_trains(active=True)
          if t.status in (TrainStatus.PASSING, TrainStatus.BETWEEN)]
```

`TrainStatus.UNKNOWN` は停車駅の位置関係が分からない場合です。旧 `is_stopping is None` に対応し、上の moving には含めません。
`next_stop` は停車中の駅を含まない次の停車駅です。旧 `next_stop_station` と同じ値が必要なら `current_or_next_stop` を使ってください。
公式JSONを表す Pydantic モデルの `wdfBlockNo` は、公式のフィールド名として残しています。

## 取得・終了処理

`regist_dia()` と未実装だった `fetch_filelist()` は削除しました。ダイヤは `fetch_pos()` が必要に応じて取得します。

```python
# v2.9
tracker = KHTracker()
await tracker.regist_dia(True)
await tracker.fetch_pos()

# v3.0
from keihan_tracker import KHTracker, FetchResult

async with KHTracker() as tracker:
    result = await tracker.fetch_pos()
    if result is FetchResult.UPDATED:
        print(tracker.position_created_at)
```

以前は戻り値が None でした。v3 は `UPDATED`・`UNCHANGED`・`SKIPPED` を返します。
`UNCHANGED` は位置情報の `fileCreatedTime` が同じ場合で、状態の適用は従来どおり行います。通信失敗や `KeihanAPIError` は戻り値に置き換えず、例外のままです。
`async with` を使わない場合は `await tracker.aclose()` で閉じます。`KHClient` にも `aclose()` があります。

```python
# v2.9: 自分で取得と待機のループを書く
while True:
    await tracker.fetch_pos()
    await asyncio.sleep(60)

# v3.0: 処理時間を差し引いて待つ
async for result in tracker.watch(60, on_error="continue"):
    print(result)
    # break で終了
```

`watch()` の既定値は `on_error="raise"` です。`"continue"` は通信・パースの失敗を warning に出し、その回は yield しません。
引数の検証は反復開始時で、interval がレート制限より短い場合と、未知の on_error は ValueError です。

同期的に取得するコードは、呼び出すたびに `asyncio.run()` する形から、専用ループを保持する同期版へ移せます。

```python
# v2.9
tracker = KHTracker()
asyncio.run(tracker.fetch_pos())

# v3.0
from keihan_tracker import KHTrackerSync

with KHTrackerSync() as tracker:
    tracker.fetch_pos()
    station = tracker.station("KH18")
```

`KHTrackerSync` は `.tracker` に非同期版を持ち、継承しません。`close()` は繰り返し呼べます。
Jupyter や動いているイベントループの中では `KHTracker` を await で使ってください。`KHClientSync` は作っていません。

## 読み取り専用の辞書と master

`trains`・`stations` は Mapping で読み取れます。要素の代入・削除は TypeError、属性への再代入は AttributeError です。
駅→停車駅の索引と矛盾しないよう、データの更新は `KHState.apply_*()` で行います。

```python
# v2.9
tracker.trains[wdf] = replacement
del tracker.stations[18]
await train.master.fetch_pos()

# v3.0: 自前で取得したデータを反映する場合
tracker.state.apply_positions(parsed_positions)
tracker.state.apply_timetable(parsed_timetable)
await tracker.fetch_pos()  # 通信は tracker から呼ぶ
state = train.master      # master は KHState
```

`StationData`・`TrainData`・`TrainHistoryRecord` の `master` は KHState です。
`master.trains`・`master.stations` は読めますが、`master.fetch_pos()`・`master.web`・`master.snapshot_dir` は使えません。
取得は tracker / tracker.client、通信なしの適用と検索は tracker.state へ分かれました。

## dataclass と比較

駅・列車・停車駅は `dataclass(slots=True, kw_only=True, eq=False)` になり、Pydantic の `model_copy()`・`model_dump()`・検証APIは持ちません。
公式JSONの Pydantic モデルは `schemas.py` に残しています。ドメインオブジェクトの複製には `dataclasses.replace()` を使えます。

```python
# v2.9
copy = train.model_copy(update={"delay_minutes": 1})

# v3.0: 表示用に複製する例（tracker 内の状態を変更するものではない）
from dataclasses import replace
copy = replace(train, delay_minutes=1)
```

列車の等しさと hash は `(date, wdf)` で決まります。走行中と運行終了後の別オブジェクトでも同じ列車として等しく、set や辞書のキーにできます。
更新後の現在のオブジェクトは `tracker.trains[wdf]` から引き直してください。

```python
# v2.9: データ全体の比較に頼らず、自前の識別子を持つ
same = (a.date, a.wdf) == (b.date, b.wdf)
key = (a.date, a.wdf)

# v3.0
same = a == b
seen = {a, b}  # 同じ date・wdf なら1本
```

`TrainType` の大小は文字コード順から種別の順位へ変わりました。順序は普通から快速特急 洛楽までの定義順です。
`==` は変わらず文字列とも等しく、臨時列車を含む `<`・`<=`・`>`・`>=` はすべて False です。臨時列車を sorted() に混ぜると並びが崩れます。

```python
# v2.9: str の比較なので、種別の速さには使えない
comparison = TrainType.LOCAL < TrainType.LTD_EXP

# v3.0: 種別の順位で比較する
comparison = TrainType.LOCAL < TrainType.LTD_EXP  # True
is_express_or_above = train.train_type >= TrainType.EXPRESS
```

`StopStationData` の `==` は同一性のままです。新しい大小比較は時刻だけを比べ、時刻不明ならすべて False になります。
同時刻の別オブジェクトは `a <= b` と `b <= a` が True でも `a != b` です。

```python
# v2.9
gap = b.time - a.time if a.time is not None and b.time is not None else None

# v3.0
gap = b - a
stops_here = station in train  # 停車するか。ダイヤなし・通過は False
stop = train.get_stop(station)
owner = stop.train if stop else None
```

`StopStationData.time` の中身は変更していません。公式の `stationDepTime`（発車時刻）です。
`fastest_arrivals` で到着として比べている時刻も、正確にはこの発車時刻です。始発時刻の補完は追加していません（[調査結果](docs/START_TIMES.md)）。

## バス・予測・表の変更

`get_khbus_info()`・モジュール関数の `fetch_diagram()` の挙動は維持しています。
共通の30秒キャッシュを使い、通信ごとに AsyncClient を開閉するため、別々の asyncio.run() からも使えます。
同じループで繰り返す場合は、新しい `BusClient` が通信クライアントとキャッシュを保持します。

```python
# v2.9（v3でも使用可能）
from keihan_tracker import get_khbus_info
info = await get_khbus_info("京阪香里園", 1)

# v3.0
from keihan_tracker import BusClient
async with BusClient() as client:
    info = await client.get_info("京阪香里園", 1)
```

`parse_busstate`・`parse_diagram` は直下から import でき、通信なしで使えます。
スナップショットの形式・ファイル名は変更していません。

到着予測は `tracker.eta()` または `estimate_arrival(..., now=...)` へまとめられます。
`EtaPolicy` で係数・閾値を変え、予測しない理由は `EtaResult.reason` で扱えます。時刻の元は発車時刻です。
[使い方](docs/USAGE.md#3-遅延を加味した停車駅の時刻の推定) に使い方の例、[比較記録](docs/ETA_SIGNAGE.md) に signage と一致しなかった全件へのリンクがあります。

tabulate への依存を削除しました。`TrainData`・`ActiveTrainData` の文字列表は固定幅になり、時刻の見出しは発車時刻、空だったホーム番線列は省いています。
`StationData.__str__` は駅名のままです。パッケージ設定は `setup.py` から `pyproject.toml` に移しました。
