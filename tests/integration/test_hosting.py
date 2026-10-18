"""Real signed access tokens, separate stores, real engine; no external identity service."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from repoweave.api.hosting import Hosting, create_hosted_app
from repoweave.config import Settings
from repoweave.runs.worker import run_once


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient, 'get_signing_key_from_jwt',
                        lambda self, token: SimpleNamespace(key=private.public_key()))
    roots = {}
    for name in ('alice', 'bob'):
        root = tmp_path / name
        root.mkdir()
        (root / 'sample.py').write_bytes((f'# {name} owned fixture\n'.encode()) * 300)
        roots[name] = root
    config = Hosting(origin='https://test.fly.dev', issuer='https://tenant.auth0.com/',
                     audience='https://repoweave.test', projects={
                         name: {'subjects': [name], 'fixtures': {'owned': root}}
                         for name, root in roots.items()})
    settings = Settings(REPOWEAVE_DATA_DIR=tmp_path / 'state')

    def token(subject='alice', **updates):
        claims = {'iss': config.issuer, 'aud': config.audience, 'sub': subject,
                  'iat': int(time.time()), 'exp': int(time.time()) + 600,
                  'scope': 'repoweave:use'}
        claims.update(updates)
        return jwt.encode(claims, private, algorithm='RS256', headers={'kid': 'test'})

    def headers(subject='alice', project=None, **updates):
        return {'Authorization': 'Bearer ' + token(subject, **updates),
                'X-RepoWeave-Project': project or subject}

    gateway = create_hosted_app(config, settings)
    with TestClient(gateway, base_url=config.origin, client=('203.0.113.1', 40000)) as api:
        yield api, config, settings, token, headers


def register(api, headers):
    response = api.post('/repositories', headers=headers, json={'fixture': 'owned'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def submit(api, headers, repository, key='shared-key'):
    return api.post('/runs', headers={**headers, 'Idempotency-Key': key}, json={
        'repository_id': repository, 'operation': 'pack',
        'limits': {'encode_seconds': 120.0, 'decode_seconds': 120.0},
    })


def test_token_signature_claims_membership_and_origin(hosted):
    api, _, _, token, headers = hosted
    assert api.get('/health').status_code == 200
    assert api.get('/runs').status_code == 401
    for updates in ({'exp': 1}, {'iss': 'https://attacker.test/'}, {'aud': 'wrong'},
                    {'scope': ''}, {'iat': int(time.time()) + 1000}):
        response = api.get('/runs', headers=headers(**updates))
        assert response.status_code == 401
        assert token() not in response.text
    signed = token()
    parts = signed.split('.')
    parts[1] = parts[1][:-2] + 'aa'
    assert api.get('/runs', headers={'Authorization': 'Bearer ' + '.'.join(parts),
                                    'X-RepoWeave-Project': 'alice'}).status_code == 401
    assert api.get('/runs', headers=headers('outsider', 'alice')).status_code == 404
    assert api.get('/runs', headers=headers('alice', 'bob')).status_code == 404
    assert api.get('/runs', headers={**headers(), 'Origin': 'https://attacker.test'}).status_code == 403
    assert api.get('/runs', headers={**headers(), 'Host': 'attacker.test'}).status_code == 403
    assert api.get('/runs', headers={**headers(), 'X-Forwarded-User': 'bob'}).json() == []
    assert api.get('/runs', headers=headers()).headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_project_run_event_artifact_and_idempotency_isolation(hosted):
    api, config, settings, _, headers = hosted
    alice, bob = headers(), headers('bob')
    repo_a = register(api, alice)
    job_a = submit(api, alice, repo_a).json()['id']
    assert api.get('/runs', headers=bob).json() == []
    assert api.get(f'/repositories/{repo_a}/graph', headers=bob).status_code == 404
    assert submit(api, bob, repo_a).status_code == 404
    for path in (f'/runs/{job_a}', f'/runs/{job_a}/events?follow=false'):
        assert api.get(path, headers=bob).status_code == 404
    for action in ('cancel', 'resume', 'approve'):
        assert api.post(f'/runs/{job_a}/{action}', headers=bob).status_code == 404
    assert await run_once(config.settings(settings, 'alice'))
    item = api.get(f'/runs/{job_a}', headers=alice).json()
    assert item['status'] == 'completed'
    artifact = item['artifacts'][0]['id']
    assert api.get(f'/artifacts/{artifact}', headers=bob).status_code == 404
    assert api.get(f'/artifacts/{artifact}', headers=alice).status_code == 200
    repo_b = register(api, bob)
    job_b = submit(api, bob, repo_b).json()['id']
    # Same idempotency key is scoped by separate stores, even when IDs coincide.
    assert job_b == job_a
    assert api.get(f'/runs/{job_b}', headers=bob).json()['repository_id'] == repo_b
    assert await run_once(config.settings(settings, 'bob'))
    assert api.get(f'/runs/{job_a}', headers=alice).json() == item
    with TestClient(create_hosted_app(config, settings), base_url=config.origin) as restarted:
        assert restarted.get(f'/runs/{job_a}', headers=alice).json() == item
        assert restarted.get(f'/artifacts/{artifact}', headers=bob).status_code == 404


def test_body_queue_and_rate_limits(hosted):
    api, _, _, _, headers = hosted
    auth = headers()
    assert api.post('/repositories', headers=auth, content=b'x' * 65537).status_code == 413
    repository = register(api, auth)
    for index in range(8):
        assert submit(api, auth, repository, str(index)).status_code == 202
    assert submit(api, auth, repository, 'overflow').status_code == 429
    assert submit(api, auth, repository, '0').status_code == 202
    statuses = [api.get('/runs', headers=auth).status_code for _ in range(60)]
    assert 429 in statuses
    assert api.get('/runs', headers=headers('bob')).status_code == 200


@pytest.mark.asyncio
async def test_jwks_failure_and_stream_connection_limits(hosted, monkeypatch):
    api, _, _, _, headers = hosted
    gateway = api.app
    gateway.active['alice'] = 4
    assert api.get('/runs', headers=headers()).status_code == 429
    gateway.active['alice'] = 0
    monkeypatch.setattr(jwt.PyJWKClient, 'get_signing_key_from_jwt',
                        lambda *args: (_ for _ in ()).throw(jwt.PyJWKClientError('hidden-sentinel')))
    response = api.get('/runs', headers=headers())
    assert response.status_code == 401 and 'hidden-sentinel' not in response.text


def test_hosting_config_fails_closed(tmp_path):
    for values in ({'origin': 'http://insecure.test'}, {'issuer': 'https://tenant.test/path'},
                   {'projects': {'../escape': {'subjects': ['alice']}}},
                   {'projects': {'Alice': {'subjects': ['alice']},
                                 'alice': {'subjects': ['bob']}}}):
        with pytest.raises(ValueError):
            Hosting.model_validate({'origin': 'https://test.fly.dev', 'issuer': 'https://tenant.test/',
                                    'audience': 'api', 'projects': {'safe': {'subjects': ['alice']}},
                                    **values})
    destination = tmp_path / 'outside'
    destination.mkdir()
    (tmp_path / 'projects').symlink_to(destination, target_is_directory=True)
    config = Hosting(origin='https://test.fly.dev', issuer='https://tenant.test/',
                     audience='api', projects={'alice': {'subjects': ['alice']}})
    with pytest.raises(ValueError):
        config.settings(Settings(REPOWEAVE_DATA_DIR=tmp_path), 'alice')
    assert list(destination.iterdir()) == []


@pytest.mark.asyncio
async def test_separate_hosted_worker_serves_both_projects(hosted, tmp_path):
    import asyncio
    import sys

    api, config, settings, _, headers = hosted
    jobs = {name: submit(api, headers(name), register(api, headers(name))).json()['id']
            for name in ('alice', 'bob')}
    path = tmp_path / 'hosting.json'
    path.write_text(config.model_dump_json())
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'repoweave.runs.hosted_worker', cwd=tmp_path,
        env={'REPOWEAVE_HOSTING_CONFIG': str(path), 'REPOWEAVE_DATA_DIR': str(settings.data_dir)},
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(15):
            while not all(api.get(f'/runs/{identity}', headers=headers(name)).json()['status'] == 'completed'
                          for name, identity in jobs.items()):
                assert process.returncode is None
                await asyncio.sleep(0.5)
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)
    assert process.returncode == 0
