"""The single-Machine supervisor must never leave an orphan worker running."""

import asyncio
import os
import signal
import subprocess
import sys
import time

import pytest

from repoweave.runs.hosted_server import main, supervise


@pytest.mark.asyncio
async def test_child_failure_stops_sibling(tmp_path):
    ready, stopped = tmp_path / 'ready', tmp_path / 'stopped'
    sibling = [sys.executable, '-c', '''
import pathlib, signal, sys, time
def stop(*args):
    pathlib.Path(sys.argv[2]).touch()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
pathlib.Path(sys.argv[1]).touch()
while True: time.sleep(.01)
''', str(ready), str(stopped)]
    failure = [sys.executable, '-c', '''
import pathlib, sys, time
while not pathlib.Path(sys.argv[1]).exists(): time.sleep(.01)
raise SystemExit(2)
''', str(ready)]
    async with asyncio.timeout(5):
        assert await supervise([sibling, failure], grace_seconds=1) == 1
    assert stopped.exists()


@pytest.mark.asyncio
async def test_stubborn_child_is_killed(tmp_path):
    ready = tmp_path / 'pid'
    stubborn = [sys.executable, '-c', '''
import os, pathlib, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
while True: time.sleep(.01)
''', str(ready)]
    failure = [sys.executable, '-c', '''
import pathlib, sys, time
while not pathlib.Path(sys.argv[1]).exists(): time.sleep(.01)
''', str(ready)]
    async with asyncio.timeout(5):
        assert await supervise([stubborn, failure], grace_seconds=.1) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int(ready.read_text()), 0)


@pytest.mark.asyncio
async def test_spawn_failure_cleans_up_started_children():
    with pytest.raises(FileNotFoundError):
        await supervise([[sys.executable, '-c', 'import time; time.sleep(60)'],
                         ['/does-not-exist/repoweave']], grace_seconds=.1)


def test_invalid_configuration_fails_without_input_details(tmp_path, monkeypatch, capsys):
    config = tmp_path / 'hosting.json'
    config.write_text('{"sensitive": "never-display-this"}')
    monkeypatch.setenv('REPOWEAVE_HOSTING_CONFIG', str(config))
    assert main() == 1
    output = capsys.readouterr()
    assert output.err == 'Hosted configuration or storage is invalid\n'


def test_supervisor_sigterm_exits_cleanly(tmp_path):
    ready = tmp_path / 'pid'
    child = [sys.executable, '-c', '''
import os, pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(60)
''', str(ready)]
    command = [sys.executable, '-c',
               ('import asyncio, sys; from repoweave.runs.hosted_server import supervise; '
                f'sys.exit(asyncio.run(supervise([{child!r}], grace_seconds=1)))')]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        process.send_signal(signal.SIGTERM)
        _, error = process.communicate(timeout=5)
        assert process.returncode == 0, error.decode()
        with pytest.raises(ProcessLookupError):
            os.kill(int(ready.read_text()), 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
