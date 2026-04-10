FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt backend/requirements.txt* ./
# When built from repo root, prefer backend/requirements.txt
RUN if [ -f backend/requirements.txt ]; then cp backend/requirements.txt requirements.txt; fi
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir \
        "openai>=1.60.0" \
        "httpx>=0.28.0" \
        "ultralytics>=8.3.0" \
        "mediapipe==0.10.14" \
        "python-jose[cryptography]==3.3.0" \
        "passlib[bcrypt]==1.7.4" \
        "bcrypt==4.1.3" \
        "google-api-python-client==2.131.0" \
        "google-auth-oauthlib==1.2.0" \
        "google-auth-httplib2==0.2.0" \
        "cryptography==42.0.8" \
        "google-genai>=1.0.0" \
        "boto3>=1.34.0"

COPY . .

# When Railway builds with repo root as context, main.py ends up in backend/
RUN if [ -f backend/main.py ]; then cp -r backend/* . ; fi

# Download YOLO model during build
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

RUN mkdir -p /app/storage/downloads /app/storage/processed /app/storage/temp

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
