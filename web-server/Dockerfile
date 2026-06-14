ARG BASE_IMAGE=python:3.13
ARG PIP_EXTRA_INDEX_URL=
FROM ${BASE_IMAGE}

# 编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}
RUN pip install uv --no-cache-dir .


COPY src/ src/

EXPOSE 11450

CMD ["sh", "-c", "uvicorn src.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-11450}"]
