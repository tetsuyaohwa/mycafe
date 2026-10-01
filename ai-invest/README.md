# AI株式ダッシュボード（日本株）

ウォッチリストの日本株について、株価・テクニカル指標を計算し、Claude が「プラス材料／リスク／注目ポイント」を整理して表示するダッシュボードです。

## しくみ

```
watchlist.json ──▶ analyze.py ──▶ data/dashboard.js ──▶ index.html
（銘柄リスト）     ├ yfinance で1年分の日足・ニュース見出しを取得
                   ├ 移動平均・RSI・ボラティリティ等を計算
                   └ Claude API で銘柄ごとにコメント生成（JSON形式）
```

## 使い方

```bash
cd ai-invest
pip install -r requirements.txt

# 1) まず画面だけ確認（ネット不要・ダミーデータ）
python analyze.py --demo

# 2) 本番：実データ + AI分析
export ANTHROPIC_API_KEY=sk-ant-...   # https://console.anthropic.com で取得
python analyze.py

# AIなしで指標だけ更新したい場合
python analyze.py --no-ai
```

実行後、`index.html` をブラウザで開けば表示されます（サーバー不要）。

## 銘柄を変える

`watchlist.json` に証券コードと名前を追加・削除してください。

```json
{"code": "7974", "name": "任天堂"}
```

## 表示される指標

| 指標 | 意味 |
|---|---|
| 1か月 / 3か月 / 1年 | 騰落率（約21 / 63 営業日前比） |
| RSI(14) | 70以上で買われ過ぎ、30以下で売られ過ぎの目安 |
| ボラティリティ | 直近20日の値動きの大きさ（年率換算） |
| 中期トレンド | 25日移動平均が75日移動平均より上なら「上昇」 |

## 注意

- 情報整理用のツールです。売買の推奨ではありません。
- AI 1回の分析ごとに API 料金がかかります（6銘柄で1回あたり数十円程度が目安）。
- Yahoo Finance のデータは非公式 API 経由のため、仕様変更で取得できなくなることがあります。
