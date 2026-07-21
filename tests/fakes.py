"""Deterministic test doubles; never importable through production configuration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import torch
from fastenhancer_server.model import HOP_SAMPLES, RF_FREQ, RF_HIDDEN, StreamCaches


@dataclass(slots=True)
class IdentityDelayModel:
    model_name: str = "FastEnhancer-B"
    model_revision: str = "49ab55a57e3a064d94c6412cd0f0a383a55ca0f8"
    checkpoint_sha256: str = "980ec00d9c3cb0497893c815c718a2fe44970329ae8477d22596d0a1373f2382"
    cuda_device_name: str = "test-double"

    def initialize_stream(self) -> StreamCaches:
        return StreamCaches(
            stft=torch.zeros(1, HOP_SAMPLES),
            istft=torch.zeros(1, HOP_SAMPLES),
            rnn=tuple(torch.zeros(1, RF_FREQ, RF_HIDDEN) for _ in range(3)),  # type: ignore[arg-type]
        )

    def infer_batch(
        self, wav_hops: torch.Tensor, stream_caches: Sequence[StreamCaches]
    ) -> tuple[torch.Tensor, list[StreamCaches]]:
        output = torch.stack([cache.stft[0] for cache in stream_caches])
        updated: list[StreamCaches] = []
        for index, cache in enumerate(stream_caches):
            updated.append(
                StreamCaches(
                    stft=wav_hops[index : index + 1].clone(),
                    istft=cache.istft.clone(),
                    rnn=tuple(value.clone() for value in cache.rnn),  # type: ignore[arg-type]
                )
            )
        return output, updated
