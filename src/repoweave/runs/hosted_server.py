"""Supervise the API and worker on one Machine sharing one persistent volume."""

import asyncio
import os
import signal
import sys

from repoweave.api.hosting import load_hosting
from repoweave.config import load_settings


async def supervise(commands: list[list[str]], *, grace_seconds: float = 20) -> int:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    children: list[asyncio.subprocess.Process] = []
    waits: list[asyncio.Task] = []
    stop = asyncio.create_task(stopped.wait())
    try:
        for command in commands:
            children.append(await asyncio.create_subprocess_exec(*command, start_new_session=True))
        waits = [asyncio.create_task(child.wait()) for child in children]
        await asyncio.wait([stop, *waits], return_when=asyncio.FIRST_COMPLETED)
        # An unexpected clean child exit is still a service failure.
        return 0 if stopped.is_set() else 1
    finally:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            async with asyncio.timeout(grace_seconds):
                await asyncio.gather(*(child.wait() for child in children))
        except TimeoutError:
            pass
        finally:
            # Also reap descendants left behind by a failed parent process.
            for child in children:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.gather(*(child.wait() for child in children))
            stop.cancel()
            await asyncio.gather(stop, *waits, return_exceptions=True)
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)


def main() -> int:
    try:
        config, settings = load_hosting(), load_settings()
        for project in config.projects:
            selected = config.settings(settings, project)
            selected.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if any(not root.is_dir() for root in selected.fixtures.values()):
                raise ValueError
    except (ValueError, OSError):
        print('Hosted configuration or storage is invalid', file=sys.stderr)
        return 1
    return asyncio.run(supervise([
        [sys.executable, '-m', 'uvicorn', 'repoweave.api.hosting:app',
         '--host', '0.0.0.0', '--port', '8000', '--workers', '1', '--no-access-log',
         '--no-proxy-headers', '--timeout-graceful-shutdown', '15'],
        [sys.executable, '-m', 'repoweave.runs.hosted_worker'],
    ]))


if __name__ == '__main__':
    raise SystemExit(main())
