"""Opt-in OIDC gateway with physically separate project stores and bounded requests."""

import asyncio
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import jwt
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from repoweave.api.app import create_app
from repoweave.config import Settings, load_settings


class Project(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subjects: list[str] = Field(min_length=1, max_length=100)
    fixtures: dict[str, Path] = Field(default_factory=dict, max_length=20)


class Hosting(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    origin: str
    issuer: str
    audience: str = Field(min_length=1)
    projects: dict[str, Project] = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def validate_config(self):
        import re
        for value in (self.origin, self.issuer):
            url = urlsplit(value)
            if (url.scheme != 'https' or not url.hostname or url.username or url.password
                    or url.query or url.fragment or url.path not in ('', '/')):
                raise ValueError('Use an HTTPS origin and tenant issuer')
        if self.origin.endswith('/') or not self.issuer.endswith('/'):
            raise ValueError('Origin has no trailing slash; issuer requires one')
        if any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name) for name in self.projects):
            raise ValueError('Invalid project identifier')
        if len({name.casefold() for name in self.projects}) != len(self.projects):
            raise ValueError('Project names must not collide on case-insensitive filesystems')
        return self

    def settings(self, base: Settings, project: str) -> Settings:
        # Policy promotion, idempotency, events and dictionaries are isolated too.
        root = base.data_dir / 'projects' / project
        if root.resolve() != root.absolute():
            raise ValueError('Project storage must not use symlinks')
        return base.model_copy(update={
            'data_dir': root,
            'fixtures': self.projects[project].fixtures,
            'execution': 'local', 'allow_remote_data': False, 'langsmith_tracing': False,
            'max_candidates': min(base.max_candidates, 3),
        })


def load_hosting() -> Hosting:
    try:
        with Path(os.environ['REPOWEAVE_HOSTING_CONFIG']).open('rb') as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError
        return Hosting.model_validate_json(data)
    except (KeyError, OSError, ValueError):
        raise ValueError('A valid REPOWEAVE_HOSTING_CONFIG is required') from None


class Verifier:
    def __init__(self, config: Hosting):
        self.config = config
        self.keys = jwt.PyJWKClient(config.issuer + '.well-known/jwks.json', timeout=5)

    def __call__(self, token: str) -> dict:
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
            raise jwt.InvalidTokenError
        key = self.keys.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=['RS256'], audience=self.config.audience,
                            issuer=self.config.issuer,
                            options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
        if not isinstance(claims.get('scope'), str) or 'repoweave:use' not in claims['scope'].split():
            raise jwt.InvalidTokenError
        if type(claims['exp']) is not int or type(claims['iat']) is not int:
            raise jwt.InvalidTokenError
        return claims


def create_hosted_app(config: Hosting | None = None, settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app):
        selected = config or load_hosting()
        base = settings or load_settings()
        app.state.config = selected
        app.state.verifier = Verifier(selected)
        app.state.projects = {}
        async with AsyncExitStack() as stack:
            for name in selected.projects:
                project_settings = selected.settings(base, name)
                project_settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                child = create_app(project_settings, public_origin=selected.origin, max_pending=8)
                await stack.enter_async_context(child.router.lifespan_context(child))
                app.state.projects[name] = child
            yield

    shell = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    return Gateway(shell)


class Gateway:
    def __init__(self, app):
        self.app = app
        self.active: dict[str, int] = {}
        self.windows: dict[str, tuple[float, int]] = {}
        self.authenticating = 0

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.app(scope, receive, send)
        if scope['type'] != 'http':
            return await send({'type': 'websocket.close', 'code': 1008})

        async def reject(status, detail):
            await JSONResponse({'detail': detail}, status_code=status)(scope, receive, send)

        config = self.app.state.config
        headers = {}
        for name, value in scope['headers']:
            key = name.decode('latin-1').lower()
            if key in headers and key in {'authorization', 'host', 'x-repoweave-project', 'origin'}:
                return await reject(400, 'Duplicate security header')
            headers[key] = value.decode('latin-1')
        if (headers.get('host') != urlsplit(config.origin).netloc
                or headers.get('origin', config.origin) != config.origin
                or headers.get('sec-fetch-site') == 'cross-site'):
            return await reject(403, 'Origin not allowed')
        if scope['path'] == '/health' and scope['method'] == 'GET':
            return await JSONResponse({'status': 'ready'})(scope, receive, send)
        authorization = headers.get('authorization', '')
        if not authorization.startswith('Bearer ') or len(authorization) > 8192:
            return await reject(401, 'Valid bearer access token required')
        if self.authenticating >= 8:
            return await reject(429, 'Authentication busy')
        self.authenticating += 1
        try:
            claims = await run_in_threadpool(self.app.state.verifier, authorization[7:])
        except (jwt.PyJWTError, ValueError, OSError):
            return await reject(401, 'Valid bearer access token required')
        finally:
            self.authenticating -= 1
        project = headers.get('x-repoweave-project', '')
        if project not in config.projects or claims['sub'] not in config.projects[project].subjects:
            return await reject(404, 'Not found')
        now = time.monotonic()
        start, count = self.windows.get(project, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        if count >= 60 or self.active.get(project, 0) >= 4:
            return await reject(429, 'Project request limit reached')
        self.windows[project] = (start, count + 1)
        self.active[project] = self.active.get(project, 0) + 1
        try:
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            return
                        body.extend(message.get('body', b''))
                        if len(body) > 65536:
                            return await reject(413, 'Request exceeds byte limit')
                        if not message.get('more_body'):
                            break
            except TimeoutError:
                return await reject(408, 'Request body timed out')
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()

            async def no_cache(message):
                if message['type'] == 'http.response.start':
                    message['headers'] = [(k, v) for k, v in message['headers']
                                          if k.lower() != b'cache-control'] + [
                                              (b'cache-control', b'no-store'),
                                              (b'x-content-type-options', b'nosniff')]
                await send(message)

            # Long SSE/downloads release capacity at token expiry or after five minutes.
            try:
                async with asyncio.timeout(min(300, max(0, claims['exp'] - time.time()))):
                    await self.app.state.projects[project](
                        {**scope, 'repoweave.authenticated': True}, bounded_receive, no_cache)
            except TimeoutError:
                return
        finally:
            self.active[project] -= 1


app = create_hosted_app()
