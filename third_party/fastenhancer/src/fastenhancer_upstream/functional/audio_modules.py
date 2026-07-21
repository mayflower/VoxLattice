import warnings
from typing import Optional, Tuple, List

import torch
from torch import Tensor, nn
import torch.nn.functional as F


class STFT(nn.Module):
    '''Short Time Fourier Transform
    forward(x):
        x: [B, T_wav] or [B, 1, T_wav]
        output: [B, n_fft//2+1, T_spec, 2]   (magnitude = False)
        output: [B, n_fft//2+1, T_spec]      (magnitude = True)
    inverse(x):
        x: [B,  n_fft//2+1, T_spec, 2]
        output: [B, T_wav]
    '''

    __constants__ = ["normalize", "center", "magnitude", "n_fft",
                     "hop_size", "win_size", "padding", "clip", "pad_mode"]
    __annotations__ = {'window': Optional[Tensor]}

    def __init__(
        self, n_fft: int, hop_size: int, win_size: Optional[int] = None,
        center: bool = True, magnitude: bool = False,
        win_type: Optional[str] = "hann",
        window: Optional[Tensor] = None, normalized: bool = False,
        pad_mode: str = "reflect",
        device=None, dtype=None
    ):
        super().__init__()
        self.normalized = normalized
        self.center = center
        self.magnitude = magnitude
        self.n_fft = n_fft
        self.hop_size = hop_size
        self.padding = 0 if center else (n_fft + 1 - hop_size) // 2
        self.clip = (hop_size % 2 == 1)
        self.pad_mode = pad_mode
        if win_size is None:
            win_size = n_fft

        if window is not None:
            win_size = window.size(-1)
        elif win_type is None:
            window = torch.ones(win_size, device=device, dtype=dtype)
        elif win_type == "povey":
            window = torch.hann_window(
                win_size,
                periodic=False,
                device=device,
                dtype=dtype
            ).pow(0.85)
        elif win_type == "hann-sqrt":
            window = torch.hann_window(
                win_size,
                periodic=False,
                device=device,
                dtype=dtype
            ).pow(0.5)
        else:
            window: Tensor = getattr(torch, f"{win_type}_window")(win_size,
                device=device, dtype=dtype)
        self.register_buffer("window", window, persistent=False)
        self.window: Tensor
        self.win_size = win_size
        assert n_fft >= win_size, f"n_fft({n_fft}) must be bigger than win_size({win_size})"

    def forward(self, x: Tensor) -> Tensor:
        # x: [B, T_wav] or [B, 1, T_wav]
        # output: [B, n_fft//2+1, T_spec(, 2)]
        if x.dim() == 3:  # [B, 1, T] -> [B, T]
            x = x.squeeze(1)
        if self.padding > 0:
            x = F.pad(x.unsqueeze(0), (self.padding, self.padding), mode=self.pad_mode).squeeze(0)

        spec = torch.stft(x, self.n_fft, hop_length=self.hop_size, win_length=self.win_size,
            window=self.window, center=self.center, pad_mode=self.pad_mode,
            normalized=self.normalized, onesided=True, return_complex=True)

        if self.magnitude:
            spec = spec.abs()
        else:
            spec = torch.view_as_real(spec)

        if self.clip:
            spec = spec[:, :, :-1]

        return spec

    def inverse(self, spec: Tensor) -> Tensor:
        # x: [B, n_fft//2+1, T_spec, 2]
        # output: [B, T_wav]
        if not self.center:
            raise ValueError("center=False is unsupported; set center=True")

        spec = torch.view_as_complex(spec.contiguous())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wav = torch.istft(spec, self.n_fft, hop_length=self.hop_size,
                win_length=self.win_size, center=self.center, normalized=self.normalized,
                window=self.window, onesided=True, return_complex=False)

        return wav

    def inverse_complex(self, spec: Tensor) -> Tensor:
        # x: [B, n_fft//2+1, T_spec] (complex)
        # output: [B, T_wav]
        if not self.center:
            raise ValueError("center=False is unsupported; set center=True")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wav = torch.istft(spec, self.n_fft, hop_length=self.hop_size,
                win_length=self.win_size, center=self.center, normalized=self.normalized,
                window=self.window, onesided=True, return_complex=False)

        return wav


