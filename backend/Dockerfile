FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache bust: change this value to force pip reinstall
ARG PIP_CACHE_BUST=2

COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "openai>=1.60.0" "httpx>=0.28.0" \
    && python -c "import ultralytics; print('ultralytics OK')" \
    && python -c "import boto3; print('boto3 OK')"

COPY . .

# Download YOLO model during build
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

RUN mkdir -p /app/storage/downloads /app/storage/processed /app/storage/temp
