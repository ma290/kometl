# trade_bot.py
import os
import asyncio
import json
import aiohttp
import math
from decimal import Decimal
from dotenv import load_dotenv
from binance import AsyncClient, BinanceSocketManager
from datetime import datetime
from aiohttp import web  # Added for HTTP server

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
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(PING_URL) as resp:
                    print(f"[{datetime.now()}] Ping sent: {resp.status}")
        except Exception as e:
            print(f"Ping error: {e}")
        await asyncio.sleep(300)


def compute_rsi(closes, period):
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    return 100 - (100 / (1 + rs))


def ema(values, period):
    multiplier = 2 / (period + 1)
    ema_vals = [sum(values[:period]) / period]
    for price in values[period:]:
        ema_vals.append((price - ema_vals[-1]) * multiplier + ema_vals[-1])
    return ema_vals[-1]


def calc_trade_logic():
    if len(candles) < max(rsi_period + 1, ema_period):
        return None

    close = candles[-1]["close"]
    open_ = candles[-1]["open"]
    high = candles[-1]["high"]
    low = candles[-1]["low"]
    volume = candles[-1]["volume"]

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
    body_strong_bull = body > avg_body * body_strength_mult
    vol_strong_bull = volume > avg_vol * volume_strength_mult
    low_upper_wick = upper_wick < body * 0.25
    rsi_ok_bull = rsiBuyMin <= rsi <= rsiBuyMax
    ema_ok_bull = close > ema_val

    bull_confluence = all([is_bull, body_strong_bull, vol_strong_bull, low_upper_wick, rsi_ok_bull, ema_ok_bull])

    is_bear = close < open_
    body_strong_bear = body > avg_body * body_strength_mult
    vol_strong_bear = volume > avg_vol * volume_strength_mult
    low_lower_wick = lower_wick < body * 0.25
    rsi_ok_bear = rsiSellMin <= rsi <= rsiSellMax
    ema_ok_bear = close < ema_val

    bear_confluence = all([is_bear, body_strong_bear, vol_strong_bear, low_lower_wick, rsi_ok_bear, ema_ok_bear])

    if bull_confluence:
        sl = low - body
        tp = close + body * risk_reward_ratio
        return "BUY", sl, tp, close
    elif bear_confluence:
        sl = high + body
        tp = close - body * risk_reward_ratio
        return "SELL", sl, tp, close
    return None


async def order_side(client, side, sl, tp, entry):
    global position_open, open_side, sl_price, tp_price, entry_price, trail_active, breakeven_active
    try:
        print(f"Placing {side} order...")
        order = await client.futures_create_order(
            symbol=SYMBOL.upper(),
            side="BUY" if side == "BUY" else "SELL",
            type="MARKET",
            quantity=TRADE_QTY,
        )
        print(f"Order Placed: {order}")
        position_open = True
        open_side = side
        sl_price = sl
        tp_price = tp
        entry_price = entry
        trail_active = True
        breakeven_active = True
    except Exception as e:
        print(f"Order Error: {e}")


async def exit_trade(client, reason):
    global position_open, open_side
    try:
        side = "SELL" if open_side == "BUY" else "BUY"
        order = await client.futures_create_order(
            symbol=SYMBOL.upper(),
            side=side,
            type="MARKET",
            quantity=TRADE_QTY,
        )
        print(f"Exited trade due to {reason}. Order: {order}")
        position_open = False
    except Exception as e:
        print(f"Exit Error: {e}")


async def price_monitor(client):
    global sl_price, tp_price, trail_active, breakeven_active
    async with BinanceSocketManager(client).symbol_mark_price_socket(SYMBOL.upper()) as stream:
        async for msg in stream:
            if "p" in msg:
                price = float(msg["p"])
                if position_open:
                    offset = entry_price * (trail_offset_pct / 100)
                    if trail_active:
                        if open_side == "BUY" and price - offset > sl_price:
                            sl_price = price - offset
                        elif open_side == "SELL" and price + offset < sl_price:
                            sl_price = price + offset

                    if breakeven_active:
                        buffer = entry_price * 0.002
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


async def candle_collector():
    while True:
        await asyncio.sleep(1)
        url = f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={SYMBOL.upper()}&interval=1m&limit=100"
        try:
            async with aiohttp.ClientSession() as session:
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
            print(f"Candle fetch error: {e}")


async def trade_handler(client):
    while True:
        if not position_open:
            signal = calc_trade_logic()
            if signal:
                side, sl, tp, entry = signal
                await order_side(client, side, sl, tp, entry)
        await asyncio.sleep(1)


# Minimal HTTP server to keep container alive
async def handle_http(_):
    return web.Response(text="Bot is alive.")

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()


async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    await asyncio.gather(
        ping_url(),
        candle_collector(),
        trade_handler(client),
        price_monitor(client),
        start_http_server(),
    )

if __name__ == "__main__":
    asyncio.run(main())


