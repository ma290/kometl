import os
import asyncio
import aiohttp
from datetime import datetime
from aiohttp import web
from binance import AsyncClient, BinanceSocketManager
from binance.enums import *
from dotenv import load_dotenv

# === Load Environment Variables ===
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
PING_URL = os.getenv("PING_URL")

# === Strategy Parameters ===
RSI_PERIOD = 14
EMA_PERIOD = 50
BODY_MULT = 0.1
VOL_MULT = 0.1
SL_MULT = 2.0
TP_MULT = 4.0
TRAIL_OFFSET = 0.5 / 100
RSI_BUY_MIN = 40
RSI_BUY_MAX = 70
RSI_SELL_MIN = 30
RSI_SELL_MAX = 60
TRADE_QTY = 0.01

# === In-Memory Tracking ===
active_position = None

async def fetch_klines(client):
    klines = await client.futures_klines(symbol=SYMBOL, interval=AsyncClient.KLINE_INTERVAL_1MINUTE, limit=100)
    return klines

def calculate_indicators(klines):
    closes = [float(k[4]) for k in klines]
    opens = [float(k[1]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    body = abs(closes[-1] - opens[-1])
    avg_body = sum(abs(c - o) for c, o in zip(closes, opens)) / len(closes)
    avg_vol = sum(volumes) / len(volumes)
    upper_wick = highs[-1] - max(closes[-1], opens[-1])
    lower_wick = min(closes[-1], opens[-1]) - lows[-1]

    gains, losses = [], []
    for i in range(1, RSI_PERIOD + 1):
        delta = closes[-i] - closes[-i - 1]
        (gains if delta > 0 else losses).append(abs(delta))
    avg_gain = sum(gains) / RSI_PERIOD
    avg_loss = sum(losses) / RSI_PERIOD
    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi = 100 - (100 / (1 + rs))

    ema = sum(closes[-EMA_PERIOD:]) / EMA_PERIOD

    return {
        "close": closes[-1],
        "open": opens[-1],
        "body": body,
        "avg_body": avg_body,
        "volume": volumes[-1],
        "avg_vol": avg_vol,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "rsi": rsi,
        "ema": ema
    }

def check_signals(ind):
    is_bull = ind["close"] > ind["open"]
    bull_confluence = (
        is_bull and
        ind["body"] > ind["avg_body"] * BODY_MULT and
        ind["volume"] > ind["avg_vol"] * VOL_MULT and
        ind["upper_wick"] < ind["body"] * 0.25 and
        RSI_BUY_MIN <= ind["rsi"] <= RSI_BUY_MAX and
        ind["close"] > ind["ema"]
    )

    is_bear = ind["close"] < ind["open"]
    bear_confluence = (
        is_bear and
        ind["body"] > ind["avg_body"] * BODY_MULT and
        ind["volume"] > ind["avg_vol"] * VOL_MULT and
        ind["lower_wick"] < ind["body"] * 0.25 and
        RSI_SELL_MIN <= ind["rsi"] <= RSI_SELL_MAX and
        ind["close"] < ind["ema"]
    )

    return bull_confluence, bear_confluence

async def place_trade(client, side, entry_price, sl, tp):
    global active_position
    try:
        await client.futures_create_order(
            symbol=SYMBOL,
            side=SIDE_BUY if side == "BUY" else SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=TRADE_QTY
        )

        stop_price = round(sl, 2)
        tp_price = round(tp, 2)

        await client.futures_create_order(
            symbol=SYMBOL,
            side=SIDE_SELL if side == "BUY" else SIDE_BUY,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=stop_price,
            closePosition=True,
            timeInForce=TIME_IN_FORCE_GTC
        )

        await client.futures_create_order(
            symbol=SYMBOL,
            side=SIDE_SELL if side == "BUY" else SIDE_BUY,
            type=ORDER_TYPE_TAKE_PROFIT_MARKET,
            stopPrice=tp_price,
            closePosition=True,
            timeInForce=TIME_IN_FORCE_GTC
        )

        active_position = {"side": side, "entry": entry_price, "sl": stop_price, "tp": tp_price}
        print(f"Trade Placed: {side} | Entry: {entry_price} | SL: {stop_price} | TP: {tp_price}")

    except Exception as e:
        print("Trade Error:", e)

async def strategy_loop(client):
    global active_position
    while True:
        try:
            klines = await fetch_klines(client)
            ind = calculate_indicators(klines)
            bull, bear = check_signals(ind)

            if not active_position:
                if bull:
                    sl = ind["close"] - ind["body"] * SL_MULT
                    tp = ind["close"] + ind["body"] * TP_MULT
                    await place_trade(client, "BUY", ind["close"], sl, tp)
                elif bear:
                    sl = ind["close"] + ind["body"] * SL_MULT
                    tp = ind["close"] - ind["body"] * TP_MULT
                    await place_trade(client, "SELL", ind["close"], sl, tp)
        except Exception as e:
            print("Strategy Loop Error:", e)

        await asyncio.sleep(1)

async def ping_loop():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(PING_URL)
                print(f"Ping sent: {datetime.now().isoformat()}")
        except:
            print("Ping failed")
        await asyncio.sleep(600)

async def health_server():
    async def handle(request):
        return web.Response(text="Bot is running.")

    app = web.Application()
    app.router.add_get('/ping', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
    await asyncio.gather(
        strategy_loop(client),
        ping_loop(),
        health_server()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user")
