# LiveKit FastEnhancer plugin

`RemoteFastEnhancer` is a track-local LiveKit `FrameProcessor` for the
[VoxLattice](https://github.com/mayflower/VoxLattice) GPU voice-isolation
service. It keeps one persistent gRPC stream per track and returns time-aligned
raw audio whenever remote enhancement is unavailable.

## Install

```bash
python -m pip install \
  "fastenhancer-protocol @ git+https://github.com/mayflower/VoxLattice.git@main#subdirectory=generated" \
  "livekit-plugins-fastenhancer @ git+https://github.com/mayflower/VoxLattice.git@main#subdirectory=packages/livekit-plugins-fastenhancer"
```

Pin a release tag instead of `main` for deployed applications.

## Use

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

Create a separate processor for every input track. A running VoxLattice server
is required. See the complete
[LiveKit guide](https://github.com/mayflower/VoxLattice/blob/main/docs/livekit.md)
for selectors, TLS/mTLS, fallback behavior, tuning, and metrics.
