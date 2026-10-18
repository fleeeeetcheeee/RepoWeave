# Daytona engine snapshot

The snapshot build contains the deterministic engine and a fixed experiment entrypoint.
It excludes configuration, API keys, agent/cloud dependencies, repositories, datasets,
and the optimization runner. The engine source allowlist also defines its revision ID.
Python is pinned by container digest; engine dependencies and wheel hashes come from
`uv.lock`. Builds run as an unprivileged user and check RPW, tar-zstd, tar-lzma, replay,
and independent byte-for-byte decoding using a generated synthetic fixture.

Prepare a new local build directory:

```bash
uv run --locked python scripts/prepare_engine_image.py .repoweave/daytona-engine-v1
```

Review `manifest.json` and the five files under `context/`. Only the wheel, requirements,
smoke test, and Dockerfile instructions are sent to Daytona. The SDK parses the three
explicit COPY sources; it never uploads the checkout. The generated context also has
an allowlist `.dockerignore` for conventional Docker builds. Do not build from the
repository root.

Creating a snapshot uploads this engine build to the configured Daytona organization.
Use a key with `write:snapshots`. Sandbox execution later needs `write:sandboxes` and
`delete:sandboxes`. Keep keys in local `.env`; do not put them in the image or commands.
See [Daytona permissions](https://www.daytona.io/docs/api-keys).

After authorizing that upload and resource allocation:

```bash
uv run --locked python scripts/create_daytona_snapshot.py .repoweave/daytona-engine-v1
uv run --locked python scripts/create_daytona_snapshot.py .repoweave/daytona-engine-v1 --status
```

This uses Daytona's remote Dockerfile builder, so local Docker and a separate registry
are unnecessary. It creates `repoweave-engine-v1` in `us`, with 2 CPUs, 4 GiB RAM,
10 GiB disk, no GPU, and `sleep infinity` as its entrypoint. It does not launch a
sandbox. Creation waits up to ten minutes; a client timeout does not cancel the remote
build. Check status before retrying. The script refuses an existing name and never
overwrites a snapshot. Use `--name` with a new version when the engine changes.

When status is `active`, select that name in Daytona's **Create Sandbox → Snapshot**
selector and set:

```dotenv
REPOWEAVE_DAYTONA_SNAPSHOT=repoweave-engine-v1
```

The **Create Snapshot → Image** field is for an existing container-registry reference;
it is unnecessary when this script builds and registers the snapshot directly.
`snapshot.json` records the resulting snapshot ID and any image reference returned
by Daytona. Remote Dockerfile builds can return an empty image reference; use the
snapshot name in that case. A snapshot name is not a Docker image name.

The build-time smoke check verifies the installed Linux engine. It does not validate
the complete optimization workflow or live sandbox lifecycle.

## Test an existing live sandbox

Create a sandbox from `repoweave-engine-v1` with 2 CPUs, 4 GiB RAM, 10 GiB disk,
and automatic deletion disabled. Then explicitly opt in using its name:

```bash
REPOWEAVE_LIVE_SANDBOX=repoweave-test-v1 uv run --locked pytest -m live tests/live/test_daytona.py -q -s
```

The test runs the installed synthetic smoke test with no repository uploads and no
credentials passed to the process. It checks exit status, engine revision, RPW and
tar reconstruction, replay, and a decoder-only directory in the same sandbox. Remote
execution is bounded to 180 seconds with a five-second termination grace period.
It then stops and preserves the named sandbox, verifies the stopped state, and saves
a report under `.repoweave/live-daytona/`. Restart the sandbox before another test.
Ephemeral sandboxes are rejected because stopping would delete them.

This tests an existing sandbox, not automatic provisioning, artifact transfer, or a
second clean decoder sandbox. It is excluded from the default test suite.
