ARG BASE_IMAGE=python:3.13
ARG PIP_EXTRA_INDEX_URL=
FROM ${BASE_IMAGE}

WORKDIR /app

# util-linux provides nsenter; bpftrace provides dynamic kernel tracing
RUN apt-get update && apt-get install -y --no-install-recommends \
    util-linux \
    bpftrace && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}
RUN pip install --no-cache-dir .

COPY src/ src/
COPY config.json ./

ENV TOOLSERVER_PORT 11451
EXPOSE 11451

CMD ["python", "-m", "src.main"]
