import os
import asyncio
import aiohttp
import time
from datetime import datetime
from aiohttp import web
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

# === Load Environment Variables ===
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
PING_URL = os.getenv("PING_URL")  # ✅ Re-added

# === Strategy Parameters ===
TRADE_QTY = 0.01
RSI_PERIOD = 14
EMA_PERIOD = 50
BODY_MULT = 0.1
VOL_MULT = 0.1
SL_MULT = 2.0
TP_MULT = 4.0
TRAIL_OFFSET = 0.5 / 100  # 0.5%
RSI_BUY_MIN = 40
RSI_BUY_MAX = 70
RSI_SELL_MIN = 30
RSI_SELL_MAX = 60

client = Client(API_KEY, API_SECRET, testnet=True)
current_position = None
entry_price = None

async def ping_url_periodically():
    while True:
        if PING_URL:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.get(PING_URL)
                    print(f"[{datetime.utcnow()}] Pinged {PING_URL}")
            except Exception as e:
                print(f"Ping error: {e}")
        await asyncio.sleep(600)

async def fetch_klines(symbol, interval, limit):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: client.get_klines(symbol=symbol, interval=interval, limit=limit))

def calculate_indicators(candles):
    closes = [float(x[4]) for x in candles]
    opens = [float(x[1]) for x in candles]
    highs = [float(x[2]) for x in candles]
    lows = [float(x[3]) for x in candles]
    volumes = [float(x[5]) for x in candles]

    close = closes[-1]
    open_ = opens[-1]
    high = highs[-1]
    low = lows[-1]
    volume = volumes[-1]

    body = abs(close - open_)
    avg_body = sum([abs(closes[i] - opens[i]) for i in range(-20, 0)]) / 20
    avg_vol = sum(volumes[-20:]) / 20
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low

    rsi = calculate_rsi(closes, RSI_PERIOD)
    ema = sum(closes[-EMA_PERIOD:]) / EMA_PERIOD

    indicators = {
        "close": close,
        "open": open_,
        "body": body,
        "avg_body": avg_body,
        "volume": volume,
        "avg_vol": avg_vol,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "rsi": rsi,
        "ema": ema
    }
    return indicators

def calculate_rsi(closes, period):
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [delta if delta > 0 else 0 for delta in deltas]
    losses = [-delta if delta < 0 else 0 for delta in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi = 100 - (100 / (1 + rs))
    return rsi

def should_enter_long(ind):
    return (
        ind["close"] > ind["open"] and
        ind["body"] > ind["avg_body"] * BODY_MULT and
        ind["volume"] > ind["avg_vol"] * VOL_MULT and
        ind["upper_wick"] < ind["body"] * 0.25 and
        RSI_BUY_MIN <= ind["rsi"] <= RSI_BUY_MAX and
        ind["close"] > ind["ema"]
    )

def should_enter_short(ind):
    return (
        ind["close"] < ind["open"] and
        ind["body"] > ind["avg_body"] * BODY_MULT and
        ind["volume"] > ind["avg_vol"] * VOL_MULT and
        ind["lower_wick"] < ind["body"] * 0.25 and
        RSI_SELL_MIN <= ind["rsi"] <= RSI_SELL_MAX and
        ind["close"] < ind["ema"]
    )

async def place_order(side, quantity, sl, tp):
    try:
        client.futures_create_order(symbol=SYMBOL, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)
        sl_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
        tp_side = sl_side

        client.futures_create_order(
            symbol=SYMBOL,
            side=sl_side,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=round(sl, 2),
            closePosition=True,
            timeInForce="GTE_GTC"
        )
        client.futures_create_order(
            symbol=SYMBOL,
            side=tp_side,
            type=ORDER_TYPE_TAKE_PROFIT_MARKET,
            stopPrice=round(tp, 2),
            closePosition=True,
            timeInForce="GTE_GTC"
        )
        print(f"Trade Placed: {side} | SL: {sl:.2f} | TP: {tp:.2f}")
    except Exception as e:
        print(f"Order placement error: {e}")

async def strategy_loop():
    global current_position
    while True:
        try:
            candles = await fetch_klines(SYMBOL, Client.KLINE_INTERVAL_1MINUTE, 100)
            indicators = calculate_indicators(candles)

            if current_position is None:
                if should_enter_long(indicators):
                    sl = indicators["close"] - indicators["body"] * SL_MULT
                    tp = indicators["close"] + indicators["body"] * TP_MULT
                    await place_order(SIDE_BUY, TRADE_QTY, sl, tp)
                    current_position = "LONG"
                elif should_enter_short(indicators):
                    sl = indicators["close"] + indicators["body"] * SL_MULT
                    tp = indicators["close"] - indicators["body"] * TP_MULT
                    await place_order(SIDE_SELL, TRADE_QTY, sl, tp)
                    current_position = "SHORT"

            positions = client.futures_position_information(symbol=SYMBOL)
            pos_amt = float([p for p in positions if p["symbol"] == SYMBOL][0]["positionAmt"])
            if pos_amt == 0:
                current_position = None

        except Exception as e:
            print(f"Strategy error: {e}")
        await asyncio.sleep(1)

async def start_server():
    async def health(request):
        return web.Response(text="OK")
    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(
        start_server(),
        strategy_loop(),
        ping_url_periodically()
    ))
