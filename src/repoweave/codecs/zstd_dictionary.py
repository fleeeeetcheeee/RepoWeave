"""Zstd with one explicitly supplied, bounded trained dictionary."""

from collections.abc import Mapping

import zstandard

from . import zstd
from .base import CodecError, validate_options


def _dictionary(parameters: Mapping[str, int], assets: Mapping[str, bytes]):
    validate_options("zstd_dictionary", parameters, assets)
    try:
        dictionary = zstandard.ZstdCompressionDict(
            assets["dictionary"], dict_type=zstandard.DICT_TYPE_FULLDICT,
        )
        if not dictionary.dict_id():
            raise CodecError("Expected a trained Zstd dictionary")
        return dictionary
    except zstandard.ZstdError:
        raise CodecError("Invalid Zstd dictionary") from None


def encode(data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes]) -> bytes:
    return zstd._encode(data, parameters, _dictionary(parameters, assets))


def decode(
    data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes], expected_size: int,
) -> bytes:
    return zstd._decode(data, expected_size, _dictionary(parameters, assets))
