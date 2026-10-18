# Initial Fly.io + Auth0 setup

Use an Auth0 **Single Page Application** for the React/Vite dashboard specified
in DESIGN.md step 14. If Python onboarding offers only Regular Web App and
Machine to Machine, leave that wizard and open Applications → Applications →
Create Application. Regular Web App is for server-side login/session handling;
M2M is for background clients with no human user. RepoWeave's Python backend is
registered separately as an **API**. It verifies access tokens, not ID tokens.
[Auth0 application settings](https://auth0.com/docs/get-started/applications/application-settings).

The repository currently has an authenticated API, not a browser login UI or
dashboard. Creating the registrations below does not implement that frontend.
Callback URLs below reserve the root route for its Authorization Code + PKCE flow.

## 1. Auth0 development tenant

In Applications → APIs → Create API:

| Field | Value |
| --- | --- |
| Name | RepoWeave API |
| Identifier / audience | `https://repoweave-api` |
| Signing algorithm | RS256 |
| Permission | `repoweave:use` — Use an assigned RepoWeave project |
| Access token lifetime | 900 seconds (also the browser-flow lifetime) |
| User-delegated access policy | Per-app authorization; authorize only RepoWeave Dashboard |

The identifier is a stable audience string, not a website you must purchase.
Do not use the Auth0 Management API audience. For the initial first-party test,
leave RBAC off: request `repoweave:use` explicitly, and let RepoWeave's configured
subject membership gate project access. If enabling RBAC later, assign this API
permission to each test user; an unassigned scope is omitted from tokens.
In the API's application access settings, explicitly authorize the Dashboard SPA
for user-delegated access to `repoweave:use`. New tenants can default to per-app
authorization, so merely creating an API permission is not sufficient.
[Auth0 API settings](https://auth0.com/docs/get-started/apis/api-settings).

Create **RepoWeave Dashboard**, type **Single Page Web Application**:

| Setting | Value |
| --- | --- |
| Allowed Callback URLs | `https://YOUR_APP.fly.dev/` |
| Allowed Logout URLs | `https://YOUR_APP.fly.dev/` |
| Allowed Web Origins | `https://YOUR_APP.fly.dev` |
| Token Endpoint Authentication Method | None (public SPA; no client secret) |
| Flow | Authorization Code + PKCE, Universal Login |
| Requested scopes | `openid profile email repoweave:use` |
| Audience in authorization request | `https://repoweave-api` |

Use exact URLs, no wildcards. The current hosted gateway requires the frontend
and API to share this origin. Do not add localhost origins and assume the hosted
API supports them. Local frontend development needs a same-origin development
proxy or a separately designed/tested CORS profile.
Leave redirect/origin fields empty until the Fly hostname is reserved in your
account; a proposed but unowned `fly.dev` name is not a safe callback destination.

Keep the tenant domain and client ID (both public configuration). No Auth0 client
secret or signing key belongs in the API. Under User Management → Users, obtain
each application test user's actual `user_id` after signup or account creation.
Your Auth0 dashboard administrator is not automatically an application user.
Assign the exact IDs in `hosting.json`; never grant access based only on email.
The application must request an access token with the audience and scope above.

## 2. Local deployment configuration

Install/login to the official Fly CLI. This setup installed it locally at
`.repoweave/tools/fly/bin/flyctl`; ordinary installations use `fly` on PATH.
Use the explicit path below, or substitute your installed `fly` command.

```sh
.repoweave/tools/fly/bin/flyctl auth login
mkdir -p .repoweave/hosting
cp -n deploy/fly.toml.example .repoweave/hosting/fly.toml
```

Replace `YOUR_APP` in the copied TOML, including the health-check Host header.
Create `.repoweave/hosting/hosting.json` from the example in [hosting.md](hosting.md),
using the actual Auth0 issuer (including trailing slash), the audience above,
and actual test-user subjects. Use the owned fixture
`/app/examples/demo_repository`. Separate subjects into separate projects for
isolation testing. These ignored files are operator configuration, not API secrets.

The prepared defaults are one shared CPU, 2 GB RAM, `iad`, one 1 GB persistent
volume, HTTPS, and no automatic stop while durable jobs can be running. The
API and worker are supervised together with one HTTP process; either child's
failure stops its sibling. Shutdown signals reach both process groups. An
initial root entrypoint gives the dedicated data directory to the unprivileged
application user; application processes run as UID 1000.

The Docker build uses `uv.lock` without dependency upgrades. Its explicit build
allowlist includes application source, deployment runtime files and three owned
demo files. It excludes `.env`, `.git`, tests, private datasets and `.repoweave`.
Never replace it with `COPY . .` or send an unrestricted context to a remote builder.
The existing engine-only Docker ignore file is unchanged.

## 3. Provision and deploy after account/budget approval

These commands create billable resources and publish the authenticated service.
Choose the app name once; its `fly.dev` hostname includes free TLS. Run from the
repository root. Keep account terms/payment entry in the user's own browser.

```sh
.repoweave/tools/fly/bin/flyctl apps create YOUR_APP --org YOUR_ORG
.repoweave/tools/fly/bin/flyctl volumes create repoweave_data --app YOUR_APP --region iad --size 1
.repoweave/tools/fly/bin/flyctl ips allocate-v6 --app YOUR_APP
.repoweave/tools/fly/bin/flyctl ips allocate-v4 --shared --app YOUR_APP
.repoweave/tools/fly/bin/flyctl config validate --strict --config .repoweave/hosting/fly.toml
.repoweave/tools/fly/bin/flyctl deploy --config .repoweave/hosting/fly.toml --remote-only --ha=false --strategy immediate
.repoweave/tools/fly/bin/flyctl status --app YOUR_APP
.repoweave/tools/fly/bin/flyctl checks list --app YOUR_APP
curl --fail https://YOUR_APP.fly.dev/health
```

`--ha=false` prevents creating a second Machine. An immediate single-Machine
deployment has downtime; SQLite and artifacts stay on its attached volume.
Do not define separate Fly process groups for API and worker: that creates separate
Machines with separate disks. Do not scale this profile horizontally.
[Fly configuration](https://fly.io/docs/reference/configuration/),
[Fly volumes](https://fly.io/docs/volumes/overview/).

Factory optimization requires a separately installed Droid CLI and a backend
credential; this first image does not include them. Compression, graph analysis,
archives and local benchmarks need no service credential. Daytona transfers and
LangSmith tracing remain disabled in this hosted profile. Do not import the
developer's `.env` into Fly secrets wholesale.

Fly bills for running compute, provisioned volumes and outbound traffic. Check
the current [pricing](https://fly.io/docs/about/pricing/) before provisioning.
Stopping a Machine does not remove volume/rootfs charges. Autostop is intentionally
disabled so pending work continues after a browser disconnects. Shut down the test
Machine deliberately when finished, keeping the volume if its results are needed.

## 4. Live acceptance gates

With two real Auth0 user access tokens, check successful authorized requests and
denial of missing/expired/wrong-audience tokens. Submit one fixture pack per project,
download and independently decode each result, and verify neither user can access
the other's run, events, actions or artifact. Restart the Machine and repeat reads
to confirm persistence. Tokens must stay out of shell history, query strings and
logs. Finally test volume backup/restore and bounded load before wider exposure.

Prepared files and local tests are not evidence of a successful public deployment,
a real Auth0 login, or a container image build. Record each completed live gate
explicitly in implementation-status.md.
