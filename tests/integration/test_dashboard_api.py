"""Dashboard reads remain scoped, persisted, and free of server paths/secrets."""

import pytest
from fastapi.testclient import TestClient

from repoweave.api.app import create_app
from repoweave.config import Settings
from repoweave.runs.worker import run_once


@pytest.mark.asyncio
async def test_dashboard_snapshot_policy_and_verification(tmp_path):
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    (fixture / 'module.py').write_text('VALUE = 1\n' * 100)
    settings = Settings(REPOWEAVE_DATA_DIR=tmp_path / 'state', REPOWEAVE_FIXTURES={'demo': fixture})
    with TestClient(create_app(settings), base_url='http://127.0.0.1:8000',
                    client=('127.0.0.1', 12345)) as api:
        assert api.get('/fixtures').json()['fixtures'] == ['demo']
        saved = api.post('/repositories', json={'fixture': 'demo'}).json()
        assert api.get('/repositories').json() == [saved]
        assert api.get(f"/repositories/{saved['id']}").json() == saved
        policy = api.get(f"/repositories/{saved['id']}/policy").json()
        assert policy['policy']['codec'] == 'zstd' and not policy['promoted']
        run = api.post('/runs', headers={'Idempotency-Key': 'dashboard'}, json={
            'repository_id': saved['id'], 'operation': 'pack'}).json()
        assert await run_once(settings)
        result = api.get(f"/runs/{run['id']}").json()
        assert result['reports']['archive-verification']['reconstructed'] is True
        assert result['reports']['archive-verification']['archive_sha256'] == result['artifacts'][0]['sha256']
        assert str(tmp_path) not in str(result)
        assert api.get(f"/repositories/{'0' * 64}/policy").status_code == 404


def test_exact_local_frontend_cors_and_remote_denial(tmp_path):
    app = create_app(Settings(REPOWEAVE_DATA_DIR=tmp_path))
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 12345)) as api:
        origin = 'http://127.0.0.1:5173'
        response = api.options('/runs', headers={'Origin': origin,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type,idempotency-key'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == origin
        response = api.get('/fixtures', headers={'Origin': origin})
        assert response.status_code == 200
        for invalid in ('https://attacker.example', 'http://127.0.0.1:5174', 'null'):
            denied = api.get('/fixtures', headers={'Origin': invalid})
            assert denied.status_code == 403
            assert 'access-control-allow-origin' not in denied.headers
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('203.0.113.1', 12345)) as api:
        assert api.get('/fixtures', headers={'Origin': origin}).status_code == 403
