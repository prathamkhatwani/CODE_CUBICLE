# Multi-stage Dockerfile for CORTEX Edge Memory Platform
FROM python:3.11-slim

WORKDIR /app

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source tree
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Set working directory to backend
WORKDIR /app/backend

# Pre-download FastEmbed model into cache to ensure offline readiness
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV EDGE_QDRANT_PATH="./qdrant_edge_data"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
