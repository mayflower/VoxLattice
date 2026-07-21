# LiveKit Agent example

This example attaches a fresh `RemoteFastEnhancer` processor to every selected
input track. It expects a running VoxLattice server and a LiveKit project.

From the repository root, install the workspace and prepare the example:

```bash
cp examples/livekit-agent/.env.example examples/livekit-agent/.env
make bootstrap
```

Fill the LiveKit credentials and use the same `FASTENHANCER_API_TOKEN` as the
server. The example file defaults to the plaintext local Compose endpoint. For
a remote service, set a DNS endpoint, enable TLS, and optionally configure the
private-CA or mTLS paths.

Run the agent in development mode:

```bash
uv run python examples/livekit-agent/agent.py dev
```

The input is explicitly configured for 16 kHz mono, 32 ms frames. The selector
returns a new processor for each track because a processor owns one remote model
state and one sample timeline.

Acoustic echo cancellation may remain enabled at the capture edge. Disable any
additional neural noise suppression in the browser or agent path so audio is
not passed through two heavy denoisers. `record=False` prevents this example
from enabling LiveKit Agents audio recording.
