"""Owned demo fixtures and separate API/worker processes; no repository code execution."""

import argparse
import asyncio
import fcntl
import signal
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import uvicorn

from repoweave.api.app import create_app
from repoweave.config import Settings, load_settings
from repoweave.runs.worker import run_once, serve


def prepare(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'fixture.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare(root)


def _prepare(root: Path) -> Path:
    fixture = root / 'owned-fixture'
    fixture.mkdir(parents=True, exist_ok=True)
    if not (fixture / 'module_0.py').exists():
        for index in range(8):
            text = (f'from module_{(index + 1) % 8} import VALUE\n'
                    f'VALUE = {index}\n' + '# Owned lossless demonstration fixture.\n' * 300)
            (fixture / f'module_{index}.py').write_text(text)
        (fixture / 'README.md').write_text('Owned synthetic fixture; import graph contains a cycle.\n')
        (fixture / 'binary.bin').write_bytes(bytes(range(256)) * 4)
        (fixture / 'unicode-λ.txt').write_text('Byte-for-byte reconstruction: λ\n')
        (fixture / 'empty').mkdir()
        (fixture / 'build').mkdir()
        (fixture / 'build' / 'ignored.txt').write_text('An owned excluded build output.\n')
        (fixture / 'module_0.py').chmod(0o755)
    return fixture


async def test_worker(settings: Settings) -> None:
    # Doubles are reachable only from this explicitly labeled test harness.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests' / 'integration'))
    from test_optimization import Agents, Cloud

    from repoweave.execution.daytona import Remote
    from repoweave.runs import service

    cloud = Cloud(settings.data_dir / 'fake-cloud')
    service.Remote = lambda store, identity, config: Remote(  # type: ignore[misc]
        store, identity, config, client=cloud)
    task = asyncio.current_task()
    assert task is not None
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    while True:
        if not await run_once(settings, agents=Agents()):
            await asyncio.sleep(.1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'api', 'worker'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--port', default=8000, type=int)
    parser.add_argument('--live', action='store_true', help='Enable owned fixture transfers to Daytona')
    parser.add_argument('--test', action='store_true', help='TEST ONLY: fake agent and cloud boundaries')
    args = parser.parse_args()
    root = args.root.resolve()
    fixture = prepare(root)
    if args.mode == 'prepare':
        return
    if args.live and args.test:
        parser.error('Live and test modes are mutually exclusive')
    settings = (Settings(DAYTONA_API_KEY='fake', REPOWEAVE_DAYTONA_SNAPSHOT='engine')
                if args.test else load_settings(Path(__file__).resolve().parents[1] / '.env'))
    settings = settings.model_copy(update={
        'data_dir': root / 'state', 'fixtures': {'demo': fixture},
        'execution': 'local', 'allow_remote_data': args.live or args.test,
        'langsmith_tracing': False, 'max_candidates': 1,
    })
    if args.mode == 'api':
        # The mocked browser suite must not require an installed/authenticated Droid CLI.
        with patch('repoweave.api.app.shutil.which', return_value='mock-droid') if args.test else nullcontext():
            uvicorn.run(create_app(settings), host='127.0.0.1', port=args.port,
                        access_log=False, proxy_headers=False)
    else:
        print('worker-ready', flush=True)
        try:
            asyncio.run(test_worker(settings) if args.test else serve(settings))
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass


if __name__ == '__main__':
    main()
