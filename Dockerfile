FROM python:3.11-slim

WORKDIR /app
COPY . /app

# Use a stable, working version with async socket support
RUN pip install --no-cache-dir \
    python-binance[async]==1.0.16 \
    python-dotenv \
    requests \
    aiohttp \
    websockets

EXPOSE 8080

CMD ["python", "main.py"]
