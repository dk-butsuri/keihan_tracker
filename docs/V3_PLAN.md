# keihan_tracker v3.0 設計プラン

v3.0 で行う構造改定の計画です。2026-11-13 時点で決めたことをまとめています。

## 目的

1. 位置まわりの名前を分かりやすくする（`next_station` が「停車中なら今の駅、そうでなければ次の駅」を返すのをやめる）
2. 高レベル部分と低レベル部分を分け、取得を自前でやりたい人・パースだけしたい人も使えるようにする
3. 高レベル部分は今より簡単に書けるようにする（同期版を含む）
4. 取得のたびに走る重い処理をなくす

## 現状の実測

`tests/fixtures` の平日データ（1,157列車、走行中44本）で計測しました。

| 処理 | 時間 |
|:---|:---|
| `fetch_pos()` 2回目以降 | 約140ms |
| うち `regist_dia(False)` | 約120ms |
| `StationData.upcoming_trains` 1回 | 約18ms |

`fetch_pos()` のたびに、ダイヤが変わっていなくても全列車の `route_stations` を空にして、`StopStationData`（pydantic）を18,370個作り直しています。これが時間の約85%です。`ActiveTrainData` と `TrainData` の作り直しは、これに比べると小さいです。

## 決めたこと

### 1. クラス名は今のまま

| 役割 | 名前 | 備考 |
|:---|:---|:---|
| 高レベル（非同期） | `KHTracker` | `fetch_pos()` も残す |
| 高レベル（同期） | `KHTrackerSync` | 新規 |
| 通信なしの状態管理 | `KHState` | 新規。`KHTracker` / `KHTrackerSync` の基底クラス |
| 取得だけ | `KHClient` / `KHClientSync` | 新規 |
| 列車 | `TrainData` / `ActiveTrainData` | 継承関係も今のまま |
| 駅・停車駅・履歴 | `StationData` / `StopStationData` / `TrainHistoryRecord` | 今のまま |

- `master` はそのまま残し、型を `KHState` にします。
- モジュール名を変える場合（`schemes` → `schemas` など）も、`keihan_tracker` 直下からの import は今どおり通るようにします。

### 2. 列車は2クラスのまま（一本化しない）

`TrainData` に `live: Optional[...]` を持たせて1クラスにする案は採りません。

- 重いのはダイヤの作り直しで、一本化しても速くなりません。
- 型は継承の方が強いです。`isinstance` 1回で全プロパティが絞れます。`Optional` にすると `None` チェックが増えます。
- 走り出し・運行終了をまたぐと別のオブジェクトになる点は v2 と同じです。またいで持つときは `tracker.trains[wdf]` を引き直します。

そのうえで、使う側が `isinstance` を書かずに済むよう、戻り値の型で先に絞ります。

| 取り方 | 戻り値の型 |
|:---|:---|
| `tracker.active_trains` | `dict[int, ActiveTrainData]` |
| `find_trains(active=True, ...)` | `list[ActiveTrainData]`（`@overload`） |
| `find_trains(active=False, ...)` | `list[TrainData]` |
| `station.arriving_trains` | `list[ActiveTrainData]` |
| `station.upcoming_trains` / `tracker.trains` | 混在（ここだけ `isinstance` が要る） |

- `is_special`・`train_number` など走行中の列車だけが持つ条件を指定したときも、`list[ActiveTrainData]` を返します。
- `delay_minutes` のように `TrainData` でも意味のある値は、今どおり基底クラスに置きます。

### 3. 位置まわりの名前

`next_station` と `next_stop_station` は v3 で削除します。列車の直下で `next` から始まるのは `next_stop` だけにします（`next_station` を次の停車駅と取り違えるのを防ぐため）。

**列車の直下：停車駅の話**

| プロパティ | 意味 |
|:---|:---|
| `stopped_at` | 停車中の駅。停車中でなければ `None` |
| `next_stop` | この先で次に停車する駅。停車中の駅は含まない。終点にいるときは `None` |
| `current_or_next_stop` | 停車中ならその駅、でなければ `next_stop`（発車標用。v2 の `next_stop_station` と同じ値） |
| `status` | `STOPPED` / `PASSING` / `BETWEEN` / `UNKNOWN` |

