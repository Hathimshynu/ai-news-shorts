# Optional: run the producer anywhere (local PC, any VPS). GitHub Actions does NOT need this.
#   docker build -t shorts .
#   docker run --env-file .env -v "$PWD/out:/app/out" -v "$PWD/data:/app/data" shorts
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core curl \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY renderer/package*.json renderer/
RUN cd renderer && npm install --no-audit --no-fund
COPY . .
CMD ["python", "-m", "pipeline.produce"]
