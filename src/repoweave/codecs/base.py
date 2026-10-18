"""Shared V1 codec contract, limits, and option validation."""

from collections.abc import Mapping
from typing import Protocol

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ENCODED_FILE_BYTES = 2 * MAX_FILE_BYTES
MAX_DECODER_MEMORY = 128 * 1024 * 1024
MAX_DICTIONARY_BYTES = 1024 * 1024

PARAMETER_BOUNDS = {
    "stored": {}, "zstd": {"level": (-7, 22)}, "lzma": {"preset": (0, 9)},
    "zstd_dictionary": {"level": (-7, 22)},
}


class CodecError(ValueError):
    """Invalid codec input, options, or encoded data."""


class Codec(Protocol):
    def encode(
        self, data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes],
    ) -> bytes: ...

    def decode(
        self, data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes],
        expected_size: int,
    ) -> bytes: ...


def validate_options(
    codec_id: str, parameters: Mapping[str, int], assets: Mapping[str, bytes],
) -> None:
    if codec_id == "zstd_dictionary":
        if (set(assets) != {"dictionary"} or not isinstance(assets["dictionary"], bytes)
                or not 0 < len(assets["dictionary"]) <= MAX_DICTIONARY_BYTES):
            raise CodecError("A bounded dictionary asset is required")
    elif assets:
        raise CodecError("V1 codecs do not support assets")
    if codec_id not in PARAMETER_BOUNDS:
        raise CodecError("Unsupported codec")
    for name, value in parameters.items():
        bounds = PARAMETER_BOUNDS[codec_id].get(name)
        if bounds is None or type(value) is not int or not bounds[0] <= value <= bounds[1]:
            raise CodecError("Unsupported codec parameter")


def validate_input(data: bytes, *, encoded: bool = False) -> None:
    limit = MAX_ENCODED_FILE_BYTES if encoded else MAX_FILE_BYTES
    if not isinstance(data, bytes) or len(data) > limit:
        raise CodecError("Codec input must be bytes within the size limit")


def validate_expected_size(expected_size: int) -> None:
    if type(expected_size) is not int or not 0 <= expected_size <= MAX_FILE_BYTES:
        raise CodecError("Invalid expected size")
