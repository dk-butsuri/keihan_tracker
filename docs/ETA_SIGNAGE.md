# 到着予測と signage の比較（2026-11-14）

参照先は [keihan_signage](https://github.com/dk-butsuri/keihan_signage/tree/e0eb4aae01f00af8ab2aa561a760a57f26adb855)、コミット `e0eb4aae01f00af8ab2aa561a760a57f26adb855`。
リポジトリ外の一時フォルダーへ clone して読み、変更・依存への追加はしていない。

`tests/eta_signage_reference.py` で `main.py` の予測関数と `_ordered_trains` を AST からそのまま読み込み、実行した。
Web アプリの起動処理は読まず、表示文言だけに使う関数は空の結果に置き換えた。
旧プロパティ名は比較用プロセス内だけで v3 の値に対応させた。
`_ordered_trains` には実際の upcoming_trains に含まれる対象列車を1本ずつ渡し、件数制限なし・残り時間の下限0分で比較した。
他の走行列車は全件渡し、前詰まり判定も実行している。

平日 fixture と、位置情報が保存されている Wayback 25件（平日・土休日）について、全走行列車 × 全停車駅の 22,494 組を比較した。
通常の土休日 fixture は位置情報がなく走行列車0本のため、土休日の走行列車は Wayback の保存データで補った。
signage の予測時刻ありは10,532組。比較データには各入力と完全な日時を保存している。

結果は22,024組一致、470組不一致。通常の平日 fixture の854組は全件一致した。
不一致はすべて、signage の一覧表示の過去時刻フィルターによるものだった。
signage は `int((estimated_time - now).total_seconds() / 60) < 0` で、当駅停車中でない列車の行を除外する。
今回の単独の予測関数は、経路上でまだ通っていない駅について補正時刻を返す。この470組はすべて予測時刻が now より1分以上前で、signage 側は行なし、ライブラリ側は時刻あり・reason=None。
補正式や表示範囲の違いによる不一致はなかった。仕様の「一致しないところは直さず一覧にして報告する」に従い、この差は変えていない。

全件は [differences.csv](../tests/fixtures/eta/differences.csv) に、入力の snapshot・WDF番号・対象駅番号・now と、signage / ライブラリの出力を並べた。
比較元の全入力・期待値、差の一覧、参照コードの SHA-256、参照した関数名、signage 側の境界値テストの入力と期待値は [signage.json.gz](../tests/fixtures/eta/signage.json.gz) に保存した。
既存の同値性テストのゴールデンは変更していない。

差の件数（snapshot別）：

| snapshot | 不一致 |
|:---|---:|
| wayback-20221108052016 | 1 |
| wayback-20230911234450 | 453 |
| wayback-20240319144023 | 1 |
| wayback-20240529005223 | 1 |
| wayback-20240617112725 | 1 |
| wayback-20241005041543 | 1 |
| wayback-20250520091048 | 1 |
| wayback-20250731093014 | 2 |
| wayback-20251006051334 | 2 |
| wayback-20260209095433 | 2 |
| wayback-20260323103816 | 1 |
| wayback-20260428001711 | 4 |
