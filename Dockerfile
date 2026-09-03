# Backend image source for the single-instance hosted pilot candidate.
# Server orchestration serves authenticated API/WS sessions while capture is
# handled by browser Web Audio or the native macOS companion.
# Real deployment remains blocked pending Task 07 and Task 08 gates.
FROM python:3.12-slim

WORKDIR /app

# System dependencies: libsndfile1 for audio file processing and libportaudio2
# for import-time sounddevice compatibility.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

# Runtime environment settings: safe non-capture defaults and unbuffered Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST_AUDIO_CAPTURE_ENABLED=false \
    AUDIO_BACKUP_ENABLED=false \
    TARS_RUNTIME_MODE=hosted-pilot \
    AUTH_BYPASS=false

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/

# Security: run as non-root
RUN useradd -r -u 1001 appuser
USER appuser

EXPOSE 8080

# Exec Uvicorn in single process without reload/workers (process-local state)
CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