class CompressedSTFT(STFT):
    def __init__(
        self,
        n_fft: int,
        hop_size: int,
        win_size: int,
        win_type: str = "hann",
        normalized: bool = False,
        compression: float = 1.0,
        discard_last_freq_bin: bool = False,
        eps: float = 1.0e-5,
    ) -> None:
        assert compression <= 1.0, compression
        super().__init__(
            n_fft=n_fft, hop_size=hop_size, win_size=win_size,
            win_type=win_type, normalized=normalized, magnitude=False
        )
        self.compression = compression
        self.eps = eps
        self.discard_last_freq_bin = discard_last_freq_bin

    def forward(self, x: Tensor) -> Tensor:
        # x: [B, 1, T_wav] or [B, T_wav]
        # output: [B, n_fft//2, T, 2] (real) if discard_last_freq_bin=True
        # output: [B, n_fft//2+1, T, 2] (real) if discard_last_freq_bin=False
        x = super().forward(x)
        if self.discard_last_freq_bin:
            x = x[:, :-1, :, :]
        mag = torch.linalg.norm(x, dim=-1, keepdim=True).clamp(min=self.eps)
        x = x * mag.pow(self.compression - 1.0)
        return x

    def inverse(self, x: Tensor) -> Tensor:
        # x: [B, n_fft//2, T] (complex) if discard_last_freq_bin=True
        # x: [B, n_fft//2+1, T] (complex) if discard_last_freq_bin=False
        # output: [B, T_wav]
        mag_compressed = x.abs()
        x = x * mag_compressed.pow(1.0 / self.compression - 1.0)
        if self.discard_last_freq_bin:
            x = F.pad(x, (0, 0, 0, 1))  # [B, n_fft//2F+1, T]
        return super().inverse_complex(x)


