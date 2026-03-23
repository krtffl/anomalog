# Stage 1: Build Rust PyO3 extension
FROM rust:1.85-bookworm AS builder

RUN apt-get update && apt-get install -y python3-dev python3-pip && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages maturin

WORKDIR /build
COPY rust/ rust/
COPY pyproject.toml .

# Build the wheel (needs logmole-core source)
COPY --from=logmole /logmole-core /build/logmole-core
RUN cd rust && maturin build --release --out /build/dist

# Stage 2: Python runtime
FROM python:3.12-slim-bookworm

RUN useradd --create-home anomalog
WORKDIR /home/anomalog

COPY --from=builder /build/dist/*.whl /tmp/
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir /tmp/*.whl && \
    pip install --no-cache-dir . && \
    rm -rf /tmp/*.whl

USER anomalog
VOLUME /home/anomalog/data

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8701/healthz'); exit(0 if r.status_code == 200 else 1)"

ENTRYPOINT ["anomalog", "serve"]
CMD ["--config", "/home/anomalog/config.yaml"]

EXPOSE 8701
