import time
import asyncio
import os
from dotenv import load_dotenv
from aiohttp import web, ClientSession
from strategy import execute_strategy

# Load environment variables
load_dotenv()
PING_URL = os.getenv("PING_URL")

# === AIOHTTP server setup ===
async def handle(request):
    return web.Response(text="Trading bot is running.")

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=8080)
    await site.start()

# === Ping URL every 10 minutes ===
async def ping_periodically():
    while True:
        try:
            async with ClientSession() as session:
                async with session.get(PING_URL, timeout=10) as response:
                    print(f"[PING] Sent to {PING_URL}, status: {response.status}")
        except Exception as e:
            print(f"[PING ERROR] {e}")
        await asyncio.sleep(600)  # Wait 10 minutes

# === Trading Strategy Loop ===
async def run_strategy_loop():
    while True:
        try:
            execute_strategy()
        except Exception as e:
            print(f"[ERROR] {e}")
        await asyncio.sleep(1)  # check every second

# === Main entry point ===
async def main():
    print("🚀 Starting trading bot with HTTP server and ping...")
    await asyncio.gather(
        start_http_server(),
        ping_periodically(),
        run_strategy_loop()
    )

if __name__ == "__main__":
    asyncio.run(main())