`StationData.arriving_trains` と `find_trains()` の絞り込みは `current_or_next_stop` を使います。

**`train.position`：座標から分かる線路上の話**

| プロパティ | 香里園に停車中・通過中 | 香里園→寝屋川市の駅間 |
|:---|:---|:---|
| `position.station` | 香里園 | `None` |
| `position.behind` | `None` | 香里園 |
| `position.ahead` | 寝屋川市 | 寝屋川市 |
| `position.line` | 京阪本線・鴨東線 | 京阪本線・鴨東線 |

- `location_col` / `location_row` も `position` に入れます。
- `TrainHistoryRecord` も同じ `position` を持ちます。`calc_position` の呼び出しが1か所になります。

**v2 からの書き換え**

| v2 | v3 |
|:---|:---|
| `next_station` | 駅にいるなら `position.station`、駅間なら `position.ahead` |
| `next_stop_station` | `current_or_next_stop` |
| `is_stopping` | `stopped_at is not None` または `status` |
| `is_at_station` | `position.station is not None` |

旧名は v3 で消えるため、古いコードは `AttributeError` で気づけます。同じ名前のまま意味が変わるのは、次の `master` だけです。

- **`master`**：`StationData` / `TrainData` / `TrainHistoryRecord` の `master` は、`KHTracker` から `KHState` に変わります（step 2 で変更）。状態（`stations`・`trains` など）は今までどおり読めますが、`fetch_pos()`・`web`・`snapshot_dir` など取得側のものは `master` から使えなくなります。取得は `KHTracker` 側で行ってください。`MIGRATION.md` にも書きます。

### 4. レイヤー分割

```
schemas.py   公式JSONのpydanticモデル（今の schemes）
parse.py     parse_positions(text) など。通信なし。壊れていれば KeihanAPIError
client.py    KHClient / KHClientSync。User-Agent・ETag・レート制限・snapshot保存
state.py     KHState。通信も時計も持たない状態管理
               .apply_stations() / .apply_positions() / .apply_timetable()（この順に呼ぶ）
tracker.py   KHTracker / KHTrackerSync = KHClient + KHState
```

| 使う人 | 使うもの |
|:---|:---|
| パースだけしたい | `parse.py` と `schemas.py` |
| 取得を自前でやりたい | `KHState()` を作り、`apply_*` に流し込む |
| 取得だけ使いたい | `KHClient` / `KHClientSync` |
| 全部任せたい | `KHTracker` / `KHTrackerSync` |

- `KHState` は営業日を位置情報の `fileCreatedTime` だけから決めます。実時刻を見ないため、テストで `tracker.date` を書き換える必要がなくなり、保存したスナップショットの解析（`tools/`）も通信のモックなしで行えます。

### 5. 高レベル部分に足すもの

- `async with KHTracker() as tracker:`（今は `AsyncClient` を閉じる手段がない）
- `KHTrackerSync`（`fetch_pos()` を `await` なしで呼べる）
- `async for _ in tracker.watch(60):`（ポーリングのループ）
- `tracker.station("香里園")` / `tracker.station("KH18")`（今は `tracker.stations[18]` のみ）
- `fetch_pos()` の戻り値で「更新した／変化なし／レート制限でスキップ」を区別できるようにする（今は何も返さない）

### 6. 最適化

1. **ダイヤは変わったときだけ組み直す**：`304 Not Modified` のときと `fileCreatedTime` が同じときは何もしません。ダイヤに臨時列車として載っている列車が走り出したときは、その1本だけ登録します。
2. **ドメイン側のクラスを `dataclass(slots=True)` にする**：pydantic は公式JSONの検証だけに使います。
3. **索引を持つ**：駅→停車する列車の索引をダイヤ適用時に作ります。走行中の列車は辞書で持ちます（今は `active_trains` が呼ぶたびに辞書を作り直し、`fetch_pos()` のループ内で毎回呼んでいます）。

### 7. そのほか

