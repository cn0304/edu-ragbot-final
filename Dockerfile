# Base image
FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies (optional, kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application source
COPY . /app

# Expose API port
EXPOSE 8000

# Default command: launch monitoring dashboard
CMD ["python", "-m", "uvicorn", "backend.app.web_dashboard:app", "--host", "0.0.0.0", "--port", "8000"]