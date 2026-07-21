# VoxLattice

[![CI](https://github.com/mayflower/VoxLattice/actions/workflows/ci.yml/badge.svg)](https://github.com/mayflower/VoxLattice/actions/workflows/ci.yml)
[![CodeQL](https://github.com/mayflower/VoxLattice/actions/workflows/codeql.yml/badge.svg)](https://github.com/mayflower/VoxLattice/actions/workflows/codeql.yml)
[![Security](https://github.com/mayflower/VoxLattice/actions/workflows/security.yml/badge.svg)](https://github.com/mayflower/VoxLattice/actions/workflows/security.yml)
[![CUDA container](https://github.com/mayflower/VoxLattice/actions/workflows/container.yml/badge.svg)](https://github.com/mayflower/VoxLattice/actions/workflows/container.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/mayflower/VoxLattice/badge)](https://securityscorecards.dev/viewer/?uri=github.com/mayflower/VoxLattice)
[![License: MIT](https://img.shields.io/github/license/mayflower/VoxLattice)](LICENSE)
[![Python 3.12–3.13](https://img.shields.io/badge/Python-3.12%E2%80%933.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)

VoxLattice is a self-hosted, CUDA-accelerated voice-isolation service for live
audio. It combines a streaming FastEnhancer-B inference server with a LiveKit
`FrameProcessor` plugin.

The server keeps model state isolated per audio stream and batches work across
concurrent streams on one GPU. The plugin maintains a persistent bidirectional
gRPC stream per LiveKit track and falls back to the time-aligned original audio
when the service is unavailable.

> VoxLattice is alpha software. The wire protocol is versioned, but operational
> defaults and Python APIs may still evolve before 1.0.

## Features

- One CUDA model instance serving multiple independent audio streams
- Exact input/output sample accounting and a fixed 16 ms algorithmic delay
- Bounded queues, backpressure, health checks, Prometheus metrics, and graceful shutdown
- Bearer authentication, TLS, and optional mutual TLS
- Reproducible model download with pinned provenance and SHA-256 verification
- Published `linux/amd64` CUDA image with SBOM and signed build provenance
- Docker Compose setup, gRPC client, LiveKit Agent example, and load benchmark

## Current scope

VoxLattice accepts 16 kHz, mono, signed 16-bit little-endian PCM. Inference
requires a compatible NVIDIA CUDA GPU; there is no production CPU fallback.
The included server uses the pinned FastEnhancer-B VCTK-Demand checkpoint.

No particular GPU model is required. Capacity and latency depend on the chosen
GPU and workload, so benchmark the target system before production use.

## Quick start with the published image

Install the NVIDIA Container Toolkit, clone the repository for its Compose
configuration, and create `deploy/.env` as described below. Then set:

```dotenv
VOXLATTICE_IMAGE=ghcr.io/mayflower/voxlattice:latest
```

Start the published image without a local build:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml pull fastenhancer
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --no-build --wait
```

Release images are also available under their full version and immutable Git
SHA tags. Use a version tag instead of `latest` for reproducible deployments.

## Quick start from source

Prerequisites:

- Git, Python 3.12 or 3.13, Docker, and Docker Compose
- NVIDIA driver and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- An NVIDIA GPU visible in `nvidia-smi`

Clone the repository:

```bash
git clone https://github.com/mayflower/VoxLattice.git
cd VoxLattice
```

Create the local configuration and inspect the available GPUs:

```bash
cp deploy/.env.example deploy/.env
nvidia-smi -L
```

Edit `deploy/.env` and set at least:

```dotenv
FASTENHANCER_API_TOKEN=<random value with at least 16 characters>
FASTENHANCER_GPU_DEVICE_ID=<GPU UUID or index from nvidia-smi -L>
```

For example, `openssl rand -hex 32` produces a suitable local token. Then run
the self-contained build and smoke test:

```bash
make compose-test
```

This downloads and verifies the official model asset, builds the container,
starts it on the selected GPU, sends a synthetic audio stream, checks exact
sample counts, and removes the containers again.

## Run the service

After configuring `deploy/.env`, one command prepares the verified model,
builds the image, starts the service, and waits for readiness:

```bash
make up
```

The default endpoints are available only on the local host:

- gRPC: `127.0.0.1:50051`
- liveness: `http://127.0.0.1:8080/healthz`
- readiness: `http://127.0.0.1:8080/readyz`
- Prometheus metrics: `http://127.0.0.1:8080/metrics`

Process a WAV file with the included client. The command reads the API token
and port from `deploy/.env`:

```bash
make enhance INPUT=input.wav OUTPUT=enhanced.wav
```

The client accepts PCM16 WAV input and converts its channel count and sample
rate to the service contract. It requires the optional local `uv` environment;
run `make bootstrap` once before using `make enhance`. Stop the service with
`make down`.

For remote access, configure TLS and intentionally change the gRPC bind
address. Do not expose the plaintext local Compose configuration to a network.
See [Operations](docs/operations.md) and [Configuration](docs/configuration.md).

## Install the LiveKit plugin

Install the plugin and its protocol package directly from GitHub:

```bash
python -m pip install \
  "fastenhancer-protocol @ git+https://github.com/mayflower/VoxLattice.git@main#subdirectory=generated" \
  "livekit-plugins-fastenhancer @ git+https://github.com/mayflower/VoxLattice.git@main#subdirectory=packages/livekit-plugins-fastenhancer"
```

With `uv`, use the same two requirement strings with `uv add`. Pin a release tag
instead of `main` when deploying a released version.

## Add it to a LiveKit Agent

Create one processor per input track and pass it as LiveKit's noise-cancellation
processor:

```python
import os

from livekit.agents import room_io
from livekit.plugins.fastenhancer import RemoteFastEnhancer

processor = RemoteFastEnhancer(
    endpoint="dns:///fastenhancer.example.com:50051",
    api_key=os.environ["FASTENHANCER_API_TOKEN"],
    tls=True,
)

room_options = room_io.RoomOptions(
    audio_input=room_io.AudioInputOptions(
        sample_rate=16_000,
        num_channels=1,
        frame_size_ms=32,
        noise_cancellation=processor,
        auto_gain_control=False,
    )
)
```

For multi-participant rooms, use a selector that creates a fresh processor for
each track. The complete example, certificate options, buffering parameters,
and lifecycle behavior are documented in [LiveKit integration](docs/livekit.md).

## Documentation

- [Configuration reference](docs/configuration.md)
- [Operations and TLS deployment](docs/operations.md)
- [LiveKit integration](docs/livekit.md)
- [Architecture](docs/architecture.md)
- [gRPC protocol and audio timeline](docs/protocol.md)
- [Benchmarking and capacity planning](docs/benchmarking.md)
- [Security and privacy](docs/security.md)
- [Model provenance](models/README.md)
- [Contributing](CONTRIBUTING.md)

## Community and support

Use [GitHub issues](https://github.com/mayflower/VoxLattice/issues) for
reproducible bugs and focused feature requests. Read [SUPPORT.md](SUPPORT.md)
for the diagnostic information to include. Contributions are welcome under the
[Code of Conduct](CODE_OF_CONDUCT.md).

Do not report suspected vulnerabilities publicly; follow the private process in
[SECURITY.md](SECURITY.md).

## License

VoxLattice is licensed under the [MIT License](LICENSE). Vendored code, model
assets, and runtime dependencies retain their respective licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

VoxLattice is an independent project and is not affiliated with or endorsed by
LiveKit, NVIDIA, or the FastEnhancer authors.
