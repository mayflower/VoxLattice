# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm@sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301

# PYTHON_IMAGE is pinned by both version and digest above.
# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION=0.11.7
WORKDIR /src
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock .python-version README.md ./
COPY generated ./generated
COPY third_party ./third_party
COPY services/fastenhancer-server ./services/fastenhancer-server
COPY packages/livekit-plugins-fastenhancer ./packages/livekit-plugins-fastenhancer
RUN uv sync --package fastenhancer-server --no-dev --frozen --no-editable

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_CHECKPOINT=/opt/model/00500.pth \
    MODEL_CONFIG=/opt/model/config.yaml \
    MODEL_MANIFEST=/opt/model/manifest.lock.json \
    CUDA_DEVICE=cuda:0
RUN groupadd --system --gid 65532 fastenhancer \
    && useradd --system --uid 65532 --gid 65532 --home-dir /nonexistent --shell /usr/sbin/nologin fastenhancer
COPY --from=builder /src/.venv /opt/venv
COPY --chown=65532:65532 --chmod=0444 models/prepared/00500.pth models/prepared/config.yaml /opt/model/
COPY --chown=65532:65532 --chmod=0444 models/manifest.lock.json /opt/model/manifest.lock.json
COPY LICENSE THIRD_PARTY_NOTICES.md /opt/licenses/
COPY third_party/fastenhancer/LICENSE /opt/licenses/FastEnhancer-LICENSE
USER 65532:65532
EXPOSE 50051 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=2).read()"]
ENTRYPOINT ["python", "-m", "fastenhancer_server.main"]
