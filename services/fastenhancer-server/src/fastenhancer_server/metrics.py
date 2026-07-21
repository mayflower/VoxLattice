"""Low-cardinality Prometheus metrics for server behavior."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info


class ServerMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.info = Info("fastenhancer", "Loaded model and CUDA device", registry=self.registry)
        self.active_streams = Gauge(
            "fastenhancer_active_streams", "Currently active RPC streams", registry=self.registry
        )
        self.streams = Counter(
            "fastenhancer_streams", "Stream lifecycle events", ["result"], registry=self.registry
        )
        self.stream_rejections = Counter(
            "fastenhancer_stream_rejections",
            "Rejected stream attempts",
            ["reason"],
            registry=self.registry,
        )
        self.input_samples = Counter(
            "fastenhancer_input_samples", "Accepted input samples", registry=self.registry
        )
        self.output_samples = Counter(
            "fastenhancer_output_samples", "Produced output samples", registry=self.registry
        )
        self.input_queue_samples = Gauge(
            "fastenhancer_input_queue_samples",
            "Queued input samples across streams",
            registry=self.registry,
        )
        self.output_queue_samples = Gauge(
            "fastenhancer_output_queue_samples",
            "Queued output samples across streams",
            registry=self.registry,
        )
        self.batch_size = Histogram(
            "fastenhancer_batch_size",
            "Micro-batch size",
            buckets=(1, 2, 4, 8, 16, 24, 32, 48, 64),
            registry=self.registry,
        )
        latency_buckets = (0.0005, 0.001, 0.002, 0.004, 0.008, 0.012, 0.016, 0.025, 0.05, 0.1)
        self.batch_wait = Histogram(
            "fastenhancer_batch_wait_seconds",
            "Time collecting a batch",
            buckets=latency_buckets,
            registry=self.registry,
        )
        self.inference = Histogram(
            "fastenhancer_inference_seconds",
            "CUDA inference wall time",
            buckets=latency_buckets,
            registry=self.registry,
        )
        self.hop_end_to_end = Histogram(
            "fastenhancer_hop_end_to_end_seconds",
            "Hop queue-to-output latency",
            buckets=latency_buckets,
            registry=self.registry,
        )
        self.protocol_errors = Counter(
            "fastenhancer_protocol_errors",
            "Fatal protocol errors",
            ["code"],
            registry=self.registry,
        )
        self.backpressure = Counter(
            "fastenhancer_backpressure", "Bounded queue failures", ["queue"], registry=self.registry
        )
        self.auth_failures = Counter(
            "fastenhancer_auth_failures", "Authentication failures", registry=self.registry
        )
