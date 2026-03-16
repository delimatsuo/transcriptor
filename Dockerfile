# T.A.R.S. backend for Cloud Run
FROM python:3.12-slim

WORKDIR /app

# System deps for soundfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/

# Security: run as non-root
RUN useradd -r -u 1001 appuser
USER appuser

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
