# Local codecs

`repoweave.codecs.registry.get_codec(id)` returns an implementation of the typed
`Codec` protocol in `codecs/base.py`:

```python
codec.encode(data: bytes, parameters, assets) -> bytes
codec.decode(data: bytes, parameters, assets, expected_size: int) -> bytes
```

Parameters are a mapping of option names to integers; assets are a mapping of
IDs to bytes. V1 requires empty assets. Registry IDs are exactly `stored`, `zstd`,
and `lzma`; V2 adds `zstd_dictionary`. Unsupported IDs/options/assets and malformed payloads raise
`CodecError`; no codec dynamically imports code named by an archive.

| Codec | Parameters | Representation |
| --- | --- | --- |
| stored | `{}` | Original bytes |
| zstd | Optional `level`, -7 through 22; default 3 | One standard Zstd frame |
| lzma | Optional `preset`, 0 through 9; default 6 | One XZ stream |
| zstd_dictionary | Optional `level`, -7 through 22; default 3 | One Zstd frame with a trained dictionary |

`zstd_dictionary` requires exactly `{"dictionary": dictionary_bytes}` in its asset map,
bounded to 1 MiB. It checks the trained dictionary format/ID and uses the same frame,
window, output, checksum, and trailing-data checks as plain zstd. Archive V2 maps a
content-hashed embedded asset to this codec input; dictionary training and policy
selection are outside the decoder. Existing V1 codecs still reject all assets.

Encode accepts at most 64 MiB. Decode accepts at most 128 MiB of encoded bytes
and requires an exact expected size between zero and 64 MiB. Decoders reject
truncation, trailing bytes, concatenated streams, and excess output. Zstd windows
are capped at 64 MiB; LZMA decoder working memory is capped at 128 MiB. These
limits do not include caller-owned input/output buffers or impose an encoder
memory/time budget; high compression settings still require experiment limits.

The implementation uses the installed packages' bounded decoding APIs:
[python-zstandard](https://python-zstandard.readthedocs.io/en/latest/decompressor.html)
and [Python's LZMADecompressor](https://docs.python.org/3.12/library/lzma.html#lzma.LZMADecompressor).
Zstd's declared-empty frames receive additional streaming and frame-completion
checks because the installed one-shot API returns early for these frames.

`encode_with_fallback(id, data, parameters, assets, metadata_overhead=...)` returns
the selected `(codec_id, payload)`. Archive assembly must supply the exact
compressed-entry metadata byte count minus the stored-entry metadata byte count
(a signed integer). Stored wins if payload plus this difference is no smaller
than original bytes. If stored wins, write `{}` as the entry's parameters.
Invalid codec options remain errors; fallback does not mask codec failures.

The archive layer remains responsible for canonical metadata sizing, full-archive
limits, SHA-256 verification, safe paths, and publication. Corruption that still
forms valid bytes, especially stored bytes, requires that archive hash check.
Inventory and pack/unpack integration live in the repository and archive layers.

RPW V3 solid blocks reuse the existing stored and zstd implementations on bounded
concatenated file bytes. `solid` is an archive slice reference, not a new registry
codec. Each block is independent, following [Zstd's frame independence](https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md).
The archive layer validates slice coverage and checks each reconstructed file hash;
codec limits apply unchanged to each decoded block.

V4 also permits LZMA solid blocks using the same XZ implementation and 128 MiB
decoder memory ceiling. Its metadata envelope uses stored or Zstd independently
of the payload codec, bounded to 16 MiB decoded metadata. Binary compaction is a
manifest representation, not a dynamically registered codec. No decoder assets
or dependencies were added.
