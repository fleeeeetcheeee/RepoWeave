import lzma
import random
import subprocess
import sys

import pytest
import zstandard

from repoweave.codecs import base
from repoweave.codecs.base import CodecError, validate_options
from repoweave.codecs.registry import CODECS, encode_with_fallback, get_codec

CODEC_IDS = ("stored", "zstd", "lzma")
SAMPLES = [b"", b"a", b"repeated source line\n" * 4096, bytes(range(256)) * 8,
           random.Random(42).randbytes(4096), "日本語 café\n".encode()]


@pytest.mark.parametrize("codec_id", CODEC_IDS)
@pytest.mark.parametrize("data", SAMPLES)
def test_exact_round_trip(codec_id: str, data: bytes) -> None:
    codec = get_codec(codec_id)
    payload = codec.encode(data, {}, {})
    assert codec.decode(payload, {}, {}, len(data)) == data
    assert codec.encode(data, {}, {}) == payload


@pytest.mark.parametrize(("codec_id", "parameters"), [
    ("stored", {}), ("zstd", {"level": -7}), ("zstd", {"level": 5}),
    ("lzma", {"preset": 0}), ("lzma", {"preset": 6}),
])
def test_supported_options(codec_id: str, parameters: dict[str, int]) -> None:
    data = b"configuration round trip" * 32
    codec = get_codec(codec_id)
    payload = codec.encode(data, parameters, {})
    assert codec.decode(payload, parameters, {}, len(data)) == data


@pytest.mark.parametrize("codec_id", CODEC_IDS)
@pytest.mark.parametrize("expected", [-1, True, 1.0, "1", base.MAX_FILE_BYTES + 1, 0, 2])
def test_invalid_expected_sizes(codec_id: str, expected: int) -> None:
    codec = get_codec(codec_id)
    payload = codec.encode(b"a", {}, {})
    with pytest.raises(CodecError):
        codec.decode(payload, {}, {}, expected)


@pytest.mark.parametrize(("codec_id", "parameters"), [
    ("stored", {"level": 1}), ("zstd", {"level": -8}), ("zstd", {"level": 23}),
    ("zstd", {"level": True}), ("zstd", {"level": "3"}), ("zstd", {"level": 3.0}),
    ("lzma", {"preset": -1}), ("lzma", {"preset": 10}), ("lzma", {"preset": True}),
    ("lzma", {"filters": 1}),
])
def test_invalid_options_on_encode_and_decode(codec_id: str, parameters: dict) -> None:
    codec = get_codec(codec_id)
    with pytest.raises(CodecError):
        codec.encode(b"", parameters, {})
    with pytest.raises(CodecError):
        codec.decode(b"", parameters, {}, 0)


@pytest.mark.parametrize("codec_id", CODEC_IDS)
def test_assets_are_not_supported(codec_id: str) -> None:
    codec = get_codec(codec_id)
    with pytest.raises(CodecError, match="assets"):
        codec.encode(b"", {}, {"dictionary": b"asset"})
    with pytest.raises(CodecError, match="assets"):
        codec.decode(b"", {}, {"dictionary": b"asset"}, 0)


@pytest.mark.parametrize("codec_id", CODEC_IDS)
def test_byte_limits_before_codec_work(codec_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "MAX_FILE_BYTES", 8)
    monkeypatch.setattr(base, "MAX_ENCODED_FILE_BYTES", 16)
    codec = get_codec(codec_id)
    with pytest.raises(CodecError):
        codec.encode(b"x" * 9, {}, {})
    with pytest.raises(CodecError):
        codec.decode(b"x" * 17, {}, {}, 8)
    with pytest.raises(CodecError):
        codec.encode(bytearray(b"x"), {}, {})


@pytest.mark.parametrize("codec_id", ["zstd", "lzma"])
@pytest.mark.parametrize("data", [b"", b"not empty" * 100])
def test_truncation_trailing_frames_and_corrupt_checksums(codec_id: str, data: bytes) -> None:
    codec = get_codec(codec_id)
    payload = codec.encode(data, {}, {})
    corruptions = [payload[:i] for i in range(len(payload))]
    corruptions += [payload + b"junk", payload + payload,
                    payload[:-1] + bytes([payload[-1] ^ 1])]
    for damaged in corruptions:
        with pytest.raises(CodecError):
            codec.decode(damaged, {}, {}, len(data))


