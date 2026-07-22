"""Unit coverage for the denoise measurement tool (no server required)."""

from __future__ import annotations

import importlib.util
from array import array
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "denoise_check", Path(__file__).resolve().parents[1] / "tools" / "denoise_check.py"
)
assert _SPEC is not None and _SPEC.loader is not None
denoise_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(denoise_check)


def test_generate_is_deterministic() -> None:
    first, ranges_a = denoise_check.generate_clip(seconds=4.0, snr_db=5.0, seed=7)
    second, ranges_b = denoise_check.generate_clip(seconds=4.0, snr_db=5.0, seed=7)
    assert first.tobytes() == second.tobytes()
    assert ranges_a == ranges_b
    assert ranges_a, "expected at least one silence gap"


def test_identical_signal_reports_no_reduction() -> None:
    noisy, silence = denoise_check.generate_clip(seconds=4.0, snr_db=5.0, seed=7)
    result = denoise_check.measure(noisy, noisy, silence)
    assert abs(result["floor_reduction_db"]) < 1e-6


def test_silencing_the_gaps_reports_strong_reduction() -> None:
    noisy, silence = denoise_check.generate_clip(seconds=4.0, snr_db=5.0, seed=7)
    denoised = array("h", noisy)
    for start, end in silence:
        for index in range(start, min(end, len(denoised))):
            denoised[index] = 0
    result = denoise_check.measure(noisy, denoised, silence)
    # The silence gaps carried real noise, so zeroing them is a large reduction.
    assert result["floor_reduction_db"] > 40.0
    # Speech regions are untouched, so their level does not change.
    assert abs(result["speech_change_db"]) < 1e-6