- **改名**：`wdfBlockNo` → `wdf`、`regist_dia` → timetable 系の名前、`keihan_train` → `train`、`schemes` → `schemas`
- **削除**：`fetch_filelist`（未実装）、`tabulate` への依存（`__str__` でしか使っていない）
- **バス**：電車と同じ層分けにします。モジュールグローバルのキャッシュと、呼ぶたびに作る `AsyncClient` をクライアントのインスタンスに移します。
- **パッケージング**：`setup.py` → `pyproject.toml`。Python の下限は `>=3.10` のままにします（上げても使いたい機能がなく、3.10 の利用者が v3 を入れられなくなるだけのため）。

### 8. 到着予測

signage で使っている到着予測を、定数を差し替えられる関数として入れます。

```python
@dataclass(frozen=True, slots=True)
class EtaPolicy:
    discount_rate: float = 0.1          # 残り10分ごとに遅れを1分割り引く
    discount_max_delay: int = 10        # これを超える遅れは割り引かない
    windows: tuple[tuple[int, int], ...] = ((60, 10), (25, 60))  # (H の上限, D の上限)
    stuck_minutes: int = 15
    growing_ratio: float = 0.7
    check_blocked_ahead: bool = True

def estimate_arrival(train, station, *, now, policy=EtaPolicy()) -> EtaResult: ...

class EtaResult:
    time: datetime | None     # 推定時刻。伏せるときは None
    reason: EtaHidden | None  # GROWING / OVER_120 / STUCK / BLOCKED_AHEAD / OUT_OF_WINDOW
```

