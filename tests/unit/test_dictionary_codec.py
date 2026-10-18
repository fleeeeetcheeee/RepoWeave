import pytest
import zstandard

from repoweave.codecs.base import CodecError
from repoweave.codecs.registry import get_codec


@pytest.mark.parametrize("data", [b"", b"a", b"function\r\n\x00\xff" * 1000], ids=["empty", "one", "large"])
def test_exact_dictionary_round_trip_and_corruption(dictionary: bytes, data: bytes) -> None:
    codec = get_codec("zstd_dictionary")
    assets = {"dictionary": dictionary}
    payload = codec.encode(data, {}, assets)
    assert codec.decode(payload, {}, assets, len(data)) == data
    assert codec.encode(data, {}, assets) == payload
    for bad in (payload[:-1], payload + b"junk", payload + payload,
                payload[:-1] + bytes([payload[-1] ^ 1])):
        with pytest.raises(CodecError):
            codec.decode(bad, {}, assets, len(data))
    with pytest.raises(CodecError):
        codec.decode(payload, {}, {}, len(data))


@pytest.mark.parametrize("asset", [b"bad", b"", b"x" * (1024 * 1024 + 1)],
                         ids=["invalid", "empty", "oversized"])
def test_invalid_assets(asset: bytes) -> None:
    with pytest.raises(CodecError):
        get_codec("zstd_dictionary").encode(b"hello", {}, {"dictionary": asset})


def test_dictionary_bomb_is_bounded(dictionary: bytes) -> None:
    payload = zstandard.ZstdCompressor(
        dict_data=zstandard.ZstdCompressionDict(dictionary), write_content_size=False,
    ).compress(b"x" * (1024 * 1024))
    for size in (0, 1, 1024):
        with pytest.raises(CodecError):
            get_codec("zstd_dictionary").decode(payload, {}, {"dictionary": dictionary}, size)
