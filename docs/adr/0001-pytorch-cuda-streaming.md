# ADR 0001: PyTorch CUDA is the sole production backend

Status: accepted, 2026-07-21.

FastEnhancer's official export wrapper uses streaming STFT and a custom iSTFT
containing `torch.fft.ifft`; its checked-in ONNX test constructs ONNX Runtime
with `CPUExecutionProvider`. ONNX Runtime chooses CUDA only per supported node
and otherwise falls back to CPU when CPU is also registered, so the mere
presence of a CUDA provider is not proof that the waveform path stays on GPU.

The repository therefore runs the official architecture directly in the
locked PyTorch 2.10.0 wheels with CUDA 12.8/cuDNN 9 on a pinned Python 3.12.12
runtime image. NVIDIA documents CUDA 12.x minor-version compatibility for
drivers in its CUDA compatibility documentation. Parameters, waveform,
FFT/iFFT buffers, and caches are
validated on the selected CUDA device. Startup fails when CUDA is unavailable
or the explicitly configured device does not exist.
ONNX is neither a backend nor a fallback. A future proposal would have to pin
an ORT version and exported graph, capture provider assignment for every node,
and prove full-path CUDA parity before replacing this ADR.
