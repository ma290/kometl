import os
import asyncio
import json
import aiohttp
from dotenv import load_dotenv
from binance import AsyncClient, BinanceSocketManager
from datetime import datetime
from aiohttp import web

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "btcusdt").lower()
PING_URL = os.getenv("PING_URL")

TRADE_QTY = 0.01
rsi_period = 14
ema_period = 50
body_strength_mult = 1.0
volume_strength_mult = 1.0
risk_reward_ratio = 2.0
trail_offset_pct = 0.5

rsiBuyMin = 40
rsiBuyMax = 70
rsiSellMin = 30
rsiSellMax = 60

candles = []
position_open = False
open_side = None
sl_price = None
tp_price = None
entry_price = None
trail_active = False
breakeven_active = False

async def ping_url():
    if not PING_URL:
        return
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(PING_URL) as resp:
                    print(f"[{datetime.now()}] Ping: {resp.status}")
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

def calc_trade_logic():
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
    avg_body = sum([abs(c["close"] - c["open"]) for c in candles[-20:]]) / 20
    avg_vol = sum(volumes[-20:]) / 20
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    rsi = compute_rsi(closes, rsi_period)
    ema_val = ema(closes, ema_period)

    is_bull = close > open_
    bull_conditions = [
        is_bull,
        body > avg_body * body_strength_mult,
        volume > avg_vol * volume_strength_mult,
        upper_wick < body * 0.25,
        rsiBuyMin <= rsi <= rsiBuyMax,
        close > ema_val
    ]

    is_bear = close < open_
    bear_conditions = [
        is_bear,
        body > avg_body * body_strength_mult,
        volume > avg_vol * volume_strength_mult,
        lower_wick < body * 0.25,
        rsiSellMin <= rsi <= rsiSellMax,
        close < ema_val
    ]

    if all(bull_conditions):
        sl = low - body
        tp = close + body * risk_reward_ratio
        return "BUY", sl, tp, close
    elif all(bear_conditions):
        sl = high + body
        tp = close - body * risk_reward_ratio
        return "SELL", sl, tp, close
    return None

async def order_side(client, side, sl, tp, entry):
    global position_open, open_side, sl_price, tp_price, entry_price, trail_active, breakeven_active
    try:
        order = await client.futures_create_order(
            symbol=SYMBOL.upper(),
            side=side,
            type="MARKET",
            quantity=TRADE_QTY,
        )
        print(f"{side} ORDER: {order}")
        position_open = True
        open_side = side
        sl_price = sl
        tp_price = tp
        entry_price = entry
        trail_active = True
        breakeven_active = True
    except Exception as e:
        print(f"[Order Error]: {e}")

async def exit_trade(client, reason):
    global position_open
    try:
        side = "SELL" if open_side == "BUY" else "BUY"
        order = await client.futures_create_order(
            symbol=SYMBOL.upper(),
            side=side,
            type="MARKET",
            quantity=TRADE_QTY,
        )
        print(f"EXIT {reason}: {order}")
        position_open = False
    except Exception as e:
        print(f"[Exit Error]: {e}")

async def price_monitor(client):
    global sl_price, tp_price, trail_active, breakeven_active
    bsm = BinanceSocketManager(client)
    socket = bsm.mark_price_socket(SYMBOL.upper())
    async with socket as stream:
        async for msg in stream:
            try:
                price = float(msg["p"])
                if position_open:
                    offset = entry_price * (trail_offset_pct / 100)
                    if trail_active:
                        if open_side == "BUY" and price - offset > sl_price:
                            sl_price = price - offset
                        elif open_side == "SELL" and price + offset < sl_price:
                            sl_price = price + offset

                    buffer = entry_price * 0.002
                    if breakeven_active:
                        if open_side == "BUY" and price >= entry_price + buffer:
                            sl_price = entry_price
                            breakeven_active = False
                        elif open_side == "SELL" and price <= entry_price - buffer:
                            sl_price = entry_price
                            breakeven_active = False

                    if open_side == "BUY" and (price <= sl_price or price >= tp_price):
                        await exit_trade(client, "SL/TP HIT")
                    elif open_side == "SELL" and (price >= sl_price or price <= tp_price):
                        await exit_trade(client, "SL/TP HIT")
            except Exception as e:
                print(f"[Monitor Error]: {e}")

async def candle_collector():
    url = f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={SYMBOL.upper()}&interval=1m&limit=100"
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(url) as res:
                    data = await res.json()
                    candles.clear()
                    for c in data:
                        candles.append({
                            "time": c[0],
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5])
                        })
            except Exception as e:
                print(f"[Candle Fetch Error]: {e}")
            await asyncio.sleep(60)

async def trade_handler(client):
    while True:
        if not position_open:
            signal = calc_trade_logic()
            if signal:
                side, sl, tp, entry = signal
                await order_side(client, side, sl, tp, entry)
        await asyncio.sleep(5)

async def handle_http(_):
    return web.Response(text="Bot is running.")

async def start_http_server():
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
            candle_collector(),
            trade_handler(client),
            price_monitor(client),
            start_http_server(),
        )
    finally:
        await client.close_connection()

if __name__ == "__main__":
    asyncio.run(main())
