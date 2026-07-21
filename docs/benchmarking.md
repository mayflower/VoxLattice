# Benchmarking and capacity planning

The benchmark opens concurrent bidirectional gRPC streams, sends one 256-sample
hop every 16 ms, and validates every output offset and sample count. It also
probes the real LiveKit plugin fallback path while sampling server metrics and
GPU utilization.

Use it to choose a safe stream limit for a specific GPU, server configuration,
network path, and plugin latency budget. Results from another host are not a
capacity guarantee.

## Run a benchmark

Install the pinned local tools once with `make bootstrap`.

Start the service. `make benchmark` reads the token and selected GPU from
`deploy/.env`:

```bash
make up
make benchmark
```

By default the benchmark tests 1, 8, 16, 32, and 64 concurrent streams. To test
one level:

```bash
BENCH_TARGET_STREAMS=16 make benchmark
```

The report is written as JSON and Markdown under
`artifacts/validation/<UTC timestamp>/`. A failed correctness or latency gate
returns a nonzero exit status.

## Workload settings

| Variable | Default | Purpose |
|---|---:|---|
| `BENCH_ENDPOINT` | `127.0.0.1:50051` | gRPC server endpoint |
| `BENCH_METRICS_URL` | `http://127.0.0.1:8080/metrics` | Prometheus endpoint |
| `BENCH_STREAM_LEVELS` | `1,8,16,32,64` | Comma-separated concurrency stages |
| `BENCH_TARGET_STREAMS` | unset | Run one concurrency stage instead |
| `BENCH_DURATION_S` | `10` | Measured audio duration per stream |
| `BENCH_WARMUP_S` | `2` | Warm-up duration excluded from RTT samples |
| `BENCH_PLUGIN_RESPONSE_WAIT_MS` | `100` | Plugin wait budget used by the fallback probe |
| `BENCH_OUTPUT_DIR` | timestamped directory | Explicit report destination |
| `BENCH_IMAGE_DIGEST` | unset | Optional deployed-image identifier recorded in the report |

Set `BENCH_PLUGIN_RESPONSE_WAIT_MS` to the value used by the deployed plugin
when validating real end-to-end behavior. The benchmark default is deliberately
more tolerant than the plugin's low-latency default.

## Gates

| Variable | Default | Failure condition |
|---|---:|---|
| `BENCH_MAX_SERVER_P95_MS` | `25` | Server inference p95 exceeds the limit |
| `BENCH_MAX_RTT_P95_MS` | `100` | Client round-trip p95 exceeds the limit |
| `BENCH_MAX_FALLBACK_RATIO` | `0` | Plugin raw fallback ratio exceeds the limit |
| `BENCH_MAX_ERROR_RATIO` | `0` | Stream error ratio exceeds the limit |

Missing telemetry, missing latency histograms, inconsistent sample offsets, or
an incorrect flushed output length also fail the run.

## Read the report

The main fields are:

- `rtt_ms`: client-observed p50, p95, and p99 response time;
- `server_ms`: server batch-wait, inference, and end-to-end hop histograms;
- `batch_distribution`: number and mean size of inference batches;
- `gpu_during_load`: model name, utilization, and memory range sampled during
  the stage;
- `plugin_probe`: enhanced-versus-fallback sample counts through
  `RemoteFastEnhancer`;
- `gates`: thresholds and human-readable failure reasons.

Real-time factor alone is not enough for an interactive service. Select a
production limit below the first level where p95/p99 latency, queueing, errors,
fallback, or GPU saturation becomes unstable. Leave headroom for traffic bursts,
rolling updates, and other workloads on the GPU.

Repeat the benchmark after changing the model, CUDA/PyTorch version, GPU,
batching, queue limits, plugin wait budget, or network path. Keep reports from
the same methodology when comparing releases.
