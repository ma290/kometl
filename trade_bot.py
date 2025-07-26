import os
import asyncio
import aiohttp
import json
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
PING_URL = os.getenv("PING_URL", "https://www.google.com")

# === Strategy Parameters ===
TRADE_QTY = 0.01
RSI_PERIOD = 14
EMA_PERIOD = 50
BODY_MULT = 0.1
VOL_MULT = 0.1
RR_RATIO = 2.0
TRAIL_OFFSET = 0.5 / 100
RSI_BUY_MIN = 40
RSI_BUY_MAX = 70
RSI_SELL_MIN = 30
RSI_SELL_MAX = 60

# === Globals ===
active_trade = None  # Stores {"side": "BUY"/"SELL", "sl": price, "tp": price}

# === Initialize Binance Client ===
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'

# === Fetch Historical Candles ===
def get_latest_15m_candle():
    klines = client.futures_klines(symbol=SYMBOL, interval=Client.KLINE_INTERVAL_15MINUTE, limit=100)
    candles = [{
        "time": int(k[0]),
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
        "volume": float(k[5])
    } for k in klines]
    return candles

# === Indicator Calculations ===
def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def rsi(closes, period):
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, -1):
        change = closes[i + 1] - closes[i]
        if change >= 0:
            gains.append(change)
        else:
            losses.append(abs(change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# === Trade Signal Logic ===
def check_signal(candles):
    global active_trade
    if active_trade:
        return

    body = [abs(c["close"] - c["open"]) for c in candles]
    volume = [c["volume"] for c in candles]
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    opens = [c["open"] for c in candles]

    last = candles[-1]
    body_last = abs(last["close"] - last["open"])
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]

    avg_body = sma(body, 20)
    avg_vol = sma(volume, 20)
    ema = sma(closes, EMA_PERIOD)
    rsi_val = rsi(closes, RSI_PERIOD)

    if not all([avg_body, avg_vol, ema, rsi_val]):
        return

    if (
        last["close"] > last["open"] and
        body_last > avg_body * BODY_MULT and
        last["volume"] > avg_vol * VOL_MULT and
        upper_wick < body_last * 0.25 and
        RSI_BUY_MIN <= rsi_val <= RSI_BUY_MAX and
        last["close"] > ema
    ):
        sl = last["low"] - body_last
        tp = last["close"] + body_last * RR_RATIO
        place_order("BUY", sl, tp)

    elif (
        last["close"] < last["open"] and
        body_last > avg_body * BODY_MULT and
        last["volume"] > avg_vol * VOL_MULT and
        lower_wick < body_last * 0.25 and
        RSI_SELL_MIN <= rsi_val <= RSI_SELL_MAX and
        last["close"] < ema
    ):
        sl = last["high"] + body_last
        tp = last["close"] - body_last * RR_RATIO
        place_order("SELL", sl, tp)

# === Place Order ===
def place_order(side, sl, tp):
    global active_trade
    try:
        order = client.futures_create_order(
            symbol=SYMBOL,
            side=SIDE_BUY if side == "BUY" else SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=TRADE_QTY
        )
        active_trade = {"side": side, "sl": sl, "tp": tp}
        print(f"✅ {side} ORDER PLACED")
    except Exception as e:
        print("❌ Failed to place order:", e)

# === Monitor Exit Conditions (SL/TP) ===
async def monitor_price():
    global active_trade
    buffer = 0.1  # Price precision buffer
    while True:
        if active_trade:
            try:
                ticker = client.futures_ticker(symbol=SYMBOL)
                price = float(ticker['lastPrice'])
                sl = active_trade["sl"]
                tp = active_trade["tp"]
                side = active_trade["side"]

                print(f"[Monitor] Price: {price:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | Side: {side}")

                hit = (
                    (price <= sl + buffer if side == "BUY" else price >= sl - buffer) or
                    (price >= tp - buffer if side == "BUY" else price <= tp + buffer)
                )

                if hit:
                    exit_side = "SELL" if side == "BUY" else "BUY"
                    client.futures_create_order(
                        symbol=SYMBOL,
                        side=SIDE_SELL if exit_side == "SELL" else SIDE_BUY,
                        type=ORDER_TYPE_MARKET,
                        quantity=TRADE_QTY
                    )
                    print(f"📤 EXITED {side} trade @ price {price}")
                    active_trade = None
            except Exception as e:
                print("❌ Price check error:", e)
        await asyncio.sleep(1)

# === Health Check HTTP Server ===
async def handle_health(request):
    return web.json_response({"status": "running"})

def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    loop.run_until_complete(site.start())

# === Ping External URL to Keep Alive ===
async def ping_url():
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(PING_URL, timeout=5):
                    print("[Ping OK]", PING_URL)
            except:
                print("[Ping Error]", PING_URL)
            await asyncio.sleep(5)

# === Main Candle Logic Loop ===
async def main_loop():
    while True:
        candles = get_latest_15m_candle()
        check_signal(candles)
        await asyncio.sleep(60)

# === Start All Tasks ===
if __name__ == "__main__":
    start_http_server()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(
        monitor_price(),
        main_loop(),
        ping_url()
    ))
