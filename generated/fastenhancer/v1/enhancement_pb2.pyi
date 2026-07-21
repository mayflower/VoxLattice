from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SampleFormat(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SAMPLE_FORMAT_UNSPECIFIED: _ClassVar[SampleFormat]
    SAMPLE_FORMAT_PCM_S16LE: _ClassVar[SampleFormat]
SAMPLE_FORMAT_UNSPECIFIED: SampleFormat
SAMPLE_FORMAT_PCM_S16LE: SampleFormat

class ClientMessage(_message.Message):
    __slots__ = ("start", "audio", "end")
    START_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    start: StartStream
    audio: AudioChunk
    end: EndStream
    def __init__(self, start: _Optional[_Union[StartStream, _Mapping]] = ..., audio: _Optional[_Union[AudioChunk, _Mapping]] = ..., end: _Optional[_Union[EndStream, _Mapping]] = ...) -> None: ...

class StartStream(_message.Message):
    __slots__ = ("protocol_version", "stream_id", "input_start_sample", "sample_rate_hz", "channels", "sample_format", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    STREAM_ID_FIELD_NUMBER: _ClassVar[int]
    INPUT_START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_HZ_FIELD_NUMBER: _ClassVar[int]
    CHANNELS_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_FORMAT_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    protocol_version: str
    stream_id: str
    input_start_sample: int
    sample_rate_hz: int
    channels: int
    sample_format: SampleFormat
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, protocol_version: _Optional[str] = ..., stream_id: _Optional[str] = ..., input_start_sample: _Optional[int] = ..., sample_rate_hz: _Optional[int] = ..., channels: _Optional[int] = ..., sample_format: _Optional[_Union[SampleFormat, str]] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class AudioChunk(_message.Message):
    __slots__ = ("sequence", "input_start_sample", "pcm_s16le")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    INPUT_START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    PCM_S16LE_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    input_start_sample: int
    pcm_s16le: bytes
    def __init__(self, sequence: _Optional[int] = ..., input_start_sample: _Optional[int] = ..., pcm_s16le: _Optional[bytes] = ...) -> None: ...

class EndStream(_message.Message):
    __slots__ = ("flush",)
    FLUSH_FIELD_NUMBER: _ClassVar[int]
    flush: bool
    def __init__(self, flush: bool = ...) -> None: ...

class ServerMessage(_message.Message):
    __slots__ = ("accepted", "audio", "ended", "error")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    ENDED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    accepted: StreamAccepted
    audio: EnhancedAudio
    ended: StreamEnded
    error: StreamError
    def __init__(self, accepted: _Optional[_Union[StreamAccepted, _Mapping]] = ..., audio: _Optional[_Union[EnhancedAudio, _Mapping]] = ..., ended: _Optional[_Union[StreamEnded, _Mapping]] = ..., error: _Optional[_Union[StreamError, _Mapping]] = ...) -> None: ...

class StreamAccepted(_message.Message):
    __slots__ = ("protocol_version", "model_name", "model_revision", "model_sha256", "sample_rate_hz", "channels", "sample_format", "hop_samples", "algorithmic_delay_samples", "cuda_device", "max_audio_chunk_samples")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODEL_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_REVISION_FIELD_NUMBER: _ClassVar[int]
    MODEL_SHA256_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_HZ_FIELD_NUMBER: _ClassVar[int]
    CHANNELS_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_FORMAT_FIELD_NUMBER: _ClassVar[int]
    HOP_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    ALGORITHMIC_DELAY_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    CUDA_DEVICE_FIELD_NUMBER: _ClassVar[int]
    MAX_AUDIO_CHUNK_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    protocol_version: str
    model_name: str
    model_revision: str
    model_sha256: str
    sample_rate_hz: int
    channels: int
    sample_format: SampleFormat
    hop_samples: int
    algorithmic_delay_samples: int
    cuda_device: str
    max_audio_chunk_samples: int
    def __init__(self, protocol_version: _Optional[str] = ..., model_name: _Optional[str] = ..., model_revision: _Optional[str] = ..., model_sha256: _Optional[str] = ..., sample_rate_hz: _Optional[int] = ..., channels: _Optional[int] = ..., sample_format: _Optional[_Union[SampleFormat, str]] = ..., hop_samples: _Optional[int] = ..., algorithmic_delay_samples: _Optional[int] = ..., cuda_device: _Optional[str] = ..., max_audio_chunk_samples: _Optional[int] = ...) -> None: ...

class EnhancedAudio(_message.Message):
    __slots__ = ("output_sequence", "output_start_sample", "pcm_s16le", "valid_samples")
    OUTPUT_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    PCM_S16LE_FIELD_NUMBER: _ClassVar[int]
    VALID_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    output_sequence: int
    output_start_sample: int
    pcm_s16le: bytes
    valid_samples: int
    def __init__(self, output_sequence: _Optional[int] = ..., output_start_sample: _Optional[int] = ..., pcm_s16le: _Optional[bytes] = ..., valid_samples: _Optional[int] = ...) -> None: ...

class StreamEnded(_message.Message):
    __slots__ = ("input_samples", "output_samples", "flushed")
    INPUT_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    FLUSHED_FIELD_NUMBER: _ClassVar[int]
    input_samples: int
    output_samples: int
    flushed: bool
    def __init__(self, input_samples: _Optional[int] = ..., output_samples: _Optional[int] = ..., flushed: bool = ...) -> None: ...

class StreamError(_message.Message):
    __slots__ = ("code", "message", "retryable", "expected_input_start_sample")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_INPUT_START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    retryable: bool
    expected_input_start_sample: int
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: bool = ..., expected_input_start_sample: _Optional[int] = ...) -> None: ...

class GetCapabilitiesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetCapabilitiesResponse(_message.Message):
    __slots__ = ("capabilities", "max_active_streams")
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    MAX_ACTIVE_STREAMS_FIELD_NUMBER: _ClassVar[int]
    capabilities: StreamAccepted
    max_active_streams: int
    def __init__(self, capabilities: _Optional[_Union[StreamAccepted, _Mapping]] = ..., max_active_streams: _Optional[int] = ...) -> None: ...
