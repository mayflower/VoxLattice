"""Per-instance counters without process-global registry side effects."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class PluginMetrics:
    frames_in: int = 0
    samples_in: int = 0
    frames_out: int = 0
    samples_out: int = 0
    enhanced_samples: int = 0
    raw_fallback_samples: int = 0
    late_response_samples: int = 0
    reconnects: int = 0
    protocol_mismatches: int = 0
    queue_overflows: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)
