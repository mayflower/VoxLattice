# Configuration reference

VoxLattice has three configuration layers: Docker Compose host settings, server
environment variables inside the container, and LiveKit plugin constructor
arguments. Secrets should be supplied through files or a secret manager rather
than committed environment files.

## Docker Compose

Copy `deploy/.env.example` to `deploy/.env`. The provided Make targets source
this file; it is ignored by Git. When invoking Docker Compose directly from the
repository root, pass `--env-file deploy/.env`.

| Variable | Default | Purpose |
|---|---:|---|
| `FASTENHANCER_API_TOKEN` | required | Bearer token exposed to the container as a Docker secret; minimum 16 characters |
| `FASTENHANCER_GPU_DEVICE_ID` | required | GPU UUID or index accepted by the NVIDIA Container Toolkit |
| `FASTENHANCER_GRPC_BIND_ADDRESS` | `127.0.0.1` | Host address for the gRPC port |
| `FASTENHANCER_GRPC_PORT` | `50051` | Host gRPC port |
| `FASTENHANCER_HTTP_BIND_ADDRESS` | `127.0.0.1` | Host address for health and metrics |
| `FASTENHANCER_HTTP_PORT` | `8080` | Host health and metrics port |
| `MAX_ACTIVE_STREAMS` | `128` | Maximum simultaneous server streams |
| `MAX_BATCH_SIZE` | `32` | Maximum different streams represented in one inference batch |
| `MAX_BATCH_WAIT_MS` | `1` | Maximum time spent collecting a batch |
| `MAX_PENDING_AUDIO_MS_PER_STREAM` | `500` | Per-stream input queue bound |
| `MAX_OUTPUT_AUDIO_MS_PER_STREAM` | `500` | Per-stream output queue bound |
| `STREAM_IDLE_TIMEOUT_S` | `30` | Inactive stream timeout |
| `MAX_AUDIO_CHUNK_SAMPLES` | `16000` | Maximum samples accepted in one client chunk |
| `GRACEFUL_SHUTDOWN_S` | `10` | Maximum gRPC drain time during shutdown |

Increasing queue bounds consumes more memory and increases worst-case latency;
it does not add GPU capacity. Use the benchmark before changing concurrency or
batch settings.

## Server environment

The container image can also be run by another orchestrator. It reads these
variables directly:

| Variable | Default | Purpose |
|---|---:|---|
| `FASTENHANCER_API_TOKEN` | none | Bearer token value |
| `FASTENHANCER_API_TOKEN_FILE` | none | File containing the bearer token; mutually exclusive with the value variable |
| `GRPC_HOST` | `0.0.0.0` | Container listen address for gRPC |
| `GRPC_PORT` | `50051` | Container gRPC port |
| `HTTP_HOST` | `0.0.0.0` | Container listen address for health and metrics |
| `HTTP_PORT` | `8080` | Container health and metrics port |
| `CUDA_DEVICE` | `cuda:0` | Explicit CUDA device visible inside the container |
| `ALLOW_INSECURE_GRPC` | `false` | Permit plaintext gRPC; use only on localhost or an isolated private network |
| `TLS_CERTIFICATE` | none | PEM server certificate path |
| `TLS_PRIVATE_KEY` | none | PEM server private-key path |
| `TLS_CLIENT_CA` | none | Optional PEM CA path used to require and verify client certificates |
| `MODEL_CHECKPOINT` | `/opt/model/00500.pth` | Prepared checkpoint path |
| `MODEL_MANIFEST` | `/opt/model/manifest.lock.json` | Locked model manifest path |
| `MODEL_CONFIG` | `/opt/model/config.yaml` | FastEnhancer model configuration path |

The resource-limit variables listed in the Compose table use the same names and
defaults inside the server. All integer and duration limits must be positive.
Startup fails for an invalid token, incomplete TLS configuration, unavailable
CUDA device, or model verification error.

## TLS Compose overlays

`deploy/docker-compose.tls.yml` requires these host paths:

| Variable | Purpose |
|---|---|
| `FASTENHANCER_TLS_CERTIFICATE` | PEM server certificate or full chain |
| `FASTENHANCER_TLS_PRIVATE_KEY` | Matching PEM private key |

For mutual TLS, add `deploy/docker-compose.mtls.yml` and set
`FASTENHANCER_TLS_CLIENT_CA` to the PEM CA that signed trusted client
certificates.

## LiveKit plugin

`RemoteFastEnhancer` accepts the following options. One instance belongs to
exactly one input track.

| Argument | Default | Purpose |
|---|---:|---|
| `endpoint` | required | gRPC target such as `dns:///host:50051` |
| `api_key` | `None` | Bearer token; required by the bundled server |
| `tls` | `True` | Use a TLS gRPC channel |
| `root_certificates` | system trust | PEM server CA bytes for a private PKI |
| `client_certificate_chain` | `None` | PEM client certificate chain for mTLS |
| `client_private_key` | `None` | Matching PEM client private-key bytes |
| `connect_timeout_s` | `2.0` | Per-attempt connection timeout |
| `response_wait_ms` | `12.0` | Time to wait for the exact enhanced interval before raw fallback |
| `max_buffer_ms` | `500` | Bound for the local raw/enhanced timeline |
| `max_request_queue_ms` | `500` | Bound for queued outbound audio |
| `reconnect_min_delay_s` | `0.1` | Initial reconnect delay |
| `reconnect_max_delay_s` | `2.0` | Maximum reconnect delay |
| `stream_metadata` | `None` | Up to 16 protocol metadata entries |
| `expected_model_revision` | bundled revision | Expected full upstream commit hash |
| `expected_model_sha256` | bundled hash | Expected checkpoint SHA-256 |

The certificate chain and key must be configured together. The plugin rejects
mTLS credentials when `tls=False`, an unexpected model identity, and metadata
outside the protocol limits.
