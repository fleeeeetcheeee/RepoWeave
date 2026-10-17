# V1 codecs

`repoweave.codecs.registry.get_codec(id)` returns an implementation of the typed
`Codec` protocol in `codecs/base.py`:

```python
codec.encode(data: bytes, parameters, assets) -> bytes
codec.decode(data: bytes, parameters, assets, expected_size: int) -> bytes
```

Parameters are a mapping of option names to integers; assets are a mapping of
IDs to bytes. V1 requires empty assets. Registry IDs are exactly `stored`, `zstd`,
and `lzma`. Unsupported IDs/options/assets and malformed payloads raise
`CodecError`; no codec dynamically imports code named by an archive.

| Codec | Parameters | Representation |
| --- | --- | --- |
| stored | `{}` | Original bytes |
| zstd | Optional `level`, -7 through 22; default 3 | One standard Zstd frame |
| lzma | Optional `preset`, 0 through 9; default 6 | One XZ stream |

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
Inventory and pack/unpack integration are not part of this codec implementation.
