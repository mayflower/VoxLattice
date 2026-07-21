# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.13.14-slim-trixie@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

# PYTHON_IMAGE is pinned by both version and digest above.
# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION=0.11.7
# Build against the interpreter in PYTHON_IMAGE. Without this uv honours
# .python-version, finds no matching interpreter, and downloads a managed one
# under /root; the venv would then symlink to a path the runtime stage does
# not copy, leaving /opt/venv/bin/python dangling.
ENV UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never
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
# /opt/model is created explicitly so that it keeps its execute bit. A COPY
# --chmod=0444 that creates the directory implicitly applies 0444 to the
# directory as well, and without +x its files cannot be opened even by their
# owner.
RUN groupadd --system --gid 65532 fastenhancer \
    && useradd --system --uid 65532 --gid 65532 --home-dir /nonexistent --shell /usr/sbin/nologin fastenhancer \
    && install -d -o 65532 -g 65532 -m 0555 /opt/model
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
