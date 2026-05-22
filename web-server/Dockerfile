ARG BASE_IMAGE=ghcr.io/loong64/python:3.13.13-slim-trixie
FROM ${BASE_IMAGE}

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ src/

EXPOSE 11450

CMD ["python", "-m", "src.main"]
