# keihan_tracker

京阪電車の列車位置・遅れ・ダイヤと、京阪バスの接近情報を Python で扱う非公式ライブラリです。

> [!WARNING]
> 京阪の公式サイトが使っているデータを読み取る**非公式**のライブラリです。公式の仕様変更で動かなくなることがあります。
> [京阪グループHPの利用規約](https://www.keihan.co.jp/terms.html)・[BUS NAVI 利用規約](https://busnavi.keihanbus.jp/pc/termsofuse)を確認し、**私的利用の範囲**で使ってください。

## できること

- 走っている列車の位置・種別・行先・遅れを取る
- 駅に「これから来る列車」を時刻順に並べる（発車標が作れます）
- 遅れを加味して、駅に着く時刻を予測する
- 「〇〇には先着」のように、先に着く列車を調べる
- 京阪バスの接近情報と、便ごとの時刻表を取る

## インストール

```bash
pip install keihan-tracker
```

Python 3.10 以上で動きます。

## はじめての使い方

香里園に来る列車を5本表示します。

```python
from keihan_tracker import KHTrackerSync

with KHTrackerSync() as tracker:
    tracker.fetch_pos()                    # 公式サイトから最新の情報を取る
    station = tracker.station("香里園")     # 駅番号 18 や "KH18" でも探せます

    for train, stop in station.upcoming_trains[:5]:
        when = stop.time.strftime("%H:%M") if stop.time else "--:--"   # ダイヤが乱れると時刻が分からないことがある
        print(when, train.train_type.value, train.destination)
```

```text
21:27 準急 淀屋橋
21:27 準急 出町柳
21:39 準急 出町柳
...
```

`async` で書くなら `KHTracker` を使います。

```python
import asyncio
from keihan_tracker import KHTracker

async def main():
    async with KHTracker() as tracker:
        await tracker.fetch_pos()
        for train in tracker.find_trains(active=True, min_delay=5):
            print(train.train_number, train.train_type.value, f"{train.delay_minutes}分遅れ")

asyncio.run(main())
```

## もっと知りたいとき

| 知りたいこと | 読むもの |
|:---|:---|
| 定期的に取り続ける・到着予測・バス・同期版と非同期版 | [使い方](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/USAGE.md) |
| つまずきやすい点・公式データの癖 | [使い方の「注意点」](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/USAGE.md#注意点) |
| クラスごとの属性とメソッド | [リファレンス](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/REFERENCE.md) |
| 値がどこまで正確か（公式の値か推定か） | [信頼性](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/RELIABILITY.md) |
| v2 から書き換える | [移行ガイド](https://github.com/dk-butsuri/keihan_tracker/blob/main/MIGRATION.md) |

## 公式サイトへの負荷について

- 列車位置の取得は30秒に1回までに制限しています（公式の位置情報は1分ごとにしか変わりません）。
- User-Agent で `keihan_tracker` と名乗ってアクセスします。
- 取得先と頻度は[使い方の「取得先と負荷への配慮」](https://github.com/dk-butsuri/keihan_tracker/blob/main/docs/USAGE.md#取得先と負荷への配慮)にあります。

## 関連プロジェクト

- [train_operation_info](https://github.com/dk-butsuri/train_operation_info)：各社の公式サイトの運行情報を取るライブラリです。

## ライセンス

MIT License © 2026 dk-butsuri
