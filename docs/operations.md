# Operations

This guide covers source-based Docker deployment. VoxLattice does not download
model weights during server startup: the verified checkpoint is prepared before
the image is built and copied into the image.

For cluster deployment with Helm or ArgoCD, see
[Kubernetes deployment](kubernetes.md).

## Prerequisites

Install Git, Python 3.12 or 3.13, Docker, Docker Compose, an NVIDIA driver, and NVIDIA
Container Toolkit. The host Python installation is used only by the
standard-library model verification tools; application dependencies are built
inside the image. Confirm that Docker can access the intended device:

```bash
nvidia-smi -L
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

The diagnostic image tag above is only used to test container GPU access; the
VoxLattice runtime image is pinned independently in its Dockerfile.

## Local deployment

Create the configuration:

```bash
cp deploy/.env.example deploy/.env
```

Set `FASTENHANCER_API_TOKEN` and `FASTENHANCER_GPU_DEVICE_ID` in that file. The
GPU value may be an index or UUID reported by `nvidia-smi -L`.

Build and start the service:

```bash
make up
```

Compose waits for readiness before returning. Verify the endpoints:

```bash
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:8080/metrics
make smoke
```

Stop the deployment with `make down`. To test the whole lifecycle without
leaving a container running, use `make compose-test`.

The base Compose file enables plaintext gRPC and binds both ports to localhost.
It is intended for development and single-host use only.

## Published container image

Stable releases publish a `linux/amd64` CUDA image to
`ghcr.io/mayflower/voxlattice`. Set the image in `deploy/.env`:

```dotenv
VOXLATTICE_IMAGE=ghcr.io/mayflower/voxlattice:latest
```

Then pull and start it without building locally:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml pull fastenhancer
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --no-build --wait
```

Production deployments should replace `latest` with a full release version or
immutable `sha-<40-character commit>` tag. Published images include an SBOM and
signed provenance attestation. Verify provenance with GitHub CLI:

```bash
gh attestation verify oci://ghcr.io/mayflower/voxlattice:<tag> \
  --repo mayflower/VoxLattice
```

## TLS deployment

For server-authenticated TLS, set the host paths in `deploy/.env`:

```dotenv
FASTENHANCER_TLS_CERTIFICATE=/etc/voxlattice/server.crt
FASTENHANCER_TLS_PRIVATE_KEY=/etc/voxlattice/server.key
```

The certificate must cover the hostname used by clients. Start Compose with the
TLS overlay:

```bash
docker compose \
  --env-file deploy/.env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.tls.yml \
  up -d --wait
```

To accept connections from other hosts, also set
`FASTENHANCER_GRPC_BIND_ADDRESS=0.0.0.0` or a specific interface address.
Leave `FASTENHANCER_HTTP_BIND_ADDRESS` on localhost or place the health and
metrics endpoint behind an internal network policy.

For mutual TLS, set `FASTENHANCER_TLS_CLIENT_CA` to the CA that signed trusted
client certificates and add the mTLS overlay:

```bash
docker compose \
  --env-file deploy/.env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.tls.yml \
  -f deploy/docker-compose.mtls.yml \
  up -d --wait
```

Clients must still send the bearer token when TLS or mTLS is enabled. TLS
protects the connection; the token authorizes access to the service.

## Secrets

The local Compose setup obtains `FASTENHANCER_API_TOKEN` from the host
environment or `deploy/.env` and exposes it to the container through a mounted
secret file. For production, use the secret facility provided by the
orchestrator and set `FASTENHANCER_API_TOKEN_FILE` to the mounted path.

Use a unique, randomly generated token of at least 16 characters. Rotate it by
replacing the mounted secret and gracefully restarting the service. Never add
tokens, private keys, certificates containing private keys, or production audio
to the repository or diagnostic bundles.

## Direct container execution

The Compose files are reference deployments, not a requirement. After
`make image`, a plaintext localhost-only container can be started directly:

```bash
docker run --rm \
  --gpus "device=${FASTENHANCER_GPU_DEVICE_ID}" \
  --read-only --tmpfs /tmp:size=64m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:50051:50051 \
  -p 127.0.0.1:8080:8080 \
  -e ALLOW_INSECURE_GRPC=true \
  -e FASTENHANCER_API_TOKEN_FILE=/run/secrets/api-token \
  -v "${FASTENHANCER_TOKEN_FILE}:/run/secrets/api-token:ro" \
  voxlattice:0.1.0
```

`FASTENHANCER_TOKEN_FILE` must refer to a host file containing only the token.
For a network-facing deployment, omit `ALLOW_INSECURE_GRPC`, mount the TLS
files, and set the TLS variables described in the
[configuration reference](configuration.md).

## Health and observability

- `/healthz` reports whether the process event loop is alive.
- `/readyz` becomes successful only after model verification, CUDA warm-up,
  state-isolation checks, and scheduler startup.
- `/metrics` exposes Prometheus metrics for stream counts, batching, inference,
  queueing, errors, and latency.
- The standard gRPC health service reports the same serving transition as
  readiness.

Metrics do not use audio, tokens, stream IDs, room names, or participant
identities as labels. Application logs are structured and intentionally omit
PCM and authentication metadata.

## Shutdown and rolling updates

On `SIGTERM`, the server clears readiness, marks gRPC health as not serving,
drains active RPCs for up to `GRACEFUL_SHUTDOWN_S`, and closes the scheduler.
Allow at least that much termination grace time in the orchestrator.

During a rolling update, keep enough old and new capacity available for the
current stream count. Plugins reconnect automatically and return aligned raw
audio while the remote stream is unavailable.

The image contains one model instance. Horizontal replicas each load their own
copy; route a single track consistently for the lifetime of its gRPC stream.

## Troubleshooting

### Server never becomes ready

- Confirm the selected device is visible to Docker and compatible with the
  pinned PyTorch/CUDA runtime.
- Inspect `docker compose logs fastenhancer` for model hash, CUDA, or warm-up
  errors.
- If a cached model download is corrupt, remove `models/cache/fastenhancer_b.zip`
  and run `make model` again.

### Client receives `UNAUTHENTICATED`

Verify that client and server use the same token and that the mounted token
file contains no unintended whitespace. Authentication headers are not logged.

### Client receives `INVALID_ARGUMENT`

Check the 16 kHz mono PCM16 contract, message ordering, sequence numbers, and
absolute sample offsets. See [Protocol](protocol.md).

### Frequent raw fallback in the LiveKit plugin

Compare plugin fallback counters with server batch, inference, and queue
latencies. Increase `response_wait_ms` only when the extra end-to-end latency is
acceptable. Do not treat larger unbounded queues as a capacity fix; benchmark
and scale the service instead.

## Upgrades

Read [CHANGELOG.md](../CHANGELOG.md) before upgrading. Build the new image,
rerun `make compose-test` and the target-GPU benchmark, then roll it out with
readiness checks. Plugin and server validate the pinned model identity, so
upgrade their release artifacts together when that identity changes.
