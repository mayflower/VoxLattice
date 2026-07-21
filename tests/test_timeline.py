from __future__ import annotations

import pytest
from livekit.plugins.fastenhancer.timeline import ContiguousPCM, EnhancedSegments


def test_raw_ranges_are_absolute() -> None:
    raw = ContiguousPCM(100)
    raw.append(100, b"\x01\x00\x02\x00")
    raw.append(102, b"\x03\x00")
    assert raw.read(101, 2) == b"\x02\x00\x03\x00"
    raw.discard_before(102)
    assert raw.start_sample == 102
    with pytest.raises(KeyError):
        raw.read(101, 1)


def test_enhanced_segments_require_gapless_read() -> None:
    enhanced = EnhancedSegments()
    enhanced.insert(10, b"\x01\x00\x02\x00")
    enhanced.insert(12, b"\x03\x00")
    assert enhanced.read(10, 3) == b"\x01\x00\x02\x00\x03\x00"
    assert enhanced.read(9, 1) is None
    with pytest.raises(ValueError):
        enhanced.insert(11, b"\xff\x7f")
    enhanced.discard_before(12)
    assert enhanced.read(12, 1) == b"\x03\x00"
