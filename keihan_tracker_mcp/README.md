# keihan_tracker_mcp

`keihan_tracker` を MCP（Model Context Protocol）サーバーとして公開する薄いラッパーです。
stdio で動作し、Claude Desktop などの MCP クライアントから京阪電車のリアルタイム位置・当日ダイヤを参照できます。

> ⚠️ 非公式 API ラッパーです。値は推定を含み無保証です。私的・自己責任でご利用ください。

## 設計方針

**計算はしない。素のデータを返すだけ。** 経路探索・最速比較・通過時刻の補間などは、
クライアント側の LLM が、返ってきたデータとサーバーが与える「予備知識」（駅ナンバリングの
座標系・direction の意味・`wdfBlockNo` が一意キー・停車パターンは列車ごとに見る、等）を使って
エージェンティックに組み立てます。鮮度はライブラリ内蔵の 15 秒レート制限に委譲しています。

## インストール

```bash
pip install keihan-tracker[mcp]
```

## 起動

```bash
keihan-mcp
```

## ツール

| ツール | 返すもの |
|---|---|
| `list_stations()` | 全駅（駅番号・駅名・路線）。駅名↔番号の解決・座標系の確認 |
| `get_live_positions()` | 現在走行中の全列車のスナップショット（位置・種別・行先・遅延） |
| `get_train(wdf_block_no)` | 指定列車の停車パターン（通過駅含む全駅順・停車駅のみ時刻）＋走行中なら現在位置 |
| `get_station_schedule(station_number, direction?)` | 指定駅にこれから来る/停車中の列車（発車標相当） |
| `find_trains(train_type?, direction?, destination_number?, status?, min_delay?)` | 条件で当日ダイヤを絞り込み |

リソース `keihan://readme` でライブラリ本体の README（データ構造の詳細）を取得できます。

## Claude Desktop 設定例

```json
{
  "mcpServers": {
    "keihan": {
      "command": "keihan-mcp"
    }
  }
}
```
