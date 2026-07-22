# LiveKit integration

`livekit-plugins-fastenhancer` provides `RemoteFastEnhancer`, a synchronous
LiveKit `FrameProcessor[AudioFrame]` backed by a VoxLattice gRPC server. One
processor instance owns one remote model state and must not be shared between
tracks.

## Compatibility

- Python 3.12 or 3.13
- `livekit>=1.1.13,<1.2`
- 16 kHz mono `AudioFrame` input
- VoxLattice server and plugin from the same release

The processor does not resample. Configure LiveKit audio input to produce 16 kHz
mono frames as shown below.

## Installation

Install the plugin and generated protocol package directly from GitHub:

```bash
python -m pip install \
  "fastenhancer-protocol @ git+https://github.com/mayflower/VoxLattice.git@main#subdirectory=generated" \
  "livekit-plugins-fastenhancer @ git+https://github.com/mayflower/VoxLattice.git@main#subdirectory=packages/livekit-plugins-fastenhancer"
```

For a `uv` project, replace `python -m pip install` with `uv add`. Pin a release
tag instead of `main` for a reproducible deployment. The server can run on
another host; only these two packages are needed in the LiveKit Agent
environment.

## Single-track setup

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

Pass `room_options` to `AgentSession.start`. Keep acoustic echo cancellation at
the capture edge when needed, but disable another neural noise-suppression
stage in the browser or agent path to avoid processing the same signal twice.

## Multiple participant tracks

Rooms that can select different linked participants should provide a selector
that creates a fresh processor for every track:

```python
import os

from livekit.agents import room_io
from livekit.plugins.fastenhancer import RemoteFastEnhancer


def select_processor(
    params: room_io.NoiseCancellationParams,
) -> RemoteFastEnhancer:
    del params
    return RemoteFastEnhancer(
        endpoint="dns:///fastenhancer.example.com:50051",
        api_key=os.environ["FASTENHANCER_API_TOKEN"],
        tls=True,
    )
```

Use `select_processor` as the `noise_cancellation` value. The runnable example
in [`examples/livekit-agent`](../examples/livekit-agent/) includes environment
loading and optional mTLS files.

## TLS and mutual TLS

For a server certificate issued by a public CA, `tls=True` uses the system trust
store. For a private CA, pass its PEM bytes:

```python
from pathlib import Path

processor = RemoteFastEnhancer(
    endpoint="dns:///fastenhancer.internal:50051",
    api_key=token,
    tls=True,
    root_certificates=Path("/run/secrets/server-ca.pem").read_bytes(),
)
```

For mutual TLS, also pass `client_certificate_chain` and
`client_private_key`. Both are required together. Mount certificate material at
runtime; do not bake private keys into an image.

Set `tls=False` only when the endpoint is localhost or an isolated private
Compose network. The bearer token is transmitted as gRPC metadata and therefore
requires transport protection on untrusted networks.

## Delay and fallback behavior

FastEnhancer introduces a fixed 256-sample delay at 16 kHz, or 16 ms. The first
processed frame can therefore contain fewer samples than the input frame. A
continuous stream catches up while preserving the original sample timeline.

For each output interval, the processor waits up to `response_wait_ms` for the
matching enhanced samples. If they are unavailable, it returns raw samples from
the same absolute interval. Late responses and responses from a replaced gRPC
generation are discarded; enhanced audio is never paired with the wrong input
time range.

Setting `processor.enabled = False` cancels the current remote stream and
returns subsequent frames raw. Re-enabling starts a new remote state. Closing
the track closes the processor and its worker. Because the LiveKit close hook
cannot return another frame, an abrupt close cannot emit the final delayed 256
samples; normal trailing silence advances and emits that interval first.

## Tuning

The most relevant client setting is `response_wait_ms`:

- Lower values reduce blocking but increase raw fallback during latency spikes.
- Higher values allow more remote jitter but directly add to processing latency.

Set it from the server's measured per-hop latency, not from a guess. The server
exposes that latency as the `fastenhancer_hop_end_to_end_seconds` histogram; if
the average or high percentiles exceed `response_wait_ms`, every interval falls
back to raw and `raw_fallback_samples` dominates `enhanced_samples` even though
the server is enhancing correctly. Inference is only part of that hop: on a GPU
shared through NVIDIA MPS, scheduling and queueing can raise the end-to-end hop
to several times the inference time, so the 12 ms default is frequently too low.
Run the agent close to the server, raise `response_wait_ms` to cover the hop, or
give the server less-contended GPU capacity.

`max_buffer_ms` and `max_request_queue_ms` are safety bounds, not capacity
controls. If queues overflow or fallback remains high, benchmark and scale the
server rather than growing buffers indefinitely. All plugin options and defaults
are listed in [Configuration](configuration.md).

## Metrics and diagnostics

`processor.metrics` returns counters for input/output samples, enhanced samples,
raw fallback, late responses, reconnects, protocol mismatches, and queue
overflows. `processor.transport_state` reports the current connection state.
These values contain counts only; they do not contain PCM, tokens, or certificate
material.
