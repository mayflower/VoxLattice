"""Bounded absolute-sample timeline buffers used by the FrameProcessor."""

from __future__ import annotations

from dataclasses import dataclass


class ContiguousPCM:
    def __init__(self, start_sample: int = 0) -> None:
        self.start_sample = start_sample
        self._data = bytearray()

    @property
    def end_sample(self) -> int:
        return self.start_sample + len(self._data) // 2

    def append(self, start_sample: int, pcm_s16le: bytes) -> None:
        if start_sample != self.end_sample:
            raise ValueError("raw PCM append is not contiguous")
        if len(pcm_s16le) % 2:
            raise ValueError("PCM must have an even byte length")
        self._data.extend(pcm_s16le)

    def read(self, start_sample: int, sample_count: int) -> bytes:
        if start_sample < self.start_sample or start_sample + sample_count > self.end_sample:
            raise KeyError("requested raw range is unavailable")
        begin = (start_sample - self.start_sample) * 2
        return bytes(self._data[begin : begin + sample_count * 2])

    def discard_before(self, sample: int) -> None:
        remove_samples = min(max(0, sample - self.start_sample), len(self._data) // 2)
        if remove_samples:
            del self._data[: remove_samples * 2]
            self.start_sample += remove_samples

    def clear_at(self, start_sample: int) -> None:
        self.start_sample = start_sample
        self._data.clear()


@dataclass(frozen=True, slots=True)
class Segment:
    start_sample: int
    pcm_s16le: bytes

    @property
    def end_sample(self) -> int:
        return self.start_sample + len(self.pcm_s16le) // 2


class EnhancedSegments:
    def __init__(self) -> None:
        self._segments: list[Segment] = []

    def insert(self, start_sample: int, pcm_s16le: bytes) -> None:
        if not pcm_s16le or len(pcm_s16le) % 2:
            raise ValueError("enhanced PCM must have positive even byte length")
        candidate = Segment(start_sample, pcm_s16le)
        for existing in self._segments:
            if (
                candidate.end_sample <= existing.start_sample
                or candidate.start_sample >= existing.end_sample
            ):
                continue
            if candidate == existing:
                return
            raise ValueError("non-identical enhanced audio overlap")
        self._segments.append(candidate)
        self._segments.sort(key=lambda segment: segment.start_sample)

    def read(self, start_sample: int, sample_count: int) -> bytes | None:
        cursor = start_sample
        end = start_sample + sample_count
        output = bytearray()
        for segment in self._segments:
            if segment.end_sample <= cursor:
                continue
            if segment.start_sample > cursor:
                return None
            begin = (cursor - segment.start_sample) * 2
            take = min(segment.end_sample, end) - cursor
            output.extend(segment.pcm_s16le[begin : begin + take * 2])
            cursor += take
            if cursor == end:
                return bytes(output)
        return None

    def discard_before(self, sample: int) -> None:
        retained: list[Segment] = []
        for segment in self._segments:
            if segment.end_sample <= sample:
                continue
            if segment.start_sample < sample:
                begin = (sample - segment.start_sample) * 2
                retained.append(Segment(sample, segment.pcm_s16le[begin:]))
            else:
                retained.append(segment)
        self._segments = retained

    def clear(self) -> None:
        self._segments.clear()
