# Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Workdir inside the container
WORKDIR /app

# Copy project files
COPY . /app

RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    chromadb \
    ollama \
    llama-index \
    llama-index-core \
    llama-index-llms-ollama \
    llama-index-embeddings-ollama \
    llama-index-vector-stores-chroma\
    sentence-transformers \
    torch

ENV PYTHONPATH=/app

# Expose the API port
EXPOSE 8000

# Start your FastAPI app
CMD ["python", "-m", "uvicorn", "backend.app.web_dashboard:app", "--host", "0.0.0.0", "--port", "8000"]
