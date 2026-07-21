# Model provenance and preparation

VoxLattice uses the official FastEnhancer-B VCTK-Demand checkpoint from the
upstream `ckpt-vd-v1.0.0` release. `manifest.lock.json` records the upstream
repository and commit, release asset ID, byte sizes, archive hash, member names,
checkpoint hash, configuration hash, and model dimensions.

Prepare the model with:

```bash
make model
```

The command downloads the pinned release asset into the ignored
`models/cache/` directory, verifies the archive before extraction, and writes
only the expected checkpoint and configuration to ignored `models/prepared/`.
Running it again reuses a valid cache.

The Docker build copies the prepared files into the image. The server never
downloads weights at runtime and verifies the checkpoint hash again before
loading it.

Model assets are not committed to this repository. Review the upstream model
release and its license before redistributing a derived container image. Source
provenance and third-party license locations are listed in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