class ONNXSTFT(nn.Module):
    '''Short-Time Fourier Transform
    STFT: Implemented using torch.stft, which can be converted into onnx.stft.
    ISTFT: Implemented using torch.fft.irfft, because onnx.istft is currently not implemented.
    forward(x):
        x: [B, hop_size*L] or [B, hop_size*L]
        output: [B, N//2+1, L, 2]
    inverse(x):
        x: [B, N//2+1, L, 2]
        output: [B, hop_size*L]
    '''

    __constants__ = ["n_fft", "hop_size", "cache_len", "normalized",
                     "window", "weight"]

    def __init__(
        self,
        n_fft: int,
        hop_size: int,
        win_size: Optional[int] = None,
        win_type: Optional[str] = "hann",
        normalized: bool = False,
        device=None,
        dtype=None
    ):
        assert n_fft % 2 == 0, f"`n_fft` must be an even number, but given {n_fft}."
        assert normalized == False
        super().__init__()
        self.n_fft = n_fft
        self.hop_size = hop_size
        self.cache_len = n_fft - hop_size
        self.normalized = normalized

        if dtype is None:
            dtype = torch.float32
        factory_kwargs = {'device': device, 'dtype': dtype}

        if win_size is None:
            win_size = n_fft
        assert n_fft >= win_size, \
            f"n_fft({n_fft}) must be bigger than win_size({win_size})"

        # Get window
        if win_type is None:
            window = torch.ones(n_fft, **factory_kwargs)
        else:
            window: Tensor = getattr(torch, f"{win_type}_window")(
                win_size, **factory_kwargs)
            if win_size < n_fft:
                padding = n_fft - win_size
                window = F.pad(window, (padding//2, padding - padding//2))
        self.register_buffer("window", window, persistent=False)
        self.window: Tensor

        # Get iSTFT weight
        K = (n_fft + hop_size - 1) // hop_size  # <=> math.ceil(n_fft / hop_size)
        L = hop_size * (2*K-1) + (n_fft - hop_size)
        win_sq = window.square().view(1, -1, 1)     # [1, n_fft, 1]
        win_sq = win_sq.expand(1, -1, 2*K-1)        # [1, n_fft, 2*K-1]
        win_sq_sum = F.fold(
            win_sq,
            output_size = (1, L),
            kernel_size = (1, n_fft),
            stride = (1, hop_size),
            padding = (0, 0)
        ).view(-1)  # [n_fft-hop_size + hop_size*(2*K-1)]
        win_sq_sum = win_sq_sum[(K-1)*hop_size:(K-1)*hop_size + n_fft]  # [n_fft]
        window_istft = window / win_sq_sum
        self.register_buffer("window_istft", window_istft, persistent=False)
        self.window_istft: Tensor

    def _initialize_cache(self, x: Tensor) -> List[Tensor]:
        cache_stft = torch.zeros(x.size(0), self.cache_len, dtype=x.dtype, device=x.device)
        return [cache_stft]

    def _forward(self, x: Tensor) -> Tensor:
        '''x: [B=1, hop_size]
        cache: [B=1, n_fft-hop_size]
        output: [B, n_fft//2, T=1, 2]
        '''
        x = x * self.window
        x = torch.fft.rfft(x, dim=1)            # [1, self.n_fft//2+1] (complex)
        x = torch.view_as_real(x).unsqueeze(2)  # [1, self.n_fft//2+1, 1, 2] (real)
        # x = x.stft(n_fft=self.n_fft, hop_length=self.hop_size,
        #            window=self.window, normalized=self.normalized,
        #            center=False, onesided=True, return_complex=True)
        # x = torch.view_as_real(x)
        return x

    def initialize_cache(self, x: Tensor) -> List[Tensor]:
        cache_stft = torch.zeros(x.size(0), self.cache_len, dtype=x.dtype, device=x.device)
        cache_istft = torch.zeros(x.size(0), self.cache_len, dtype=x.dtype, device=x.device)
        return [cache_stft, cache_istft]

    def forward(self, x: Tensor, cache: Tensor) -> Tuple[Tensor, Tensor]:
        '''x: [B=1, hop_size]
        cache: [B=1, n_fft-hop_size]
        output: [B, n_fft//2, T=1, 2]
        '''
        x = torch.cat([cache, x], dim=1)  # [B, n_fft]
        cache = x[:, -self.cache_len:]    # [B, n_fft-hop_size]
        x = x * self.window
        x = torch.fft.rfft(x, dim=1)            # [1, self.n_fft//2+1] (complex)
        x = torch.view_as_real(x).unsqueeze(2)  # [1, self.n_fft//2+1, 1, 2] (real)
        # x = x.stft(n_fft=self.n_fft, hop_length=self.hop_size,
        #            window=self.window, normalized=self.normalized,
        #            center=False, onesided=True, return_complex=True)
        # x = torch.view_as_real(x)
        return x, cache

    def inverse(self, x: Tensor, cache: Tensor) -> Tuple[Tensor, Tensor]:
        '''input:
            x: [B, N//2+1, T=1, 2]
            cache: [B, N-H]
        output:
            x: [B, H*T=H]
            cache: [B, N-H]
        '''
        # Below is an original irFFT code.
        # x = torch.view_as_complex(x.view(self.n_fft//2+1, 2))
        # x = torch.fft.irfft(x).view(1, self.n_fft)
        # ONNX doesn't support irFFT with an input of [n_fft//2+1, 2].

        # Method 1) X: [B, n_fft//2+1] -> X_full: [B, n_fft] -> iFFT -> real part
        # x_full = nn.functional.pad(
        #     x.squeeze(2),
        #     (0, 0, 0, self.n_fft//2-1),
        #     mode='reflect'
        # )                                       # [B, n_fft, 2]
        # x_full[:, self.n_fft//2+1:, 1] *= -1    # complex conjugate
        # x_full = torch.view_as_complex(x_full)  # [B, n_fft] (complex)
        # x = torch.fft.ifft(x_full, dim=1).real  # [B, n_fft] (real)

        # Method 2)
        # x[n] = 1/N sigma_{k=0}^{N-1}{e^{j 2 \pi k / N * n} X[k]}
        #      = 2/N Re{ sigma_{k=0}^{N/2}{e^{j 2 \pi k / N * n} X[k]} } - 1/N*(X[0]+(-1)^n*X[N/2])
        x_0 = x[:, 0:1, 0, 0]
        x_last = x[:, -1:, 0, 0]
        x = nn.functional.pad(
            x.squeeze(2),
            (0, 0, 0, self.n_fft//2-1)
        )   # [B, n_fft, 2]
        x = torch.fft.ifft(torch.view_as_complex(x), dim=1).real        # [B, n_fft]
        # x = torch.fft.irfft(torch.view_as_complex(x), dim=1, n=self.n_fft)  # [B, n_fft]
        x = x.reshape(-1, self.n_fft//2, 2)                             # [B, n_fft//2, 2]
        correction = torch.stack([x_0 + x_last, x_0 - x_last], dim=2)   # [B, 1, 2]
        x = 2 * x - correction / self.n_fft
        x =  x.view(-1, self.n_fft)
        # irFFT end

        x = x * self.window_istft
        x[:, :cache.size(1)] += cache
        out = x[:, :-(self.n_fft - self.hop_size)]      # [B, H*T]
        cache = x[:, -(self.n_fft - self.hop_size):]    # [B, N-H]
        return out, cache
