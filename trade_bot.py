import os
import asyncio
import aiohttp
import json
from datetime import datetime
from aiohttp import web
from binance import AsyncClient
from dotenv import load_dotenv

# === Load Environment Variables ===
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")  # KEEP AS-IS
PING_URL = os.getenv("PING_URL")

# === Strategy Parameters ===
TRADE_QTY = 0.01
rsi_period = 14
ema_period = 50
risk_reward_ratio = 2.0
body_strength_mult = 0.1
volume_strength_mult = 0.1
trail_offset_pct = 0.5
breakeven_buffer_pct = 0.2

rsiBuyMin, rsiBuyMax = 40, 70
rsiSellMin, rsiSellMax = 30, 60

# === State (RAM-based) ===
candles = []
position_open = False
open_side = None
entry_price = None
sl_price = None
tp_price = None
trail_active = False
breakeven_active = False

# === Ping Monitor ===
async def ping_url():
    if not PING_URL:
        return
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(PING_URL) as res:
                    print(f"[{datetime.now()}] Ping OK: {res.status}")
            except Exception as e:
                print(f"[Ping Error]: {e}")
            await asyncio.sleep(300)

def compute_rsi(closes, period):
    if len(closes) < period + 1:
        return 50
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rs = avg_gain / avg_loss if avg_loss else 0
    return 100 - (100 / (1 + rs)) if avg_loss else 100

def ema(values, period):
    if len(values) < period:
        return sum(values) / len(values)
    multiplier = 2 / (period + 1)
    ema_val = sum(values[:period]) / period
    for val in values[period:]:
        ema_val = (val - ema_val) * multiplier + ema_val
    return ema_val

def calc_trade_signal():
    if len(candles) < max(rsi_period + 1, ema_period):
        return None
    latest = candles[-1]
    close = latest["close"]
    open_ = latest["open"]
    high = latest["high"]
    low = latest["low"]
    volume = latest["volume"]
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    body = abs(close - open_)
    avg_body = sum(abs(c["close"] - c["open"]) for c in candles[-20:]) / 20
    avg_vol = sum(volumes[-20:]) / 20
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    rsi = compute_rsi(closes, rsi_period)
    ema_val = ema(closes, ema_period)

    is_bull = close > open_ and all([
        body > avg_body * body_strength_mult,
        volume > avg_vol * volume_strength_mult,
        upper_wick < body * 0.25,
        rsiBuyMin <= rsi <= rsiBuyMax,
        close > ema_val
    ])

    is_bear = close < open_ and all([
        body > avg_body * body_strength_mult,
        volume > avg_vol * volume_strength_mult,
        lower_wick < body * 0.25,
        rsiSellMin <= rsi <= rsiSellMax,
        close < ema_val
    ])

    if is_bull:
        sl = low - body
        tp = close + body * risk_reward_ratio
        return "BUY", sl, tp, close
    elif is_bear:
        sl = high + body
        tp = close - body * risk_reward_ratio
        return "SELL", sl, tp, close
    return None

async def place_order(client, side, sl, tp, entry):
    global position_open, open_side, entry_price, sl_price, tp_price, trail_active, breakeven_active
    try:
        await client.futures_create_order(
            symbol=SYMBOL,
            side=side,
            type="MARKET",
            quantity=TRADE_QTY,
        )
        print(f"{side} ORDER PLACED @ {entry}")
        position_open = True
        open_side = side
        entry_price = entry
        sl_price = sl
        tp_price = tp
        trail_active = True
        breakeven_active = True
    except Exception as e:
        print(f"[Order Error]: {e}")

async def exit_trade(client, reason):
    global position_open
    try:
        exit_side = "SELL" if open_side == "BUY" else "BUY"
        await client.futures_create_order(
            symbol=SYMBOL,
            side=exit_side,
            type="MARKET",
            quantity=TRADE_QTY,
            reduceOnly=True
        )
        print(f"[EXIT TRADE]: {reason}")
        position_open = False
    except Exception as e:
        print(f"[Exit Error]: {e}")

# === Real-time SL/TP Monitor ===
async def monitor_price(client):
    global sl_price, tp_price, trail_active, breakeven_active, position_open
    url = f"wss://fstream.binance.com/ws/{SYMBOL.lower()}@markPrice"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url) as ws:
                    async for msg in ws:
                        try:
                            data = json.loads(msg.data)
                            price = float(data["p"])
                            if not position_open:
                                continue

                            offset = entry_price * (trail_offset_pct / 100)

                            if trail_active:
                                if open_side == "BUY" and price - offset > sl_price:
                                    sl_price = price - offset
                                elif open_side == "SELL" and price + offset < sl_price:
                                    sl_price = price + offset

                            if breakeven_active:
                                threshold = entry_price * (1 + breakeven_buffer_pct / 100) if open_side == "BUY" else entry_price * (1 - breakeven_buffer_pct / 100)
                                if (open_side == "BUY" and price >= threshold) or (open_side == "SELL" and price <= threshold):
                                    sl_price = entry_price
                                    breakeven_active = False
                                    print("🔐 Breakeven SL activated")

                            if position_open and (
                                (open_side == "BUY" and (price <= sl_price or price >= tp_price)) or
                                (open_side == "SELL" and (price >= sl_price or price <= tp_price))
                            ):
                                position_open = False
                                await exit_trade(client, "🎯 TP or SL hit")

                        except Exception as e:
                            print(f"[Monitor Parse Error]: {e}")
        except Exception as e:
            print(f"[Reconnect WS Error]: {e}")
            await asyncio.sleep(5)

async def fetch_candles():
    url = f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={SYMBOL}&interval=1m&limit=100"
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(url) as res:
                    raw = await res.json()
                    candles.clear()
                    for c in raw:
                        candles.append({
                            "time": c[0],
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5])
                        })
            except Exception as e:
                print(f"[Candle Error]: {e}")
            await asyncio.sleep(60)

async def trade_loop(client):
    while True:
        if not position_open:
            signal = calc_trade_signal()
            if signal:
                side, sl, tp, entry = signal
                await place_order(client, side, sl, tp, entry)
        await asyncio.sleep(5)

async def handle_http(_): return web.Response(text="Bot running")

async def start_http():
    app = web.Application()
    app.router.add_get("/", handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    try:
        await asyncio.gather(
            ping_url(),
            fetch_candles(),
            trade_loop(client),
            monitor_price(client),
            start_http(),
        )
    finally:
        await client.close_connection()

if __name__ == "__main__":
    asyncio.run(main())
