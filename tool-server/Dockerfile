ARG BASE_IMAGE=ghcr.io/loong64/python:3.13.13-slim-trixie
FROM ${BASE_IMAGE}

WORKDIR /app

# util-linux provides nsenter for host command execution
RUN apt-get update && apt-get install -y --no-install-recommends util-linux && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ src/
COPY config.json ./

ENV TOOLSERVER_PORT 11451
EXPOSE 11451

CMD ["python", "-m", "src.main"]
