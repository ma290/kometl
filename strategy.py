from trade_execution import get_last_closed_candle, place_order, SL_MULT, TP_MULT

# Track last processed candle time
last_candle_time = None

def calculate_trade_prices(open_price, close_price, sl_mult, tp_mult):
    body = abs(close_price - open_price)
    sl_price = round(close_price - body * sl_mult, 2) if close_price > open_price else round(close_price + body * sl_mult, 2)
    tp_price = round(close_price + body * tp_mult, 2) if close_price > open_price else round(close_price - body * tp_mult, 2)
    return sl_price, tp_price

def check_confluence_conditions(open_price, close_price):
    # Example logic, update as needed
    return close_price > open_price

def execute_strategy():
    global last_candle_time

    open_price, close_price, candle_close_time = get_last_closed_candle("BTCUSDT")

    # Avoid placing multiple trades on same candle
    if candle_close_time == last_candle_time:
        return

    print(f"🕒 Candle Time: {candle_close_time} | Open: {open_price} | Close: {close_price}")

    if check_confluence_conditions(open_price, close_price):
        sl_price, tp_price = calculate_trade_prices(open_price, close_price, SL_MULT, TP_MULT)
        side = 'BUY' if close_price > open_price else 'SELL'
        place_order(side, close_price, sl_price, tp_price)
        print(f"✅ Trade placed for candle at {candle_close_time}")
    else:
        print("⚠️ No trade condition met.")

    # Update last processed candle
    last_candle_time = candle_close_time
