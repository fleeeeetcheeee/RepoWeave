"""Uncompressed bytes."""

from collections.abc import Mapping

from .base import CodecError, validate_expected_size, validate_input, validate_options


def encode(data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes]) -> bytes:
    validate_options("stored", parameters, assets)
    validate_input(data)
    return data


def decode(
    data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes], expected_size: int,
) -> bytes:
    validate_options("stored", parameters, assets)
    validate_expected_size(expected_size)
    validate_input(data, encoded=True)
    if len(data) != expected_size:
        raise CodecError("Decoded size does not match expected size")
    return data
