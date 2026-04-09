FROM python:3.12-slim

# Install ffmpeg (required by yt-dlp for merging video+audio)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Pass your token via environment variable at runtime:
#   docker run -e TELEGRAM_BOT_TOKEN=xxx reclip-bot
CMD ["python", "bot.py"]
