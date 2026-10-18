"""One Zstandard frame with bounded window and output size."""

from collections.abc import Mapping

import zstandard

from .base import (
    MAX_FILE_BYTES,
    CodecError,
    validate_expected_size,
    validate_input,
    validate_options,
)


def encode(data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes]) -> bytes:
    validate_options("zstd", parameters, assets)
    return _encode(data, parameters)


def _encode(
    data: bytes, parameters: Mapping[str, int], dictionary: zstandard.ZstdCompressionDict | None = None,
) -> bytes:
    validate_input(data)
    try:
        result = zstandard.ZstdCompressor(
            level=parameters.get("level", 3), write_checksum=True, dict_data=dictionary,
        ).compress(data)
    except zstandard.ZstdError:
        raise CodecError("Zstd encoding failed") from None
    validate_input(result, encoded=True)
    return result


def decode(
    data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes], expected_size: int,
) -> bytes:
    validate_options("zstd", parameters, assets)
    return _decode(data, expected_size)


def _decode(
    data: bytes, expected_size: int, dictionary: zstandard.ZstdCompressionDict | None = None,
) -> bytes:
    validate_expected_size(expected_size)
    validate_input(data, encoded=True)
    if not data.startswith(b"\x28\xb5\x2f\xfd"):
        raise CodecError("Expected a standard Zstd frame")
    try:
        frame = zstandard.get_frame_parameters(data)
        if (frame.dict_id != (dictionary.dict_id() if dictionary else 0)
                or frame.window_size > MAX_FILE_BYTES):
            raise CodecError("Zstd frame requires an asset or excessive window memory")
        if frame.content_size not in (expected_size, zstandard.CONTENTSIZE_UNKNOWN):
            raise CodecError("Zstd content size does not match expected size")
        decoder = zstandard.ZstdDecompressor(max_window_size=MAX_FILE_BYTES, dict_data=dictionary)
        if frame.content_size == 0:
            # The one-shot API skips frame validation for declared-empty frames.
            with decoder.stream_reader(data, read_across_frames=True) as reader:
                if reader.read(1):
                    raise CodecError("Zstd content size does not match expected size")
            check = zstandard.ZstdDecompressor(
                max_window_size=MAX_FILE_BYTES, dict_data=dictionary,
            ).decompressobj()
            result = check.decompress(data)
            if not check.eof or check.unused_data:
                raise CodecError("Zstd frame is incomplete or has trailing data")
        else:
            result = decoder.decompress(
                data, max_output_size=max(expected_size, 1), allow_extra_data=False,
            )
    except zstandard.ZstdError:
        raise CodecError("Invalid or excessive Zstd frame") from None
    if len(result) != expected_size:
        raise CodecError("Decoded size does not match expected size")
    return result
