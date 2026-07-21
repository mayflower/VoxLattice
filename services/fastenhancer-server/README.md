# FastEnhancer server

CUDA inference server for
[VoxLattice](https://github.com/mayflower/VoxLattice). It exposes a versioned,
authenticated bidirectional gRPC API plus HTTP health and Prometheus endpoints.

The supported installation path is the repository's Docker build because the
server requires a verified FastEnhancer checkpoint and pinned CUDA/PyTorch
runtime:

```bash
git clone https://github.com/mayflower/VoxLattice.git
cd VoxLattice
cp deploy/.env.example deploy/.env
# Set FASTENHANCER_API_TOKEN and FASTENHANCER_GPU_DEVICE_ID.
make up
```

See the [quick start](https://github.com/mayflower/VoxLattice#quick-start-from-source)
and [operations guide](https://github.com/mayflower/VoxLattice/blob/main/docs/operations.md)
for GPU prerequisites, TLS, configuration, health checks, and upgrades.
