# Protocol and sample timeline

`fastenhancer.v1.EnhancementService.Enhance` is the only audio method and one
RPC is exactly one model state. `GetCapabilities` exposes the locked identity
before streaming. Both methods require `authorization: Bearer <token>`.

The first client item is `StartStream(protocol_version="1")`, followed by
contiguous `AudioChunk` sequence and absolute sample offsets, and an optional
final `EndStream`. The server answers with `StreamAccepted`, ordered
`EnhancedAudio`, then `StreamEnded`. Fatal framing errors abort with a precise
gRPC status rather than embedding PCM in an error.

Validation limits are 128 UTF-8 bytes for stream IDs, 16 metadata entries,
64-byte keys, 256-byte values, and `MAX_AUDIO_CHUNK_SAMPLES` PCM samples.
Payloads must be positive even byte counts. Sequences begin at zero; offsets
start at `input_start_sample` and have no gaps, overlaps, or duplicates.

## Delay and flush

The waveform wrapper initially combines 256 zero-cache samples with source hop
0. Its first output is startup and is not emitted. The next model output maps
to source hop 0, so `output_start_sample` always names original input—not model
wall time. At `EndStream(flush=true)`, one zero hop emits the last pending source
hop. A partial final source is zero-padded internally and trimmed to its real
length. Therefore a clean flush produces exactly the input sample count.

`flush=false`, cancellation, timeout, and protocol failure do not claim exact
output length. Reconnect creates a new RPC/model state at a new absolute start;
responses from an older plugin generation cannot enter the newer timeline.
