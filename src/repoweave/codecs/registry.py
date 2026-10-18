"""Explicit codec lookup and storage selection; no dynamic imports."""

from collections.abc import Mapping
from types import MappingProxyType

from . import lzma, stored, zstd, zstd_dictionary
from .base import Codec, CodecError

CODECS: Mapping[str, Codec] = MappingProxyType({
    "stored": stored, "zstd": zstd, "lzma": lzma, "zstd_dictionary": zstd_dictionary,
})


def get_codec(codec_id: str) -> Codec:
    try:
        return CODECS[codec_id]
    except KeyError:
        raise CodecError("Unsupported codec") from None


def encode_with_fallback(
    codec_id: str, data: bytes, parameters: Mapping[str, int], assets: Mapping[str, bytes],
    *, metadata_overhead: int,
) -> tuple[str, bytes]:
    """Return selected ID/payload; assembly supplies compressed-minus-stored metadata bytes.

    When stored wins (including ties), its manifest parameters must be {}.
    """
    if type(metadata_overhead) is not int:
        raise CodecError("Metadata overhead must be an integer byte count")
    payload = get_codec(codec_id).encode(data, parameters, assets)
    if codec_id != "stored" and len(payload) + metadata_overhead >= len(data):
        return "stored", stored.encode(data, {}, {})
    return codec_id, payload
