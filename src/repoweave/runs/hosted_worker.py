"""One sequential worker, rotating through configured isolated project queues."""

import asyncio
import fcntl
import signal

from repoweave.api.hosting import load_hosting
from repoweave.config import load_settings
from repoweave.runs.worker import run_once


async def serve():
    config, settings = load_hosting(), load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    task = asyncio.current_task()
    assert task is not None
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    with (settings.data_dir / 'hosted-worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            worked = False
            for project in config.projects:
                worked = await run_once(config.settings(settings, project)) or worked
            if not worked:
                await asyncio.sleep(0.5)


if __name__ == '__main__':
    try:
        asyncio.run(serve())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
