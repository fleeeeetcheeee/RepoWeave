"""One XZ stream with bounded decoder memory and output."""

import lzma
from collections.abc import Mapping

from .base import (
    MAX_DECODER_MEMORY,
    CodecError,
    validate_expected_size,
    validate_input,
    validate_options,
)


def encode(data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes]) -> bytes:
    validate_options("lzma", parameters, assets)
    validate_input(data)
    try:
        result = lzma.compress(data, format=lzma.FORMAT_XZ, preset=parameters.get("preset", 6))
    except lzma.LZMAError:
        raise CodecError("LZMA encoding failed") from None
    validate_input(result, encoded=True)
    return result


def decode(
    data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes], expected_size: int,
) -> bytes:
    validate_options("lzma", parameters, assets)
    validate_expected_size(expected_size)
    validate_input(data, encoded=True)
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=MAX_DECODER_MEMORY)
        result = decoder.decompress(data, max_length=expected_size + 1)
    except lzma.LZMAError:
        raise CodecError("Invalid or excessive LZMA stream") from None
    if len(result) != expected_size or not decoder.eof or decoder.unused_data:
        raise CodecError("LZMA stream is incomplete, has trailing data, or has the wrong size")
    return result