- 元の実装は [dk-butsuri/keihan_signage](https://github.com/dk-butsuri/keihan_signage) にあります（`_corrected_delay_minutes`・`_within_estimate_window`・`_blocked_ahead`、`trains.delay_growing_ratio`・`trains.stuck_minutes`）。実装するときはこのコードを読み、出力が一致することをテストで確かめます。
- 既定値は signage の値です。何も渡さなければ signage と同じ結果になります。
- 列車のメソッドではなく独立した関数にします。前が詰まっているかの判定は他の列車も見るためです。
- `KHState` は時計を持たないため、`now` は引数で受けます。`KHTracker` には `now` を自動で入れる `tracker.eta(train, station)` を置きます。
- 伏せた理由を `reason` で返します。「150分以上遅れ」のような出し分けは使う側が `reason` を見て行います。
- 定数で変えられるのは係数と閾値だけです。補正式の形を変えたい場合は、`reason` の判定だけ使い、時刻は使う側で計算します。

計算の中身（D：公式の遅れ分数、H：定刻＋D までの残り分数）

| 段階 | 内容 |
|:---|:---|
| 補正 | D ≤ 10分なら 定刻 ＋ D − min(D, 0.1×H)。D > 10分なら 定刻 ＋ D |
| 表示する条件 | D ≥ 1 のとき、(H ≤ 60 かつ D ≤ 10) または (H ≤ 25 かつ D ≤ 60) |
| 伏せる列車 | 遅れが増え続けている／遅れが120分以上／同じ位置に15分超（始発駅の発車待ちは除く）／同じ方向で当駅との間に遅れ増加中か15分超停滞の列車がいる |

### 9. 比較

- **列車の `==` / `hash`**：`(date, wdf)` で比べる。WDF番号は1日の中でしか列車を区別しないため、日付も含める。`ActiveTrainData` と、それを `inactivate()` した `TrainData` は等しい（同じ列車）。`set` や辞書のキーに使える。
- **種別の順位**：`TrainType` だけに持たせる（列車同士の `<` は作らない。`==` を `(date, wdf)` で比べるのと食い違うため）。`train.train_type > other.train_type`、`train.train_type >= TrainType.EXPRESS` のように使う。
  - 今の `TrainType` は `str` の `Enum` のため、`<` が文字列の順で比べてしまう。これを順位での比較に置き換える。`==` は今のまま（`"特急"` とも等しい）。
  - 順位（低い順）：普通 < 区間急行 < 準急 < 通勤準急 < 急行 < 通勤急行 < 深夜急行 < 快速急行 < 通勤快急 < 特急 < ライナー < 快速特急 洛楽（`schemes.py` の定義順）
  - 「臨時列車」（公式が種別を出さないとき）は順位が分からないため、NaN と同じ扱いにする。`<`・`<=`・`>`・`>=` は相手が何でも False を返す。表示では「上位とも下位とも言えない」のでラベルが出ないだけで済む。ただし `sorted()` に混ぜると並びが崩れることを docstring に書く。
  - 想定する使い方：`fastest_arrivals` で先着する列車が先発と違うとき、「先発の普通、後続の特急が先着」のようなラベルを付ける。

## 進める順番

1. **v2.9**：同値性テストと非推奨警告
   - Wayback 26日分と平日・土休日の fixture で、全列車の全プロパティを書き出して保存する。v3 の出力と比べ、種別・方向・停車駅の推定が変わっていないことを確かめる
   - 削除・改名する名前に `DeprecationWarning` を出し、書き換え先を警告文に入れる
   - 京阪バスの遅延取得を追加する。新しい関数は作らず `get_khbus_info` を拡張する（遅延の取得元と、返す値の形は未決定）。v3 でバスを層分けするときに移しやすいよう、取得・パース・加工を関数で分けて書く
   - 状況（2026-11-13）：同値性テスト（`tests/equivalence.py`・`tests/test_equivalence.py`、平日・土休日・Wayback 28件の計30件）と非推奨警告は済み。v3 の名前（`position`・`stopped_at`・`next_stop`・`current_or_next_stop`・`wdf`）は v2.9 で先に足した。`status`・`find_trains` の `is_stopping` 引数・モジュール名の変更は v3 で行う
   - バス遅延の調査（2026-11-13 20:43、同志社香里・京阪香里園・枚方市駅北口の計7のりば・39便）：遅れた便はなく、遅れの表記は未確認。受信中の便は HTML の見出しに「定刻： 21:09 (定時運行中)」、`busstateprms` の時刻欄に「21:09 到着予定」が入る。到着済は「(到着済)」、未受信の便は括弧なしで時刻欄が「定刻 21:45」。遅れたときは括弧内か「到着予定」の時刻が変わると考えられるが、遅れた便の応答を保存してから形を決める。Wayback Machine には `busstateupd` の保存が無く、`linediagram` の42件も定刻表だけ（推定時刻なし）だった。このため `get_khbus_info` / `fetch_diagram` に `snapshot_dir` を足し、`KHTracker` と同じ形式で応答を保存できるようにした。その後、遅れた便の HTML が手に入ったため（見出しが「定刻： 21:11 (約1分遅れ)」、時刻欄が「21:12 到着予定」、`more_min` に `delay` クラス）、`BusState` に `scheduled_time`・`delay_text`・`delay_minutes` を足した。取得は `_fetch_busstate`、パースと加工は `parse_busstate`（通信なし）に分けた
   - `StationData.fastest_arrivals`（この駅から乗れる列車で、この先の各駅に最も早く着く列車と到着時刻。乗り換えなし・予定時刻）を足す。遅れは足さない（待ち合わせる列車は、相手が遅れても待つため順番が変わらない）。乗り換えも考えない。v3 で駅→列車の索引を作ったら、それを使って速くする
2. `parse` / `KHClient` / `KHState` に分割する（挙動は変えない）
   - 状況（2026-11-13）：済み。`parse.py`・`client.py`・`state.py` を作り、`KHTracker` は今の属性を property で `state` / `client` に委譲する。`master` は `KHState` になった（上の「3. 位置まわりの名前」の後の注意を参照）。ダイヤは位置情報の後に渡す（臨時列車は走行中のものだけ登録するため）。`KHState()` は営業日を持たずに始まり、`apply_positions` で決まる。モジュール名（`schemes`→`schemas` など）は変えていない
3. dataclass 化、ダイヤの差分更新、索引
4. 位置まわりの改名、`position`、`status`、`find_trains` の `@overload`、比較（「9. 比較」）
5. `KHTrackerSync`、`watch`、駅の検索、バスの層分け、到着予測（signage の出力と一致することをテストで確かめる）
6. README・RELIABILITY の更新、`MIGRATION.md`、3.0.0 リリース
