# Architecture

```text
LiveKit AudioFrame (one track / processor)
  -> bounded raw absolute timeline
  -> one joinable transport thread and persistent gRPC channel
  -> one Enhance RPC generation
  -> bounded StreamSession rechunker and queues
  -> fair central scheduler (at most one hop per stream per batch)
  -> one FastEnhancer-B instance on NVIDIA RTX A6000
  -> split caches back to owning sessions
  -> offset-stamped EnhancedAudio
  -> enhanced segment or same-range raw fallback
```

`FastEnhancerBStreamingModel` is the sole production model class. It validates
the manifest and released YAML, hashes and strictly loads the checkpoint,
removes upstream weight reparameterizations, moves every parameter/cache to
float32 CUDA, and serializes calls. There is no provider interface, registry,
CPU backend, ONNX fallback, or per-stream model copy.

Each `StreamSession` owns a rechunk remainder, two `[1,256]` signal caches,
three `[1,24,36]` GRU caches, sequence/offset cursors, one delayed source hop,
and bounded queues. For batch size B, signal caches concatenate on axis 0 and
GRU caches on axis 1 into `[1,B*24,36]`. A session is not eligible for another
batch until its current inference has returned updated caches.

The scheduler uses a ready queue containing each session at most once. It
collects for at most `MAX_BATCH_WAIT_MS`, executes one call in a dedicated
single-thread CUDA executor, then requeues sessions with more work. A slow
client cannot block the global scheduler: output overflow terminates that RPC
with `RESOURCE_EXHAUSTED`.

Readiness requires manifest/checkpoint verification, exact A6000 name, real
CUDA warm-up through batch sizes, finite output, and impulse-based batch versus
independent state-isolation parity. The gRPC health service and `/readyz` remain
not-ready until all checks pass.
