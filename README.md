# VoxLattice

VoxLattice is an open-source, CUDA-only voice-isolation service for live audio.
It provides a FastEnhancer-B gRPC server and
`livekit-plugins-fastenhancer`, a track-local LiveKit
`FrameProcessor[AudioFrame]`. The wire and audio contract is deliberately
narrow: 16,000 Hz, mono, signed PCM16 little-endian, with 256-sample model
hops and 256 samples (16 ms) of algorithmic delay.

The VoxLattice name covers the repository and container. Existing Python
distribution names, imports, and the `fastenhancer.v1` protocol remain stable.

The server loads one official VCTK-Demand Base checkpoint and micro-batches
the next dependent hop from different streams. STFT, iSTFT, and all three GRU
caches remain isolated per RPC. Production startup rejects CPU execution and,
for this deployment, rejects every CUDA device except the NVIDIA RTX A6000.

## Quickstart

Prerequisites are Python 3.12, `uv`, Docker Compose, NVIDIA driver 550 or
compatible, and NVIDIA Container Toolkit. This checkout is locked to A6000 UUID
`GPU-bac67bca-195d-3490-88f0-b8a3453c5929`; update the Compose device ID only
as an intentional deployment change while retaining the name check.

```bash
make bootstrap
make check
make test
make test-integration
make model
export FASTENHANCER_API_TOKEN="$(openssl rand -hex 32)"
make image
make up
make smoke
make test-gpu
make benchmark BENCH_TARGET_STREAMS=16
make down
```

For a self-contained local Compose test, generate a checkout-local secret and
run the one-shot test profile:

```bash
printf 'FASTENHANCER_API_TOKEN=%s\n' "$(openssl rand -hex 32)" > deploy/.env
make compose-test
```

This prepares the verified model, builds the image, waits for the A6000 server
to become healthy, runs a synthetic one-second bidi-gRPC stream from the
Compose network, verifies exact input/output sample counts and offsets, and
then removes the test containers. The server ports remain bound only to
localhost and use ephemeral host ports during this one-shot test, avoiding
collisions with existing local services. `deploy/.env` is ignored by Git;
remove it when finished. For a long-running `make up`, the defaults remain
`127.0.0.1:50051` and `127.0.0.1:8080`; override
`FASTENHANCER_GRPC_PORT` or `FASTENHANCER_HTTP_PORT` if needed.

The image never downloads weights. `make model` verifies the GitHub asset ID,
archive size/hash, member names, and checkpoint/config hashes before the Docker
build copies them. Plaintext gRPC in Compose binds only to localhost. Configure
the TLS variables documented in [operations](docs/operations.md) for any
distributed deployment.

## LiveKit

```python
import os

from livekit.agents import room_io
from livekit.plugins import fastenhancer

processor = fastenhancer.RemoteFastEnhancer(
    endpoint="dns:///fastenhancer:50051",
    api_key=os.environ["FASTENHANCER_API_TOKEN"],
    tls=False,  # local Compose network only
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

Create a fresh processor for every participant track. See the selector in
`examples/livekit-agent/agent.py`. Acoustic echo cancellation may remain at the
capture edge, but disable browser/agent neural noise suppression so two heavy
denoisers are not stacked.

On remote failure, output remains delayed by the model contract and falls open
to raw audio for the exact same absolute sample interval. Late remote output is
discarded. Abrupt track close cannot return the final delayed 256 samples
because `FrameProcessor._close()` has no frame return value; a following silence
frame exposes that final speech hop during normal continuous operation.

## Capacity and latency

Do not copy a stream count from another GPU. Run `make benchmark` on the target
A6000 and use its JSON/Markdown artifact. Gates are controlled by
`BENCH_MAX_SERVER_P95_MS`, `BENCH_MAX_RTT_P95_MS`,
`BENCH_MAX_FALLBACK_RATIO`, and `BENCH_MAX_ERROR_RATIO`. A failed gate exits
nonzero; the tool never substitutes or invents measurements.

The fixed algorithmic delay is 16 ms. Queueing, batching, transport, and plugin
wait time are additional and are reported independently. See
[architecture](docs/architecture.md), [protocol](docs/protocol.md), and
[benchmarking](docs/benchmarking.md).

## Community and security

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), follow
the [Code of Conduct](CODE_OF_CONDUCT.md), and use the repository issue
templates for reproducible bugs and scoped feature proposals. General usage
questions are covered by [SUPPORT.md](SUPPORT.md).

Do not disclose suspected vulnerabilities in a public issue. Follow the
private-first process in [SECURITY.md](SECURITY.md). Project decisions and
maintainer responsibilities are described in [GOVERNANCE.md](GOVERNANCE.md).

## License and independence

VoxLattice source code is licensed under the [MIT License](LICENSE).
Vendored code, model assets, and runtime dependencies retain their respective
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the vendored
license files.

VoxLattice is an independent community project. It is not affiliated with or
endorsed by LiveKit, NVIDIA, or the FastEnhancer authors. Those names and any
related marks belong to their respective owners.
