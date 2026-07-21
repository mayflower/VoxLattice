from __future__ import annotations

import torch
from fastenhancer_server.audio import SourceHop
from fastenhancer_server.model import combine_stream_caches, split_stream_caches
from fastenhancer_server.pipeline import StreamingPipeline

from .fakes import IdentityDelayModel


def test_cache_split_combine_identity() -> None:
    model = IdentityDelayModel()
    caches = [model.initialize_stream() for _ in range(8)]
    for index, cache in enumerate(caches):
        cache.stft.fill_(index)
        for rnn in cache.rnn:
            rnn.fill_(index + 0.5)
    combined = combine_stream_caches(caches)
    split = split_stream_caches(combined, 8)
    for expected, actual in zip(caches, split, strict=True):
        assert torch.equal(expected.stft, actual.stft)
        assert torch.equal(expected.istft, actual.istft)
        assert all(torch.equal(a, b) for a, b in zip(expected.rnn, actual.rnn, strict=True))


def test_delay_and_exact_partial_flush() -> None:
    pipeline = StreamingPipeline(IdentityDelayModel())
    first = SourceHop(400, torch.arange(256, dtype=torch.float32) / 256, 256)
    second = SourceHop(656, torch.full((256,), 0.25), 17)
    assert pipeline.push(first) is None
    first_output = pipeline.push(second)
    assert first_output is not None
    assert first_output.start_sample == 400
    assert torch.equal(first_output.samples, first.samples)
    final = pipeline.flush()
    assert final is not None
    assert final.start_sample == 656
    assert final.valid_samples == 17
    assert torch.equal(final.samples, second.samples[:17])
    assert pipeline.flush() is None


def test_batch_matches_independent_streams() -> None:
    model = IdentityDelayModel()
    values = torch.stack([torch.full((256,), float(index)) for index in range(16)])
    initial = [model.initialize_stream() for _ in range(16)]
    _, updated = model.infer_batch(values, initial)
    batched, _ = model.infer_batch(torch.zeros_like(values), updated)
    assert torch.equal(batched, values)


def test_batched_stream_impulses_do_not_cross_talk() -> None:
    model = IdentityDelayModel()
    impulses = torch.zeros(4, 256)
    impulses[0, 7] = 1
    impulses[1, 31] = -0.5
    impulses[2, 127] = 0.25
    impulses[3, 255] = -1
    caches = [model.initialize_stream() for _ in range(4)]
    initial_output, caches = model.infer_batch(impulses, caches)
    assert torch.count_nonzero(initial_output) == 0
    delayed_output, _ = model.infer_batch(torch.zeros_like(impulses), caches)
    assert torch.equal(delayed_output, impulses)
