import os
from binance.client import Client
from dotenv import load_dotenv

# Load .env variables
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
TRADE_QTY = float(os.getenv("TRADE_QTY", 0.01))
SL_MULT = float(os.getenv("SL_MULT", 1.0))
TP_MULT = float(os.getenv("TP_MULT", 2.0))
TIME_FRAME = os.getenv("TIME_FRAME", "1m")

# Setup Binance Futures Testnet client
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'

def get_last_closed_candle(symbol):
    klines = client.futures_klines(symbol=symbol, interval=TIME_FRAME, limit=2)
    last = klines[-2]  # Closed candle
    open_price = float(last[1])
    close_price = float(last[4])
    close_time = int(last[6])  # ms timestamp
    return open_price, close_price, close_time

def place_order(side, entry_price, sl_price, tp_price):
    print(f"[{side}] Entry: {entry_price}, SL: {sl_price}, TP: {tp_price}")

    # Safety checks
    if side == "BUY":
        if sl_price >= entry_price or tp_price <= entry_price:
            print("❌ Invalid SL/TP for BUY trade. Skipping.")
            return
    else:
        if sl_price <= entry_price or tp_price >= entry_price:
            print("❌ Invalid SL/TP for SELL trade. Skipping.")
            return

    # Market entry
    client.futures_create_order(
        symbol=SYMBOL,
        side=side,
        type='MARKET',
        quantity=TRADE_QTY
    )

    # Stop loss
    client.futures_create_order(
        symbol=SYMBOL,
        side='SELL' if side == 'BUY' else 'BUY',
        type='STOP_MARKET',
        stopPrice=sl_price,
        closePosition=True,
        timeInForce='GTC'
    )

    # Take profit
    client.futures_create_order(
        symbol=SYMBOL,
        side='SELL' if side == 'BUY' else 'BUY',
        type='TAKE_PROFIT_MARKET',
        stopPrice=tp_price,
        closePosition=True,
        timeInForce='GTC'
    )

    print(f"📈 {side} trade executed successfully.")
