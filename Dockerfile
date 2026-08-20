# CURRENT PROTOTYPE CONTAINER, NOT A PRODUCTION-READY CONTROL PLANE.
# The packaged backend still couples server orchestration with local
# sounddevice/BlackHole capture, while the approved target moves capture into a
# native macOS companion. Deployment remains blocked pending containment, auth,
# ownership, isolated environments, tests, and a re-run plan gate.
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
