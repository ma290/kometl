import os
import time
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
PING_URL = os.getenv("PING_URL")

# Binance Futures Testnet
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'

# Strategy config
LOT_SIZE = 0.03
RSI_PERIOD = 14
EMA_PERIOD = 50
VOLUME_STRENGTH = 2.0
BODY_STRENGTH = 2.0
TRAIL_PERCENT = 0.5 / 100
RR = 2.0

def get_candles():
    return client.futures_klines(symbol=SYMBOL, interval=Client.KLINE_INTERVAL_15MINUTE, limit=100)

def calc_indicators(candles):
    closes = [float(k[4]) for k in candles]
    opens = [float(k[1]) for k in candles]
    highs = [float(k[2]) for k in candles]
    lows = [float(k[3]) for k in candles]
    volumes = [float(k[5]) for k in candles]

    body = abs(closes[-1] - opens[-1])
    avg_body = sum([abs(c - o) for c, o in zip(closes[-21:], opens[-21:])]) / 20
    avg_vol = sum(volumes[-21:]) / 20
    upper_wick = highs[-1] - max(closes[-1], opens[-1])
    lower_wick = min(closes[-1], opens[-1]) - lows[-1]

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains[-RSI_PERIOD:]) / RSI_PERIOD if gains else 0.01
    avg_loss = sum(losses[-RSI_PERIOD:]) / RSI_PERIOD if losses else 0.01
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    ema = sum(closes[-EMA_PERIOD:]) / EMA_PERIOD

    return {
        "open": opens[-1],
        "close": closes[-1],
        "high": highs[-1],
        "low": lows[-1],
        "volume": volumes[-1],
        "avg_body": avg_body,
        "avg_vol": avg_vol,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "ema": ema,
        "rsi": rsi,
        "body": body
    }

def check_and_trade():
    candles = get_candles()
    data = calc_indicators(candles)
    cp = data["close"]

    bull = (
        data["close"] > data["open"] and
        data["body"] > data["avg_body"] * BODY_STRENGTH and
        data["volume"] > data["avg_vol"] * VOLUME_STRENGTH and
        data["upper_wick"] < data["body"] * 0.25 and
        40 <= data["rsi"] <= 70 and
        data["close"] > data["ema"]
    )

    bear = (
        data["close"] < data["open"] and
        data["body"] > data["avg_body"] * BODY_STRENGTH and
        data["volume"] > data["avg_vol"] * VOLUME_STRENGTH and
        data["lower_wick"] < data["body"] * 0.25 and
        30 <= data["rsi"] <= 60 and
        data["close"] < data["ema"]
    )

    if bull:
        sl = round(data["low"] - data["body"], 2)
        tp = round(cp + data["body"] * RR, 2)
        trail_offset = round(cp * TRAIL_PERCENT, 2)
        print(f"Going LONG at {cp} | SL: {sl} | TP: {tp}")
        place_order(SIDE_BUY, sl, tp, trail_offset)

    elif bear:
        sl = round(data["high"] + data["body"], 2)
        tp = round(cp - data["body"] * RR, 2)
        trail_offset = round(cp * TRAIL_PERCENT, 2)
        print(f"Going SHORT at {cp} | SL: {sl} | TP: {tp}")
        place_order(SIDE_SELL, sl, tp, trail_offset)

def place_order(side, stop_loss, take_profit, trail_offset):
    try:
        order = client.futures_create_order(
            symbol=SYMBOL,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=LOT_SIZE
        )
        print(f"Order placed: {order['orderId']}")
        opposite = SIDE_SELL if side == SIDE_BUY else SIDE_BUY

        # SL
        client.futures_create_order(
            symbol=SYMBOL,
            side=opposite,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=stop_loss,
            closePosition=True,
            timeInForce=TIME_IN_FORCE_GTC,
            reduceOnly=True
        )

        # TP
        client.futures_create_order(
            symbol=SYMBOL,
            side=opposite,
            type=ORDER_TYPE_TAKE_PROFIT_MARKET,
            stopPrice=take_profit,
            closePosition=True,
            timeInForce=TIME_IN_FORCE_GTC,
            reduceOnly=True
        )

        print("SL and TP orders placed.")

    except Exception as e:
        print(f"Error placing order: {e}")

# --- Ping server and HTTP server ---
def ping_url():
    while True:
        try:
            if PING_URL:
                requests.get(PING_URL)
                print("Pinged:", PING_URL)
        except:
            print("Ping failed.")
        time.sleep(300)

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")

def run_http_server():
    server = HTTPServer(("", 8080), PingHandler)
    server.serve_forever()

# --- Start everything ---
print("Bot is live...")

# Threads: HTTP + Ping + Main Loop
threading.Thread(target=run_http_server, daemon=True).start()
threading.Thread(target=ping_url, daemon=True).start()

while True:
    try:
        check_and_trade()
    except Exception as e:
        print("Runtime error:", e)
    time.sleep(1)
