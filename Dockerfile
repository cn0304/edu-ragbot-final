# Dockerfile
FROM python:3.11-slim

# Make logs flush immediately
ENV PYTHONUNBUFFERED=1

# Workdir inside the container
WORKDIR /app

# Copy project files
COPY . /app

# Install the Python packages your app imports at startup
# (reranker deps are optional; your code disables reranker if missing)
RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    chromadb \
    ollama

# Make sure Python can import "backend.app.web_dashboard"
ENV PYTHONPATH=/app

# Expose the API port
EXPOSE 8000

# Start your FastAPI app
CMD ["python", "-m", "uvicorn", "backend.app.web_dashboard:app", "--host", "0.0.0.0", "--port", "8000"]
