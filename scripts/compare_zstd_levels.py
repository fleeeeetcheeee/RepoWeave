"""Same-level RPW and normalized whole-tree tar-zstd controls, with exact reconstruction."""

import argparse
import io
import os
import tarfile
import tempfile
import time
from pathlib import Path

import zstandard

from repoweave.archive.decode import unpack
from repoweave.archive.filesystem import open_regular
from repoweave.archive.integrity import check_bytes
from repoweave.archive.manifest import FileEntry
from repoweave.archive.writer import _snapshot_manifest, write_archive
from repoweave.benchmarks.worker import _compare, _tar_decode
from repoweave.repository import create_snapshot
from repoweave.routing.policy import write_json


def tar_zstd(snapshot, output, level):
    with (
        output.open('xb') as raw,
        zstandard.ZstdCompressor(level=level, write_checksum=True).stream_writer(
            raw, closefd=False) as compressed,
        tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as tar,
    ):
        for entry in _snapshot_manifest(snapshot).entries:
            info = tarfile.TarInfo(entry.path)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            if isinstance(entry, FileEntry):
                info.size, info.mode = entry.size, 0o600 | entry.executable_bits
                with open_regular(snapshot.files_root / entry.path) as source:
                    data = source.read(entry.size + 1)
                    check_bytes(data, entry.size, entry.sha256)
                    if os.fstat(source.fileno()).st_mode & 0o111 != entry.executable_bits:
                        raise ValueError('Snapshot executable bits changed')
                tar.addfile(info, io.BytesIO(data))
            else:
                info.type, info.mode = tarfile.DIRTYPE, 0o700
                tar.addfile(info)


def compare(source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    snapshot = create_snapshot(source, output / 'captures')
    results = []
    for level in (3, 9):
        for method in ('rpw-zstd', 'tar-zstd'):
            samples = []
            for repetition in range(3):
                with tempfile.TemporaryDirectory(dir=output) as directory:
                    work = Path(directory)
                    archive, restored = work / 'archive.bin', work / 'restored'
                    started = time.perf_counter()
                    if method == 'rpw-zstd':
                        write_archive(snapshot, archive, parameters={'level': level})
                    else:
                        tar_zstd(snapshot, archive, level)
                    encode_seconds = time.perf_counter() - started
                    started = time.perf_counter()
                    if method == 'rpw-zstd':
                        unpack(archive, restored)
                    else:
                        _tar_decode(snapshot, archive, restored, 'zstd')
                    _compare(snapshot, restored)
                    if repetition:
                        samples.append({'archive_bytes': archive.stat().st_size,
                                        'reconstructed': True, 'encode_seconds': encode_seconds,
                                        'decode_seconds': time.perf_counter() - started})
            assert samples[0]['archive_bytes'] == samples[1]['archive_bytes']
            results.append({'method': method, 'level': level, 'samples': samples})
    report = {'snapshot_id': snapshot.snapshot_id, 'repeats': 2, 'warmups': 1,
              'measurement': 'local sequential same process; no RSS eligibility claim',
              'results': results}
    write_json(output / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = compare(args.source, args.output)
    print([{'method': r['method'], 'level': r['level'], 'bytes': r['samples'][0]['archive_bytes']}
           for r in report['results']])
