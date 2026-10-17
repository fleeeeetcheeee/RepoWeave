# RPW archive format, version 1

This is the integration contract for DESIGN.md step 6.2. Header and manifest
validation are implemented in `repoweave.archive.manifest`. Payload decoding,
hash verification, filesystem extraction, and atomic publication remain work for
the archive reader/writer. A valid manifest alone does not verify an archive.

## Envelope

All integers in the header are unsigned, big-endian. No padding is present.

| Offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 8 | Magic: hexadecimal `52 50 57 45 41 56 45 00` (`RPWEAVE` followed by NUL) |
| 8 | 2 | Format version: `1` |
| 10 | 8 | Manifest byte length, excluding the header |
| 18 | manifest length | Canonical UTF-8 JSON manifest |
| 18 + manifest length | remainder | Encoded file payloads |

Python's header layout is `struct.Struct(">8sHQ")`. Readers must read exactly
18 bytes, reject unknown magic/version, and check the manifest length before
allocating or reading it. Reject truncated headers, manifests, or payloads and
all trailing bytes not described by the manifest. Never infer a codec or import
Python code from an archive field.

The empty archive has manifest `{"entries":[]}` (14 bytes), no payload, and header
hexadecimal `52505745415645000001000000000000000e`.

## Manifest

The only root field is `entries`, an array sorted by path using Unicode code-point
order. Every field shown below is required; unknown fields are rejected. No
timestamps, host paths, credentials, source bodies, or external references occur
in the manifest.

Directory entry:

```json
{"kind":"directory","path":"src"}
```

File entry (the SHA-256 of an empty file):

```json
{"codec":"stored","executable_bits":0,"kind":"file","length":0,"offset":0,"parameters":{},"path":"src/empty.py","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","size":0}
```

- `size`: original byte count, including zero for empty files.
- `sha256`: SHA-256 of original bytes as exactly 64 lowercase hexadecimal digits.
- `executable_bits`: integer containing only the original `mode & 0o111` bits;
  zero when unsupported. Other permission bits, owners, ACLs, and timestamps are
  outside V1's reconstruction scope. Readers never restore setuid/setgid bits.
- `codec`: exactly `stored`, `zstd`, or `lzma`.
- `parameters`: integer options only. `stored` requires `{}`; `zstd` accepts only
  optional `level` in -7 through 22 (default 3); `lzma` accepts only optional
  `preset` in 0 through 9 (default 6). These are the V1 supported encoding subset,
  even if a codec library accepts more options. Boolean values are not integers.
- `offset` and `length`: encoded byte range relative to the **payload section**,
  never relative to the beginning of the archive. Stored length equals size.

Stored payloads are raw bytes. Zstd payloads are one standard frame; LZMA
payloads are one XZ stream. No dictionaries, concatenated streams, external
assets, or cross-file decode dependencies are supported yet. Reject any such
extension until its schema, asset hashes, embedding, and compatibility rules
are specified. A frame requiring an unavailable dictionary must fail decoding.

File payloads occur contiguously in entry order, starting at offset zero.
Directories consume no payload. Empty stored files may share a boundary offset.
Overlaps, gaps, reordered ranges, and unreferenced trailing bytes are invalid.
The sum of lengths must equal the actual remaining archive size.

Canonical JSON is the UTF-8 encoding of Python `json.dumps(value,
ensure_ascii=False, sort_keys=True, separators=(",", ":"))` for this schema.
There is no BOM, whitespace outside strings, or terminal newline. JSON object
keys sort lexicographically; array order is preserved. Only strings, objects,
arrays, and integers occur. Duplicate keys, floats, booleans, null, invalid UTF-8,
and noncanonical alternate encodings are rejected. Manifest strings are metadata,
never executable code.

## Paths and scope

Paths are NFC Unicode relative paths using `/`. Root itself has no entry. Every
parent must have its own directory entry, so empty directories are preserved.
Reject absolute paths, empty components, `.`/`..`, backslashes, colons, ASCII
controls including DEL, and Windows-forbidden characters `< > " | ? *`.
Components cannot end in a dot or space or have a Windows device basename
(`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, including Windows'
superscript 1/2/3 forms), even with an extension or different casing.

Reject duplicate paths and collisions after NFC normalization of Unicode
casefolding. Parent spelling must match exactly. A file cannot be a parent.
This deliberately supports a portable subset of filenames; packing unsupported
names must fail clearly, never rename or silently omit them. Symlinks, hard-link
identity, special files, and filesystem metadata beyond declared scope are not
represented. Hard-linked regular files may be stored as independent files.

## V1 resource limits

| Resource | Maximum |
| --- | --- |
| Manifest bytes | 16 MiB |
| Entries, files plus directories | 100,000 |
| Original bytes per file | 64 MiB |
| Encoded bytes per file | 128 MiB |
| Total original bytes | 1 GiB |
| Total payload bytes | 2 GiB |
| UTF-8 path bytes | 1,024 |
| UTF-8 component bytes | 255 |
| Path components | 64 |

These are initial format limits, not a promise of constant-memory extraction.
Readers process one file at a time and may impose smaller local limits. Decoders
must enforce output limits during decompression, reject incomplete streams and
unused trailing input, check exact output size, and hash the actual decoded
bytes. Declared sizes alone are never sufficient. Codec memory/window limits
must also prevent small compressed inputs from requesting unbounded allocation.

## Reader and writer obligations

1. Validate header and manifest before any output. `parse_header` returns a
   bounded manifest length; `parse_manifest(..., payload_size=actual_length)`
   validates schema, canonical representation, paths, layout, and declared bounds.
2. Extract into a new operation-owned staging directory adjacent to the target.
   Do not follow symlinks in the destination or its ancestors; use filesystem
   operations that prevent traversal and races. Validate actual filesystem name
   collisions too: the portable name rules cannot model every filesystem.
3. Decode using an explicit codec registry, enforce runtime resource limits, and
   verify each file's actual size and SHA-256 before publication. On error,
   remove only this operation's staging files.
4. Publish the complete directory only after all files verify. Refuse an existing
   destination, including a dangling symlink, by default. Publication must not
   replace a destination created concurrently; unsupported platforms must fail
   safely rather than silently fall back to a replacing rename.
5. Write archives to an exclusive temporary sibling, flush and close, verify the
   result, then atomically publish without replacing an existing archive. Never
   modify source files or remove another operation's files on failure. Crash
   durability beyond atomic visibility requires an explicit fsync policy later.

## Integration with parallel work

The inventory/snapshot layer supplies supported relative paths, directory
entries, executable bits, and sizes/hashes of the bytes actually snapshotted.
Archive assembly must read those immutable bytes and translate inventory records
into this schema; it must not re-inventory the changing source tree.

The codec layer supplies the three explicit IDs and the byte-oriented contract
from DESIGN.md 6.1. Archive assembly chooses the codec, records parameters and
encoded lengths, and assigns offsets. If compressed output plus its incremental
metadata is larger than stored output, select stored. This decision belongs to
assembly because codecs do not know manifest overhead.

These fields and the restricted codec parameters are the proposed shared V1
contract; inventory and codec integration still need verification when those
implementations are available. No imports from their unfinished modules are
required to validate a manifest.
