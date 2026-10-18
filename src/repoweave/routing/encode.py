"""Resolve encoding policy assets before passing explicit bytes to the archive writer."""

from pathlib import Path

from repoweave.archive.manifest import ArchiveFormatError, Manifest
from repoweave.archive.writer import write_archive
from repoweave.dictionaries.train import load_bundle
from repoweave.repository import Snapshot

from .schemas import Policy


def encode_policy(snapshot: Snapshot, output: Path, policy: Policy, registry: Path) -> Manifest:
    dictionaries: dict[str, bytes] = {}
    if policy.dictionary_id is not None:
        bundle = load_bundle(registry, policy.dictionary_id)
        if bundle.grouping != policy.grouping:
            raise ArchiveFormatError("Policy and dictionary grouping differ")
        dictionaries, _ = bundle.for_snapshot(snapshot)
    return write_archive(snapshot, output, codec_id=policy.codec, parameters=policy.parameters,
                         dictionaries=dictionaries, solid_block_bytes=policy.solid_block_bytes,
                         metadata_encoding=policy.metadata_encoding)
