# trade_bot.py
import os, asyncio, aiohttp
from dotenv import load_dotenv
from binance import AsyncClient, BinanceSocketManager
from aiohttp import web
from datetime import datetime

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "btcusdt").lower()
PING_URL = os.getenv("PING_URL")

TRADE_QTY = 0.01
rsi_period = 14
ema_period = 50
candles = []

# Global state
position_open = False
open_side = None
entry_price = sl_price = tp_price = None
trail_active = breakeven_active = False

# === Indicators ===
def compute_rsi(closes, period):
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
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
    c = candles[-1]
    close, open_, high, low, volume = c["close"], c["open"], c["high"], c["low"], c["volume"]
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    body = abs(close - open_)
    avg_body = sum(abs(c["close"] - c["open"]) for c in candles[-20:]) / 20
    avg_vol = sum(volumes[-20:]) / 20
    rsi = compute_rsi(closes, rsi_period)
    ema_val = ema(closes, ema_period)

    bullish = (close > open_ and body > avg_body and volume > avg_vol and rsi > 45 and close > ema_val)
    bearish = (close < open_ and body > avg_body and volume > avg_vol and rsi < 55 and close < ema_val)

    if bullish:
        sl, tp = low - body, close + body * 2
        return "BUY", sl, tp, close
    elif bearish:
        sl, tp = high + body, close - body * 2
        return "SELL", sl, tp, close
    return None

# === Trade Functions ===
async def order_side(client, side, sl, tp, entry):
    global position_open, open_side, sl_price, tp_price, entry_price, trail_active, breakeven_active
    try:
        order = await client.futures_create_order(
            symbol=SYMBOL.upper(), side=side, type="MARKET", quantity=TRADE_QTY)
        position_open = True
        open_side, sl_price, tp_price, entry_price = side, sl, tp, entry
        trail_active = breakeven_active = True
        print(f"{side} order executed. SL: {sl:.2f}, TP: {tp:.2f}")
    except Exception as e:
        print(f"Order error: {e}")

async def exit_trade(client, reason):
    global position_open
    try:
        side = "SELL" if open_side == "BUY" else "BUY"
        await client.futures_create_order(symbol=SYMBOL.upper(), side=side, type="MARKET", quantity=TRADE_QTY)
        print(f"Exited due to {reason}")
        position_open = False
    except Exception as e:
        print(f"Exit error: {e}")

# === Candle Collector ===
async def candle_collector():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={SYMBOL.upper()}&interval=1m&limit=100"
                ) as res:
                    data = await res.json()
                    candles.clear()
                    for c in data:
                        candles.append({
                            "open": float(c[1]), "high": float(c[2]),
                            "low": float(c[3]), "close": float(c[4]),
                            "volume": float(c[5])
                        })
        except Exception as e:
            print(f"Candle fetch error: {e}")
        await asyncio.sleep(15)

# === Price Monitor ===
async def price_monitor(client):
    global sl_price, tp_price, trail_active, breakeven_active
    bsm = BinanceSocketManager(client)
    async with bsm.mark_price_socket(SYMBOL.upper()) as stream:
        async for msg in stream:
            try:
                price = float(msg["p"])
                if position_open:
                    offset = entry_price * 0.005
                    if trail_active:
                        if open_side == "BUY" and price - offset > sl_price:
                            sl_price = price - offset
                        elif open_side == "SELL" and price + offset < sl_price:
                            sl_price = price + offset
                    if breakeven_active:
                        if open_side == "BUY" and price >= entry_price * 1.002:
                            sl_price = entry_price
                            breakeven_active = False
                        elif open_side == "SELL" and price <= entry_price * 0.998:
                            sl_price = entry_price
                            breakeven_active = False
                    if (open_side == "BUY" and (price <= sl_price or price >= tp_price)) or \
                       (open_side == "SELL" and (price >= sl_price or price <= tp_price)):
                        await exit_trade(client, "TP or SL hit")
            except Exception as e:
                print(f"Price monitor error: {e}")

# === HTTP Health Server ===
async def handle_http(_): return web.Response(text="Bot running")
async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

# === Trade Logic ===
async def trade_handler(client):
    while True:
        if not position_open:
            signal = calc_trade_logic()
            if signal:
                await order_side(client, *signal)
        await asyncio.sleep(5)

# === Main ===
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    try:
        await asyncio.gather(
            start_http_server(),
            candle_collector(),
            trade_handler(client),
            price_monitor(client)
        )
    finally:
        await client.close_connection()

if __name__ == "__main__":
    asyncio.run(main())