@pytest.mark.parametrize("codec_id", ["zstd", "lzma"])
def test_expansion_beyond_claimed_output_limit(codec_id: str) -> None:
    data = b"x" * (1024 * 1024)
    # Omit Zstd's size so the decoder must enforce the limit during decompression.
    payload = (zstandard.ZstdCompressor(write_content_size=False).compress(data)
               if codec_id == "zstd" else lzma.compress(data))
    for size in (0, 1, 1024):
        with pytest.raises(CodecError):
            get_codec(codec_id).decode(payload, {}, {}, size)


@pytest.mark.parametrize("data", [b"", b"hello" * 256])
def test_zstd_unknown_content_size(data: bytes) -> None:
    payload = zstandard.ZstdCompressor(write_content_size=False).compress(data)
    assert get_codec("zstd").decode(payload, {}, {}, len(data)) == data


def test_zstd_rejects_dictionary_frame_and_skippable_frame() -> None:
    dictionary_frame = b"\x28\xb5\x2f\xfd\x21\x07\x00\x01\x00\x00"
    assert zstandard.get_frame_parameters(dictionary_frame).dict_id == 7
    for payload in (dictionary_frame, b"\x50\x2a\x4d\x18\x00\x00\x00\x00"):
        with pytest.raises(CodecError):
            get_codec("zstd").decode(payload, {}, {}, 0)


def test_decoder_memory_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from repoweave.codecs import lzma as lzma_codec
    from repoweave.codecs import zstd as zstd_codec

    data = b"x" * 4096
    zstd_payload = zstd_codec.encode(data, {}, {})
    lzma_payload = lzma_codec.encode(data, {}, {})
    monkeypatch.setattr(zstd_codec, "MAX_FILE_BYTES", 1024)
    monkeypatch.setattr(lzma_codec, "MAX_DECODER_MEMORY", 1024)
    with pytest.raises(CodecError):
        zstd_codec.decode(zstd_payload, {}, {}, len(data))
    with pytest.raises(CodecError):
        lzma_codec.decode(lzma_payload, {}, {}, len(data))


def test_lzma_rejects_legacy_container() -> None:
    payload = lzma.compress(b"hello", format=lzma.FORMAT_ALONE)
    with pytest.raises(CodecError):
        get_codec("lzma").decode(payload, {}, {}, 5)


def test_registry_rejects_unregistered_identifiers() -> None:
    assert set(CODECS) == {*CODEC_IDS, "zstd_dictionary"}
    for name in ("", "os.system", "repoweave.codecs.zstd", "neural"):
        with pytest.raises(CodecError):
            get_codec(name)
        with pytest.raises(CodecError):
            validate_options(name, {}, {})


@pytest.mark.parametrize("codec_id", ["zstd", "lzma"])
def test_stored_fallback_counts_incremental_metadata(codec_id: str) -> None:
    data = b"repetition" * 1000
    compressed = get_codec(codec_id).encode(data, {}, {})
    assert encode_with_fallback(codec_id, data, {}, {}, metadata_overhead=0) == (
        codec_id, compressed,
    )
    for overhead in (len(data) - len(compressed), len(data)):
        assert encode_with_fallback(codec_id, data, {}, {}, metadata_overhead=overhead) == (
            "stored", data,
        )
    assert encode_with_fallback(codec_id, b"x", {}, {}, metadata_overhead=0) == ("stored", b"x")


def test_fallback_does_not_hide_invalid_configuration() -> None:
    with pytest.raises(CodecError):
        encode_with_fallback("zstd", b"x", {"level": 99}, {}, metadata_overhead=0)
    with pytest.raises(CodecError):
        encode_with_fallback("zstd", b"x", {}, {}, metadata_overhead=True)


def test_clean_process_does_not_load_application_or_cloud_modules(tmp_path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from repoweave.codecs.registry import CODECS
for name in ('stored', 'zstd', 'lzma'):
    codec = CODECS[name]
    assert codec.decode(codec.encode(b'hello', {}, {}), {}, {}, 5) == b'hello'
assert not any(name.startswith(('repoweave.config', 'repoweave.agents',
    'repoweave.workflows', 'daytona', 'langsmith', 'langgraph', 'dotenv')) for name in sys.modules)
"""], cwd=tmp_path, env={}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
