# Authenticated hosting test profile

Recommended first public test target: one Fly.io Machine and persistent volume,
using the app's `APP_NAME.fly.dev` HTTPS domain, with an Auth0 development tenant.
This is a modest test deployment, not a highly available production service.
Fly volumes attach to a single Machine and are not automatically replicated:
[Fly volumes](https://fly.io/docs/volumes/overview/). Do not scale this SQLite/local
artifact implementation to multiple Machines or run multiple HTTP processes.
Keep the HTTP process and the separate worker on that same Machine and volume.
A host failure needs a separately tested backup/restore procedure.

**Current verification:** real signed RSA tokens, real SQLite stores, local API/worker
operations and compression were tested with two users/projects. A Fly account/app,
public URL, Auth0 tenant, live login, TLS/proxy configuration and restart/backup tests
have not been supplied or tested. No API was published. Do not expose the default
`repoweave.api.app:app` as a public proxy target.

## Configuration

Create an operator-owned JSON file outside the checkout or under ignored `.repoweave/`:

```json
{
  "origin": "https://YOUR_APP.fly.dev",
  "issuer": "https://YOUR_TENANT.auth0.com/",
  "audience": "https://repoweave-api.example",
  "projects": {
    "alice-project": {
      "subjects": ["auth0|REPLACE_WITH_USER_SUB"],
      "fixtures": {"demo": "/app/examples/demo_repository"}
    },
    "bob-project": {
      "subjects": ["auth0|REPLACE_WITH_OTHER_USER_SUB"],
      "fixtures": {"demo": "/app/examples/demo_repository"}
    }
  }
}
```

The audience is the Auth0 Custom API Identifier, not its application's client ID.
Configure RS256 access tokens, the `repoweave:use` API scope, and short token lifetimes.
Use Auth0 Universal Login with Authorization Code + PKCE for a future browser client.
An ID token is not an API access token. The backend needs only the public issuer/JWKS
and audience to verify tokens; it needs no Auth0 signing key or client secret.
See [Auth0 access-token validation](https://auth0.com/docs/secure/tokens/access-tokens/validate-access-tokens).
Subject-to-project membership is configured by the operator; token-supplied project
or role claims cannot grant access. Members of one project share full control of its
jobs and policies. Removing membership requires restarting the configured processes.

Set backend process environment variables, without placing tokens in command arguments:

```dotenv
REPOWEAVE_HOSTING_CONFIG=/run/repoweave/hosting.json
REPOWEAVE_DATA_DIR=/data/repoweave
```

Run exactly one HTTP process and one worker under the hosting platform's process
supervisor, on the same persistent volume:

```bash
uv run --locked uvicorn repoweave.api.hosting:app --host 0.0.0.0 --port 8000 --no-access-log
uv run --locked python -m repoweave.runs.hosted_worker
```

The TLS reverse proxy must preserve the configured Host header, redirect HTTP to
HTTPS, bound headers/connections, and avoid logging Authorization headers. The API
checks the configured Host and browser Origin; forwarded identity headers are ignored.
The ASGI gateway never treats a client header as an authenticated identity.

Every protected request, including SSE and artifact downloads, requires:

- `Authorization: Bearer ACCESS_TOKEN` (never a query-string token).
- `X-RepoWeave-Project: PROJECT_ID`.

Only `/health` is unauthenticated. Each project receives its own store below
`REPOWEAVE_DATA_DIR/projects/PROJECT_ID`: repositories, jobs, dictionaries, events,
checkpoints, active policies and artifacts are all separate. An ID from another
project gives no access. Identical idempotency keys in different projects are independent.
Only the project's configured fixture roots can be registered; there is no arbitrary
upload/server-path endpoint. Do not mount the private datasets in the public test
service without separate authorization for that hosting provider. Current private
transfer approval covers Daytona only.

## Bounds and recovery

The gateway enforces a 64 KiB actual body limit, ten-second body timeout, 60 requests
per project per minute, four concurrent requests per project, eight concurrent JWT
verifications, eight unfinished jobs per project, at most three benchmark repetitions
and one warmup, and at most 300 seconds per encode/decode budget. SSE/download sessions
end at token expiry or five minutes; replay uses the existing event sequence IDs.
Sensitive responses are `Cache-Control: no-store`. JWT validation requires RS256,
signature, trusted issuer, intended audience, expiry, issued-at, subject and API scope.
JWKS failures deny access. Limits in memory assume one HTTP process.

The hosted worker rotates projects sequentially. It uses existing durable job recovery
and cancellation. Backend cloud execution and external traces are disabled by this
profile: a browser cannot authorize private transfers on behalf of the operator.
Factory reasoning still requires the existing backend configuration and installed Droid
CLI; only sanitized summaries are sent by the existing integration. A deployment image
must include those runtime requirements before offering optimization.

These controls do not provide OS isolation between processes, storage quotas, HA,
backups, immediate identity-provider token revocation, per-action roles, or a browser
login UI. Local measurements use the existing bounded child processes and RSS checks.
Adversarial load and a real Auth0/Fly deployment remain acceptance gates.
