# Contributing to VoxLattice

Thank you for helping improve VoxLattice. Contributions should preserve its
narrow contract: one CUDA FastEnhancer-B service, isolated per-stream state,
bounded queues, a versioned bidi-gRPC protocol, and a track-local LiveKit
processor.

## Before opening a change

- Use a GitHub issue for non-trivial bugs or design changes so scope can be
  agreed before substantial work begins.
- Use [SECURITY.md](SECURITY.md), not a public issue, for vulnerabilities.
- Keep pull requests focused. Do not combine unrelated cleanup and behavior
  changes.
- Do not submit audio, credentials, model weights, generated benchmark claims,
  or material you do not have permission to redistribute.

## Development setup

Python 3.12 and `uv` are required. GPU tests and the production server require
an NVIDIA CUDA device; production has no CPU fallback.

```bash
make bootstrap
make check
make test
make test-integration
```

When the verified model and a CUDA device are available, set
`FASTENHANCER_GPU_DEVICE_ID` to its UUID or index as shown by `nvidia-smi -L`:

```bash
cp deploy/.env.example deploy/.env
# Fill FASTENHANCER_API_TOKEN and FASTENHANCER_GPU_DEVICE_ID in deploy/.env.
make test-gpu
make compose-test
```

Never invent benchmark results. Attach the generated JSON and Markdown
artifacts when a change makes performance claims.

## Code and test expectations

- Add regression coverage for behavior changes.
- Preserve exact sample offsets, output lengths, flush behavior, and stream
  state isolation.
- Keep network, stream, and audio buffers bounded.
- Never log PCM, bearer tokens, private keys, room identities, or participant
  identities.
- Keep test doubles under `tests/`; production must fail rather than silently
  select a fake or CPU model.
- Update user-facing documentation and the changelog when appropriate.
- Run `make format` before the final checks.

Generated protobuf files must match `proto/fastenhancer/v1/enhancement.proto`.
Use `make proto` after an intentional schema change and never reuse published
field numbers.

## Pull requests

A pull request should explain the problem, the chosen solution, risk to audio
alignment or concurrency, and the exact verification performed. Maintainers
may request smaller commits or additional evidence. By contributing, you agree
that your contribution is licensed under the repository's MIT License and that
you have the right to submit it.

All contributors must follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
