ARG BASE_IMAGE=ghcr.io/loong64/python:3.13.13-slim-trixie
FROM ${BASE_IMAGE}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install uv --extra-index-url https://mirrors.loong64.com/pypi/simple --no-cache-dir .

COPY src/ src/

EXPOSE 11452

CMD ["python", "-m", "src.main"]
