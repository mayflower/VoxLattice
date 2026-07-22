#!/usr/bin/env python3
"""Measure how much noise VoxLattice removes from a clip.

The tool has two subcommands:

``generate``
    Write a deterministic 16 kHz mono PCM16 clip of voiced "speech" separated by
    silence gaps, mixed with broadband noise at a chosen SNR. A sidecar JSON next
    to the clip records the exact silence ranges so ``measure`` does not have to
    guess them (energy detection is unreliable at low SNR, where the noise floor
    rivals speech).

``measure``
    Compare the silence-gap noise floor of the original clip and the enhanced
    clip and report the reduction in dB, plus the change in speech energy.

Typical flow against a running server (see docs/operations.md)::

    python tools/denoise_check.py generate noisy.wav
    make enhance INPUT=noisy.wav OUTPUT=enhanced.wav
    python tools/denoise_check.py measure noisy.wav enhanced.wav

The service does not resample; the contract is 16 kHz mono signed 16-bit PCM.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import wave
from array import array
from pathlib import Path

SAMPLE_RATE_HZ = 16_000


def _write_wav(path: Path, samples: array) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE_HZ)
        handle.writeframes(samples.tobytes())


def _read_wav(path: Path) -> array:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16 kHz mono PCM16")
        if handle.getframerate() != SAMPLE_RATE_HZ:
            raise ValueError(f"{path}: expected {SAMPLE_RATE_HZ} Hz, got {handle.getframerate()}")
        samples = array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
    return samples


def _rms(samples: array, ranges: list[tuple[int, int]]) -> float:
    total = 0.0
    count = 0
    for start, end in ranges:
        end = min(end, len(samples))
        for value in samples[start:end]:
            scaled = value / 32768.0
            total += scaled * scaled
            count += 1
    if count == 0:
        return float("nan")
    return math.sqrt(total / count)


def generate_clip(seconds: float, snr_db: float, seed: int) -> tuple[array, list[tuple[int, int]]]:
    """Return a noisy clip and the sample ranges that are silence before mixing."""
    rng = random.Random(seed)  # noqa: S311 - audio synthesis, not security
    word_samples = int(0.8 * SAMPLE_RATE_HZ)
    gap_samples = int(0.5 * SAMPLE_RATE_HZ)
    clean: list[float] = []
    silence: list[tuple[int, int]] = []
    words = max(1, int(seconds / 1.3))
    for index in range(words):
        f0 = 110.0 + (8 * index) % 40
        phase = 0.0
        for n in range(word_samples):
            f0_n = f0 * (1 + 0.03 * math.sin(2 * math.pi * 4 * n / SAMPLE_RATE_HZ))
            phase += 2 * math.pi * f0_n / SAMPLE_RATE_HZ
            harmonics = sum(
                amp * math.sin(k * phase)
                for k, amp in enumerate((1.0, 0.6, 0.4, 0.25, 0.15, 0.1), start=1)
            )
            envelope = 0.5 + 0.5 * math.exp(-(((n % 1600) - 800) ** 2) / (2 * 300**2))
            clean.append(harmonics * envelope)
        start = len(clean)
        clean.extend([0.0] * gap_samples)
        silence.append((start, start + gap_samples))

    peak = max((abs(value) for value in clean), default=1.0) or 1.0
    clean = [0.5 * value / peak for value in clean]

    active = [value for value in clean if value != 0.0]
    speech_rms = math.sqrt(sum(value * value for value in active) / max(1, len(active)))
    noise_gain = speech_rms / (10 ** (snr_db / 20))

    noisy = array("h")
    for value in clean:
        mixed = value + noise_gain * rng.gauss(0.0, 1.0)
        mixed = max(-0.999, min(0.999, mixed))
        noisy.append(int(mixed * 32767))
    return noisy, silence


def _db_ratio(numerator: float, denominator: float) -> float:
    """20*log10(numerator/denominator), saturating instead of dividing by zero."""
    if denominator == 0.0:
        return math.inf if numerator > 0.0 else 0.0
    if numerator == 0.0:
        return -math.inf
    return 20 * math.log10(numerator / denominator)


def measure(noisy: array, enhanced: array, silence: list[tuple[int, int]]) -> dict[str, float]:
    """Return silence-gap and speech RMS for both signals and the reduction in dB."""
    speech = _complement(silence, len(noisy))
    input_floor = _rms(noisy, silence)
    enhanced_floor = _rms(enhanced, silence)
    input_speech = _rms(noisy, speech)
    enhanced_speech = _rms(enhanced, speech)
    return {
        "input_floor": input_floor,
        "enhanced_floor": enhanced_floor,
        "floor_reduction_db": _db_ratio(input_floor, enhanced_floor),
        "input_speech": input_speech,
        "enhanced_speech": enhanced_speech,
        "speech_change_db": _db_ratio(enhanced_speech, input_speech),
    }


def _complement(ranges: list[tuple[int, int]], length: int) -> list[tuple[int, int]]:
    speech: list[tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(ranges):
        if start > cursor:
            speech.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        speech.append((cursor, length))
    return speech


def _ranges_path(clip: Path) -> Path:
    return clip.with_suffix(clip.suffix + ".ranges.json")


def _cmd_generate(args: argparse.Namespace) -> int:
    noisy, silence = generate_clip(args.seconds, args.snr_db, args.seed)
    out = Path(args.output)
    _write_wav(out, noisy)
    _ranges_path(out).write_text(json.dumps(silence), encoding="utf-8")
    print(
        f"wrote {out} ({len(noisy) / SAMPLE_RATE_HZ:.1f}s, SNR {args.snr_db:g} dB) "
        f"and {_ranges_path(out).name}"
    )
    return 0


def _cmd_measure(args: argparse.Namespace) -> int:
    noisy = _read_wav(Path(args.noisy))
    enhanced = _read_wav(Path(args.enhanced))
    ranges_file = _ranges_path(Path(args.noisy))
    if not ranges_file.exists():
        print(f"missing {ranges_file.name}; regenerate the clip with the generate subcommand")
        return 2
    silence = [tuple(pair) for pair in json.loads(ranges_file.read_text(encoding="utf-8"))]
    result = measure(noisy, enhanced, silence)
    print(
        f"silence noise floor: input={result['input_floor']:.5f} "
        f"enhanced={result['enhanced_floor']:.5f} "
        f"reduction={result['floor_reduction_db']:+.1f} dB"
    )
    print(
        f"speech level:        input={result['input_speech']:.5f} "
        f"enhanced={result['enhanced_speech']:.5f} "
        f"change={result['speech_change_db']:+.1f} dB"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write a deterministic noisy test clip")
    gen.add_argument("output")
    gen.add_argument("--seconds", type=float, default=14.0)
    gen.add_argument("--snr-db", type=float, default=5.0)
    gen.add_argument("--seed", type=int, default=7)
    gen.set_defaults(func=_cmd_generate)

    mea = sub.add_parser("measure", help="report noise-floor reduction after enhancement")
    mea.add_argument("noisy")
    mea.add_argument("enhanced")
    mea.set_defaults(func=_cmd_measure)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
