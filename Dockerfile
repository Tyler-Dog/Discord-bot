FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Healthy = the bot wrote a heartbeat in the last 2 minutes (gateway connected, event loop alive).
HEALTHCHECK --interval=60s --timeout=5s --start-period=45s --retries=3 CMD ["python", "scripts/healthcheck.py"]

EXPOSE 8787
CMD ["python", "bot.py"]
