ARG BASE_IMAGE=ghcr.io/loong64/python:3.13.13-trixie
FROM ${BASE_IMAGE}

# 编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install uv --extra-index-url https://mirrors.loong64.com/pypi/simple --no-cache-dir .


COPY src/ src/

EXPOSE 11450

CMD ["python", "-m", "src.main"]
