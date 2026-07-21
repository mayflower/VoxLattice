# LiveKit integration

The package supports `livekit>=1.1.13,<1.2` and directly subclasses the current
synchronous `rtc.FrameProcessor[rtc.AudioFrame]` contract. It uses current
Agents `room_io.RoomOptions` and `AudioInputOptions`; the latter defaults to 24
kHz, so examples explicitly request 16 kHz mono.

`_process` copies input PCM once into a bounded absolute raw timeline and queues
the same bytes to a dedicated synchronous-gRPC thread. It outputs at most the
input frame size and only through `input_cursor - 256`. Thus a first 32-ms frame
returns 16 ms; a first 10-ms frame returns zero samples. Variable output frame
sizes are part of the contract.

The processor waits `response_wait_ms` for a gapless enhanced interval. If that
exact interval is absent, it returns raw PCM read at the same start/count. It
never combines a current raw frame with an older enhancement. Results below
`output_cursor` and results from an old generation are counted and discarded.

`enabled=False` cancels the RPC generation, clears remote buffers, and returns
new frames raw immediately. Re-enabling starts a new generation. `_close()` is
idempotent, best-effort flushes, cancels after a bounded wait, joins the worker,
and closes the channel. It cannot return the last delayed hop; continuous
silence after speech normally advances it before close.

Use the selector example for rooms that can change linked participants. A
processor owns exactly one track. LiveKit credentials are never sent to the
enhancement server; only the configured FastEnhancer token is used.

For mTLS, load the PEM files outside the repository and pass the server CA as
`root_certificates`, plus the client chain and key as
`client_certificate_chain` and `client_private_key`. The processor rejects a
half-configured certificate pair and never includes the private key in its
configuration representation.
