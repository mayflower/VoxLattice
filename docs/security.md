# Security and privacy

VoxLattice processes live PCM in memory and is designed to avoid persisting or
observing audio content. Operators are still responsible for securing the host,
GPU runtime, network, certificates, tokens, metrics endpoint, and surrounding
LiveKit deployment.

Report suspected vulnerabilities privately according to
[`SECURITY.md`](../SECURITY.md).

## Deployment checklist

- Use TLS for every connection beyond localhost or an isolated private network.
- Use mTLS when clients also need certificate-based identity.
- Generate a unique bearer token, mount it through a secret manager, and rotate
  it when access changes.
- Keep the health and Prometheus HTTP port private.
- Retain the container's read-only filesystem, dropped capabilities,
  `no-new-privileges`, non-root user, and bounded tmpfs.
- Restrict which workloads and users can access the selected GPU and Docker
  socket.
- Disable core dumps if the surrounding platform enables them.
- Keep VoxLattice, its base image, NVIDIA runtime, and host packages updated.

The local Compose file intentionally enables plaintext gRPC but binds it to
`127.0.0.1`. Use the TLS overlays or an equivalent orchestrator configuration
before changing that bind address.

## Authentication and transport

There is no working default credential. The server accepts either
`FASTENHANCER_API_TOKEN` or `FASTENHANCER_API_TOKEN_FILE`, never both, and
requires at least 16 characters. Bearer comparison is constant-time.

TLS is required by default in the server process. Plaintext must be explicitly
enabled with `ALLOW_INSECURE_GRPC=true`. A configured `TLS_CLIENT_CA` changes
the listener to mutual TLS while the bearer-token requirement remains active.

## Data handling

Audio exists in bounded in-memory buffers required for streaming and fallback.
It is not written to disk, logged, traced, included in exceptions, or attached
to metric labels. Tokens and private keys are excluded from configuration
representations and logs.

The LiveKit example sets `record=False`; users should separately review their
LiveKit Cloud, agent observability, room recording, logging, and retention
settings. VoxLattice cannot prevent another component in the audio path from
recording the signal.

Stream metadata can contain room and participant context. The server validates
its size but does not use it for authorization or metric labels. Do not add
secrets or unnecessary personal data to custom metadata.

## Resource limits

RPC message size, active stream count, metadata, per-stream input/output queues,
plugin buffers, and idle time are bounded. Overload fails a stream with a gRPC
resource status instead of growing memory without limit. Operators must choose
limits appropriate for the GPU and enforce network-level connection and traffic
controls where denial-of-service exposure exists.

## Supply chain

The model asset, checkpoint, model configuration, upstream source provenance,
base image, Python dependencies, and GitHub Actions are pinned. Model hashes are
checked during preparation and again during server startup. The runtime image
uses a separate non-root stage and does not contain Git or the build toolchain.

CI audits Python dependencies, scans the container, runs CodeQL and dependency
review, and produces a CycloneDX SBOM with release candidates. These controls do
not replace review of deployment-specific images and dependencies.

## Reviewed dependency-audit exceptions

`make audit` is fail-closed except for these PyTorch 2.10.0 advisories:

- `PYSEC-2026-139` concerns local PT2 artifact loading. VoxLattice does not load
  PT2 packages or invoke `torch.compile`; it loads a SHA-256-pinned state
  dictionary with `weights_only=True`.
- `PYSEC-2025-194` concerns local `torch.jit.script` memory corruption.
  VoxLattice does not invoke TorchScript. The listed fixed line currently
  requires a different CUDA runtime contract.

These exceptions are limited to the named advisories; every additional finding
still fails the audit. They must be removed when a compatible fixed PyTorch
release is adopted.
