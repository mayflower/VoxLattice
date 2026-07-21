# Security and privacy

The container runs UID/GID 65532, drops Linux capabilities, enables
no-new-privileges, uses a read-only root filesystem and bounded tmpfs, and
contains neither Git nor build toolchain from the builder stage. Model and
manifest are immutable image inputs and are rehashed at process startup.

There is no working default secret. Bearer comparison is constant-time. TLS is
mandatory unless insecure transport is explicitly enabled for a local isolated
network; a client CA enables mTLS. RPC message sizes, active streams, metadata,
per-stream buffers, output queues, and idle time are bounded.

Audio bytes are never logged, traced, stored, placed in exceptions, or attached
to metric labels. Structured logs contain lifecycle/error classes only. The
LiveKit example passes `record=False` so Agents observability does not upload
audio. Disable core dumps in the deployment runtime if the surrounding platform
enables them.

CI audits Python dependencies, scans the built container, generates an SBOM,
and checks that normal tests use no model/network downloads. Release artifacts
must include `THIRD_PARTY_NOTICES.md` and the vendored MIT license.

## Dependency-audit exceptions

`make audit` is fail-closed except for these reviewed PyTorch 2.10.0 findings:

- `PYSEC-2026-139` concerns local PT2 artifact loading and has no published
  fixed release. This service never loads PT2 packages or invokes
  `torch.compile`; it loads one SHA-256-pinned upstream state dictionary with
  `weights_only=True`.
- `PYSEC-2025-194` concerns local `torch.jit.script` memory corruption. The
  service never invokes TorchScript. The first listed fix is PyTorch 2.13.0,
  whose CUDA 13 runtime requires an NVIDIA driver newer than the deployed 550
  driver; silently changing the production runtime would break the A6000 gate.

These are not blanket CVE suppressions: any additional finding still fails CI.
Re-review both exceptions and the pinned package version by 2026-08-21, and
remove them as soon as a CUDA-12-compatible fixed PyTorch build is available.
