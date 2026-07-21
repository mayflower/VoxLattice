from __future__ import annotations

import inspect
from importlib.metadata import version

from livekit import rtc
from livekit.agents import room_io
from livekit.plugins import fastenhancer


def test_supported_livekit_contract_is_current() -> None:
    # 1.1.13 is both the lower bound and current highest release below 1.2.
    assert version("livekit") == "1.1.13"
    assert not inspect.iscoroutinefunction(rtc.FrameProcessor._process)
    assert issubclass(fastenhancer.RemoteFastEnhancer, rtc.FrameProcessor)
    options = room_io.AudioInputOptions(
        sample_rate=16_000,
        num_channels=1,
        frame_size_ms=32,
        noise_cancellation=None,
    )
    assert options.sample_rate == 16_000
    assert options.num_channels == 1
    assert room_io.RoomOptions(audio_input=options).get_audio_input_options() is options


def test_audio_frame_userdata_contract() -> None:
    userdata = {"track": "one"}
    frame = rtc.AudioFrame(bytes(64), 16_000, 1, 32, userdata=userdata)
    assert frame.data.format == "h"
    assert frame.userdata is userdata
