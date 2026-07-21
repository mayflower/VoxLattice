# Benchmarking

`benchmarks/load.py` starts N independent bidi RPCs behind a barrier, sends one
256-sample hop every 16 ms, flushes, and validates every output offset and
sample count. It records RTT p50/p95/p99, server histogram quantiles,
real-time factor, per-stream counts/errors, a real `RemoteFastEnhancer` fallback
probe, and repeated A6000 UUID/utilization/memory samples while every load level
is active. JSON and Markdown are written under
`artifacts/validation/<UTC timestamp>/`.

Default stages are 1, 8, 16, 32, and 64 streams. Select one with
`BENCH_TARGET_STREAMS`; configure duration/warm-up with `BENCH_DURATION_S` and
`BENCH_WARMUP_S`. The fallback probe defaults to a deliberately conservative
100-ms `BENCH_PLUGIN_RESPONSE_WAIT_MS`; set it to the deployed plugin value when
capacity-qualifying that configuration. Gates are explicit environment values. A missing histogram,
telemetry failure, offset error, fallback excess, or threshold miss is visible
and exits nonzero. Measurements are never synthesized.

Optimize only after inspecting an artifact. Preserve one dependent hop per
stream and the bounded queues. Any change to batching, pinned memory,
preallocation, compilation, CUDA graphs, or precision requires before/after
artifacts plus waveform and state-isolation parity. FP16 is not enabled.
