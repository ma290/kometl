import os
from binance.client import Client
from dotenv import load_dotenv
from trade_execution import place_order

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL")
client = Client(API_KEY, API_SECRET)

# Config
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
EMA_PERIOD = int(os.getenv("EMA_PERIOD", 50))
SL_MULT = float(os.getenv("SL_MULT", 1.0))
TP_MULT = float(os.getenv("TP_MULT", 2.0))
BODY_STRENGTH_MULT = float(os.getenv("BODY_STRENGTH", 2.0))
VOLUME_STRENGTH_MULT = float(os.getenv("VOLUME_STRENGTH", 2.0))
TRAIL_OFFSET = float(os.getenv("TRAIL_OFFSET", 0.5)) / 100.0
TRADE_QTY = float(os.getenv("TRADE_QTY", 0.01))

def fetch_klines(symbol, interval="1m", limit=100):
    data = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return [{
        'open': float(i[1]),
        'high': float(i[2]),
        'low': float(i[3]),
        'close': float(i[4]),
        'volume': float(i[5])
    } for i in data]

def sma(values, period):
    return sum(values[-period:]) / period if len(values) >= period else 0

def rsi(closes, period):
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-i] - closes[-i - 1]
        if delta > 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rs = avg_gain / avg_loss if avg_loss else 0
    return 100 - (100 / (1 + rs))

def ema(values, period):
    weights = []
    multiplier = 2 / (period + 1)
    ema_val = values[0]
    for price in values:
        ema_val = (price - ema_val) * multiplier + ema_val
        weights.append(ema_val)
    return weights[-1]

def execute_strategy():
    candles = fetch_klines(SYMBOL, interval="1m", limit=100)
    if len(candles) < RSI_PERIOD + 1:
        print("Not enough candle data.")
        return

    closes = [c['close'] for c in candles]
    opens = [c['open'] for c in candles]
    highs = [c['high'] for c in candles]
    lows = [c['low'] for c in candles]
    volumes = [c['volume'] for c in candles]

    body = abs(closes[-1] - opens[-1])
    avg_body = sma([abs(c - o) for c, o in zip(closes, opens)], 20)
    avg_volume = sma(volumes, 20)
    upper_wick = highs[-1] - max(closes[-1], opens[-1])
    lower_wick = min(closes[-1], opens[-1]) - lows[-1]
    rsi_val = rsi(closes, RSI_PERIOD)
    ema_val = ema(closes[-EMA_PERIOD:], EMA_PERIOD)

    # Bullish conditions
    is_bull = closes[-1] > opens[-1]
    body_strong = body > avg_body * BODY_STRENGTH_MULT
    vol_strong = volumes[-1] > avg_volume * VOLUME_STRENGTH_MULT
    low_upper_wick = upper_wick < body * 0.25
    rsi_ok = 40 <= rsi_val <= 70
    ema_ok = closes[-1] > ema_val

    if is_bull and body_strong and vol_strong and low_upper_wick and rsi_ok and ema_ok:
        sl = closes[-1] - body * SL_MULT
        tp = closes[-1] + body * TP_MULT
        trail_points = closes[-1] * TRAIL_OFFSET
        place_order(SYMBOL, "BUY", TRADE_QTY, sl, tp, trailing_stop=trail_points)
        print(f"🔼 LONG | Price: {closes[-1]} | SL: {sl} | TP: {tp} | RSI: {rsi_val:.2f}")

    # Bearish conditions
    is_bear = closes[-1] < opens[-1]
    body_strong_bear = body > avg_body * BODY_STRENGTH_MULT
    vol_strong_bear = volumes[-1] > avg_volume * VOLUME_STRENGTH_MULT
    low_lower_wick = lower_wick < body * 0.25
    rsi_ok_bear = 30 <= rsi_val <= 60
    ema_ok_bear = closes[-1] < ema_val

    if is_bear and body_strong_bear and vol_strong_bear and low_lower_wick and rsi_ok_bear and ema_ok_bear:
        sl = closes[-1] + body * SL_MULT
        tp = closes[-1] - body * TP_MULT
        trail_points = closes[-1] * TRAIL_OFFSET
        place_order(SYMBOL, "SELL", TRADE_QTY, sl, tp, trailing_stop=trail_points)
        print(f"🔻 SHORT | Price: {closes[-1]} | SL: {sl} | TP: {tp} | RSI: {rsi_val:.2f}")
