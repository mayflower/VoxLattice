# Architecture

VoxLattice separates real-time audio integration from GPU inference. LiveKit
Agents run a lightweight track-local plugin; one or more server processes own
the model and CUDA resources.

```text
LiveKit input track
  -> RemoteFastEnhancer (one instance per track)
  -> persistent bidirectional gRPC stream
  -> StreamSession (rechunking, offsets, bounded queues)
  -> fair micro-batch scheduler
  -> one FastEnhancer-B model on the configured CUDA device
  -> offset-stamped enhanced audio
  -> matching enhanced interval or time-aligned raw fallback
```

## Plugin

`RemoteFastEnhancer` implements LiveKit's synchronous
`FrameProcessor[AudioFrame]` interface. Network I/O runs in one controlled worker
thread per processor, so frame processing does not create an event loop or a new
connection for every frame.

The plugin assigns absolute sample offsets to input, enhanced output, and raw
fallback. Only audio older than the model's 256-sample delay is eligible for
output. If the matching enhanced interval is late or missing, the plugin returns
the raw interval with the same offsets.

## Server sessions

One gRPC `Enhance` call represents one streaming model state. Its
`StreamSession` owns:

- input rechunking and sequence validation;
- STFT, inverse-STFT, and three recurrent caches;
- absolute input and output cursors;
- the delayed source hop used for exact flush behavior;
- bounded input and output queues.

Streams do not receive separate model instances or CUDA contexts. Closing or
reconnecting a stream discards only that stream's state.

## Micro-batching

The scheduler maintains a fair ready queue and takes at most one next hop from
each stream into a batch. Consecutive hops from the same stream cannot share a
batch because the second hop depends on caches produced by the first.

For batch size `B`, signal caches are combined on batch axis 0. The recurrent
caches have shape `[1, B * 24, 36]` and are combined on axis 1, then split back
into per-stream blocks after inference. A slow client cannot block every other
stream; exceeding a bounded output queue terminates that RPC with
`RESOURCE_EXHAUSTED`.

## Model lifecycle

The server loads exactly one FastEnhancer-B model. Startup verifies the model
manifest and checkpoint hash, moves parameters and caches to float32 CUDA,
warms representative batch sizes, verifies finite output, and checks that
batched streams remain state-isolated. Readiness and gRPC health remain false
until those checks and scheduler startup complete.

There is no production CPU or ONNX fallback. Startup fails if CUDA is
unavailable or the configured `cuda:N` device cannot be opened. The rationale is
recorded in [ADR 0001](adr/0001-pytorch-cuda-streaming.md).

## Failure behavior

Server validation errors terminate the affected RPC with a specific gRPC
status. Overload produces backpressure or a bounded failure instead of silently
dropping audio. The plugin reconnects with a fresh model state and continues
returning aligned raw audio until enhanced output becomes available again.

This behavior preserves the local audio timeline; it does not make a restarted
model state continuous with the state that was lost.
