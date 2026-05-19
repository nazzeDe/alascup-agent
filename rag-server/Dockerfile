FROM ghcr.io/loong64/python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/

EXPOSE 8002

CMD ["python", "-m", "src.main"]
