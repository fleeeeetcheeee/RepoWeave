"""Explicitly approved financial CSV subsets; never log file contents or names."""

import os
from pathlib import Path

import pytest
from test_extended_cloud import cleanup_receipt, configuration

from repoweave.archive.filesystem import open_regular
from repoweave.execution.daytona import Remote
from repoweave.experiments.runner import Runner
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy
from repoweave.runs.service import submit
from repoweave.storage.database import Store


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize('label', ['VIETA', 'Portfolio'])
async def test_approved_private_csv_cloud(label):
    if os.environ.get('REPOWEAVE_PRIVATE_APPROVED') != 'VIETA,Portfolio:daytona':
        pytest.skip('Requires explicit approval for the two named private roots and Daytona')
    output, settings = configuration('private-' + label.lower())
    output.chmod(0o700)
    receipt = {'status': 'failed', 'dataset': label, 'scope': 'bounded complete CSV files only',
               'authorization': 'User approved local reads and Daytona transfer on 2026-10-18'}
    runner = None
    try:
        root = Path.home() / 'Documents' / label
        source = output / 'selected-data'
        source.mkdir(mode=0o700)
        count = total = 0
        for path in sorted(root.rglob('*.csv')):
            parts = path.relative_to(root).parts
            if (any(part.startswith('.') or part in {'node_modules', 'venv'} for part in parts)
                    or any(word in path.name.lower() for word in ('secret', 'credential', 'token', 'password'))
                    or any(parent.is_symlink() for parent in [path, *path.parents])):
                continue
            size = path.stat().st_size
            if not 0 < size <= 8 * 1024 * 1024 or total + size > 16 * 1024 * 1024:
                continue
            # Anonymous file names avoid sharing private directory/file labels with the service.
            with open_regular(path) as original, (source / f'{count:04d}.csv').open('xb') as target:
                remaining = size
                while remaining:
                    chunk = original.read(min(remaining, 1024 * 1024))
                    assert chunk
                    target.write(chunk)
                    remaining -= len(chunk)
                assert original.read(1) == b''
            count += 1
            total += size
            if count == 64:
                break
        assert count > 0
        receipt.update(files=count, original_bytes=total)
        snapshot = create_snapshot(source, output / 'captures')
        run_id = submit(source, Policy(), settings, repeats=2, warmups=1,
                        approved_snapshot=snapshot.snapshot_id,
                        limits=Limits(encode_seconds=120., decode_seconds=120.,
                                      peak_memory_bytes=1024 * 1024 * 1024))
        receipt['run_id'] = run_id
        store = Store(settings.data_dir)
        runner = Runner(store, run_id, settings, remote=Remote(store, run_id, settings))
        store.status(run_id, 'running')
        await runner.prepare()
        report = await runner.report(Policy(zstd_level=9))
        assert all(r['eligible'] and all(s['reconstructed'] for s in r['samples'])
                   for r in report['results'])
        assert all(len({s['archive_bytes'] for s in r['samples']}) == 1 for r in report['results'])
        await runner.independent_decode(report)
        await runner.close()
        store.status(run_id, 'completed-without-improvement')
        receipt.update(status='passed', benchmark=report, sandboxes=cleanup_receipt(store, run_id),
                       independent_decode_id=store.phase(run_id, 'independent-decode'))
    except Exception as error:  # noqa: BLE001 - private paths and SDK payloads stay out of logs
        receipt['error_type'] = type(error).__name__
        raise pytest.fail.Exception(f'Private test failed ({type(error).__name__}); see local receipt',
                                    pytrace=False) from None
    finally:
        if runner:
            try:
                await runner.close()
            except Exception:  # noqa: BLE001
                receipt['cleanup_pending'] = True
            receipt['sandboxes'] = {r: runner.store.sandbox(runner.run_id, r)
                                    for r in ('experiment', 'decoder')}
        # Local records remain in an ignored, owner-only directory for reproducibility.
        write_json(output / 'report.json', receipt)
        print(f'Private cloud receipt: {output / "report.json"}')
