# gRPC protocol and audio timeline

The public schema is
[`proto/fastenhancer/v1/enhancement.proto`](../proto/fastenhancer/v1/enhancement.proto).
The package name is `fastenhancer.v1`; incompatible future protocols must use a
new package or protocol version.

Every RPC requires this metadata header:

```text
authorization: Bearer <token>
```

Use TLS whenever the connection is not restricted to localhost or an isolated
private network.

## Service methods

### `GetCapabilities`

Returns the protocol version, model name and identity, audio format, hop and
delay sizes, selected CUDA device name, maximum audio chunk size, and maximum
active streams. Clients should call it during startup or rely on the
`StreamAccepted` message, then reject an unexpected model identity or audio
contract.

### `Enhance`

`Enhance` is bidirectional streaming. One RPC represents exactly one model
state and one contiguous audio timeline.

Client message order:

1. Exactly one `StartStream`.
2. Zero or more contiguous `AudioChunk` messages.
3. Exactly one `EndStream` for a clean completion.

Server message order:

1. One `StreamAccepted`.
2. Ordered `EnhancedAudio` messages.
3. One `StreamEnded` after a clean completion.

Fatal framing and resource errors terminate the RPC with a gRPC status rather
than returning audio in an error payload.

## Audio contract

- Sample rate: 16,000 Hz
- Channels: 1
- Format: signed PCM16 little-endian
- Model hop: 256 samples
- Algorithmic delay: 256 samples (16 ms)

`AudioChunk` payloads may contain arbitrary positive sample counts up to
`max_audio_chunk_samples`; the server rechunks them into model hops. Byte length
must be even.

`sequence` starts at zero and increments by one. `input_start_sample` in every
chunk must equal the end of the previous chunk. Gaps, overlaps, duplicates, and
out-of-order chunks are rejected.

## Start metadata limits

| Field | Limit |
|---|---:|
| UTF-8 `stream_id` | 128 bytes |
| Metadata entries | 16 |
| Metadata key | 64 UTF-8 bytes |
| Metadata value | 256 UTF-8 bytes |

Stream IDs must be unique among currently active RPCs. Metadata is protocol
context, not an authorization mechanism. Do not put tokens, private data, or
audio into it.

## Delay and clean flush

The first model call produces startup output from the initial cache and source
hop 0; that startup output is not emitted. The next model output corresponds to
source hop 0. Consequently, every `output_start_sample` refers to the original
input timeline rather than server wall-clock time.

With `EndStream(flush=true)`, the server sends one internal zero hop through the
model to emit the final pending source hop. A partial final hop is padded for
inference and trimmed back to its real length. On clean completion:

```text
StreamEnded.input_samples == StreamEnded.output_samples
```

With `flush=false`, client cancellation, timeout, or protocol failure, exact
output length is not guaranteed.

## Reconnection

A reconnect creates a new RPC and a fresh model state. The client may choose a
new `input_start_sample` to continue its own absolute timeline, but recurrent
model context from the lost stream cannot be restored. Clients must discard
responses belonging to an older connection generation.

## Common gRPC statuses

| Status | Typical cause |
|---|---|
| `UNAUTHENTICATED` | Missing or incorrect bearer token |
| `INVALID_ARGUMENT` | Unsupported audio format, malformed payload, invalid metadata, sequence, or offset |
| `FAILED_PRECONDITION` | Missing/duplicate start or end message, unsupported protocol version |
| `ALREADY_EXISTS` | Another active stream uses the same stream ID |
| `RESOURCE_EXHAUSTED` | Stream limit, chunk limit, or bounded queue exceeded |
| `DEADLINE_EXCEEDED` | Stream start or subsequent input remained idle too long |
| `INTERNAL` | CUDA inference or unexpected server processing failure |

Clients may retry transport and transient resource failures with bounded
backoff, but must create a new stream state. Correct malformed requests before
retrying `INVALID_ARGUMENT` or `FAILED_PRECONDITION` errors.
