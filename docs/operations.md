# Operations

Install NVIDIA Container Toolkit from NVIDIA's official documentation and
verify `docker run --rm --gpus all nvidia/cuda:<pinned-tag> nvidia-smi` before
building. This deployment reserves only A6000 UUID
`GPU-bac67bca-195d-3490-88f0-b8a3453c5929`; inside the restricted container it
is `cuda:0`. The server additionally checks the device name, preventing a
numeric-ordering mistake from silently selecting the P40.

Prepare with `make model`, export a random token of at least 16 characters, and
run `make image && make up`. Compose sources the token as a Docker secret and
mounts it at `/run/secrets`. To build, start, health-check, smoke-test, and stop
the complete local stack in one command:

```bash
printf 'FASTENHANCER_API_TOKEN=%s\n' "$(openssl rand -hex 32)" > deploy/.env
make compose-test
```

The `test` Compose profile runs the checked-in gRPC client inside the Compose
network, publishes ephemeral localhost ports to avoid collisions, and requires
an exact flushed output before returning success. Long-running `make up` uses
host ports 50051 and 8080 unless `FASTENHANCER_GRPC_PORT` and
`FASTENHANCER_HTTP_PORT` override them. For direct local execution:

```bash
FASTENHANCER_API_TOKEN_FILE=/run/secrets/fastenhancer_api_token \
ALLOW_INSECURE_GRPC=true fastenhancer-server
```

On a host containing only the intended A6000, the generic Docker invocation is
`docker run --gpus all`. On this mixed-GPU host, keep the P40 invisible and use
the UUID selector instead:

```bash
docker run --rm \
  --gpus '"device=GPU-bac67bca-195d-3490-88f0-b8a3453c5929"' \
  --read-only --tmpfs /tmp:size=64m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:50051:50051 -p 127.0.0.1:8080:8080 \
  -e ALLOW_INSECURE_GRPC=true \
  -e FASTENHANCER_API_TOKEN_FILE=/run/secrets/fastenhancer_api_token \
  -v "$FASTENHANCER_TOKEN_FILE:/run/secrets/fastenhancer_api_token:ro" \
  voxlattice:0.1.0
```

`FASTENHANCER_TOKEN_FILE` must name a host file containing a random token and
must not be inside the image or repository. Never use `--gpus all` on this
mixed host: the UUID-scoped command and the runtime device-name assertion are
the two independent controls that keep inference on the RTX A6000.

For a network deployment, omit `ALLOW_INSECURE_GRPC`, set `TLS_CERTIFICATE` and
`TLS_PRIVATE_KEY`, and optionally set `TLS_CLIENT_CA` to require mTLS. Clients
then pass the server CA as `root_certificates`; when mTLS is enabled they also
pass `client_certificate_chain` and `client_private_key`. The example client
accepts the equivalent `--root-certificate`, `--client-certificate`, and
`--client-private-key` files. Rotate a token by replacing the secret and
gracefully restarting; plugins serve aligned raw fallback while reconnecting.

`/healthz` means the process loop is alive. `/readyz` means the locked model,
CUDA/A6000 warm-up, state isolation, and scheduler are ready. `/metrics` and the
gRPC health service expose serving status. Stream IDs, room names, participant
identities, tokens, and audio are not metric labels.

SIGTERM first clears readiness, sets gRPC health to not-serving, drains within
`GRACEFUL_SHUTDOWN_S`, then closes scheduler and HTTP resources. Use rolling
restart capacity so plugins fall open briefly instead of losing audio.

Troubleshooting:

- CUDA/name failure: compare PyTorch device ordering and NVIDIA UUIDs; never
  change the name check to obtain a CPU or P40 fallback.
- Hash failure: delete only the corrupt ignored cache and rerun `make model`.
- `UNAUTHENTICATED`: verify the mounted token; headers are intentionally absent
  from logs.
- `INVALID_ARGUMENT`: inspect sequence and absolute offsets, not PCM payload.
- Raw fallback: compare plugin fallback/late counters with server batch,
  inference, and hop latency histograms; do not hide overload with larger
  unbounded queues.

Model upgrades are explicit lockfile updates: resolve a release commit/asset,
replace every size/hash, vendor and review changed inference code, regenerate
parity evidence, build a new image, and capacity-test it before rollout.
