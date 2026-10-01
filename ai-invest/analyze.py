"""日本株ウォッチリストの株価を取得し、テクニカル指標とAIコメントを data/dashboard.json に書き出す。

使い方:
    python analyze.py            # Yahoo Finance から取得 + Claude で分析（ANTHROPIC_API_KEY が必要）
    python analyze.py --no-ai    # 指標だけ計算（AIコメントなし）
    python analyze.py --demo     # ネット接続なしでダミーデータを生成（画面確認用）
"""

import argparse
import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
WATCHLIST = ROOT / "watchlist.json"
OUTPUT = ROOT / "data" / "dashboard.json"
JST = timezone(timedelta(hours=9))
MODEL = "claude-opus-5-5"


# ---------- データ取得 ----------

def fetch_history(code: str) -> tuple[list[dict], list[str]]:
    """東証銘柄（例: 7203 → 7203.T）の約1年分の日足とニュース見出しを返す。"""
    import yfinance as yf

    ticker = yf.Ticker(f"{code}.T")
    df = ticker.history(period="1y", interval="1d", auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"{code}: 株価データを取得できませんでした")
    rows = [
        {"date": idx.strftime("%Y-%m-%d"), "close": float(r["Close"]), "volume": int(r["Volume"])}
        for idx, r in df.iterrows()
    ]
    headlines = []
    try:
        for item in (ticker.news or [])[:5]:
            title = item.get("title") or (item.get("content") or {}).get("title")
            if title:
                headlines.append(title)
    except Exception:
        pass
    return rows, headlines


def demo_history(code: str) -> tuple[list[dict], list[str]]:
    """ランダムウォークのダミー株価（デモ表示用）。"""
    rng = random.Random(int(code))
    price = rng.uniform(1500, 9000)
    drift = rng.uniform(-0.0006, 0.0012)
    rows, d = [], date.today() - timedelta(days=365)
    while d <= date.today():
        if d.weekday() < 5:
            price *= math.exp(drift + rng.gauss(0, 0.016))
            rows.append({"date": d.isoformat(), "close": round(price, 1),
                         "volume": int(rng.uniform(2e6, 2e7))})
        d += timedelta(days=1)
    return rows, []


# ---------- 指標計算 ----------

def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for prev, cur in zip(values[-n - 1:-1], values[-n:]):
        diff = cur - prev
        gains += max(diff, 0)
        losses += max(-diff, 0)
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def pct_change(values: list[float], days: int) -> float | None:
    if len(values) <= days:
        return None
    return (values[-1] / values[-1 - days] - 1) * 100


def volatility(values: list[float], n: int = 20) -> float | None:
    """直近n日の日次リターン標準偏差を年率換算（%）。"""
    if len(values) <= n:
        return None
    rets = [math.log(b / a) for a, b in zip(values[-n - 1:-1], values[-n:])]
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(245) * 100


def compute_metrics(rows: list[dict]) -> dict:
    closes = [r["close"] for r in rows]
    ma25, ma75 = sma(closes, 25), sma(closes, 75)
    return {
        "price": closes[-1],
        "change_1d": pct_change(closes, 1),
        "change_1m": pct_change(closes, 21),
        "change_3m": pct_change(closes, 63),
        "change_1y": pct_change(closes, len(closes) - 1),
        "ma25": ma25,
        "ma75": ma75,
        "rsi14": rsi(closes),
        "volatility": volatility(closes),
        "high_52w": max(closes),
        "low_52w": min(closes),
        "trend": None if ma25 is None or ma75 is None else ("上昇" if ma25 > ma75 else "下降"),
    }


# ---------- AI分析 ----------

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["強気", "やや強気", "中立", "やや弱気", "弱気"]},
        "summary": {"type": "string", "description": "2〜3文の日本語要約"},
        "positives": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "watch_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stance", "summary", "positives", "risks", "watch_points"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """あなたは日本株のアナリストです。与えられたテクニカル指標とニュース見出しだけを根拠に、
個人投資家向けに銘柄の現状を簡潔に整理してください。
- 数値に基づいて書き、推測には「〜の可能性」と明記する
- 売買の推奨や目標株価は出さない（投資判断の材料整理に徹する）
- positives / risks / watch_points は各1〜3項目、1項目40字以内"""


def ai_analyze(client, stock: dict) -> dict | None:
    payload = {
        "銘柄": f'{stock["name"]} ({stock["code"]})',
        "指標": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in stock["metrics"].items()},
        "直近20日終値": [round(r["close"], 1) for r in stock["history"][-20:]],
        "ニュース見出し": stock["headlines"] or ["（取得なし）"],
    }
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    if response.stop_reason == "refusal":
        print(f'  ! {stock["code"]}: AIが回答を辞退しました')
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    return json.loads(text) if text else None


def demo_ai(stock: dict) -> dict:
    """--demo 用のルールベースのサンプルコメント（AIは呼ばない）。"""
    m = stock["metrics"]
    up = m["trend"] == "上昇"
    rsi_v = m["rsi14"] or 50
    stance = "やや強気" if up and rsi_v < 70 else "中立" if up else "やや弱気"
    return {
        "stance": stance,
        "summary": f"【サンプル】25日線が75日線を{'上回り' if up else '下回り'}、中期トレンドは{m['trend']}。"
                   f"RSIは{rsi_v:.0f}で{'過熱気味' if rsi_v > 70 else '売られ過ぎ圏' if rsi_v < 30 else '中立圏'}です。",
        "positives": [f"3か月騰落率 {m['change_3m']:+.1f}%"] if (m["change_3m"] or 0) > 0 else ["52週安値からの反発余地"],
        "risks": [f"年率ボラティリティ {m['volatility']:.0f}%"],
        "watch_points": ["25日線を維持できるか", "次回決算での業績見通し"],
    }


# ---------- メイン ----------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="ダミーデータで生成")
    parser.add_argument("--no-ai", action="store_true", help="AI分析をスキップ")
    args = parser.parse_args()

    client = None
    if not (args.demo or args.no_ai):
        import anthropic
        client = anthropic.Anthropic()

    stocks = []
    for item in json.loads(WATCHLIST.read_text(encoding="utf-8")):
        print(f'{item["code"]} {item["name"]} ...')
        rows, headlines = (demo_history if args.demo else fetch_history)(item["code"])
        stock = {**item, "headlines": headlines, "metrics": compute_metrics(rows), "history": rows}
        stock["ai"] = ai_analyze(client, stock) if client else demo_ai(stock) if args.demo else None
        stocks.append(stock)

    OUTPUT.parent.mkdir(exist_ok=True)
    data = json.dumps({
        "generated_at": datetime.now(JST).isoformat(timespec="minutes"),
        "demo": args.demo,
        "model": MODEL if client else None,
        "stocks": stocks,
    }, ensure_ascii=False)
    OUTPUT.write_text(data, encoding="utf-8")
    # index.html をサーバーなし（file://）でも開けるよう JS 版も書き出す
    OUTPUT.with_suffix(".js").write_text(f"window.DASHBOARD = {data};\n", encoding="utf-8")
    print(f"→ {OUTPUT.relative_to(ROOT)} / dashboard.js を書き出しました")


if __name__ == "__main__":
    main()
