FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir "openai>=1.60.0" "httpx>=0.28.0"

COPY . .

# Скачиваем модели при сборке
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

RUN mkdir -p /app/storage/downloads /app/storage/processed /app/storage/temp
