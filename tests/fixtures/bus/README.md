# 京阪バスの実データ

2026年11月7日 17:54（日本時間）に、公式 BUS NAVI から取得した応答を gzip 圧縮して保存しています。
`manifest.json` は展開後の SHA-256 です。テスト時にはネットワークへ接続しません。

- `busstateupd.json.gz`: `POST https://busnavi.keihanbus.jp/pc/busstateupd`。
  `dgmpl=同志社香里:1::`（既存の取得関数と同じく値を URL エンコードしてフォームに指定）、`sort4=0`。
  `datetimeStr` は `2026/11/07 17:54現在`、接近情報は7件です。
- `linediagram.html.gz`: `GET https://busnavi.keihanbus.jp/pc/linediagram`。
  `mode=4`、`dgmpl=同志社香里:1:1:75120:17:12`、`dt=202611071754`。
  寝屋川市駅［東口］17:02発から京阪香里園17:31着までの17停留所です。
  接近情報の先頭の便（同志社香里17:53）とは別の便です。

`dgmpl` の組み立て方は、公式の接近情報詳細ページが読む
`https://busnavi.keihanbus.jp/pc/static/js/b_busstatedtl.min.js` の `.btnDiagram` を確認しています。
`bus_key` の3番目を0時からの分数として時分に分け、4番目と2番目を文字列で連結します。
