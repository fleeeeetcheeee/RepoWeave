"""Local API; durable work is executed only by repoweave.runs.worker."""

import asyncio
import hashlib
import ipaddress
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from repoweave.api.schemas import Registration, Submission
from repoweave.archive.filesystem import open_regular
from repoweave.config import Settings, load_settings
from repoweave.experiments.schemas import MAX_ARTIFACT_BYTES, Identity
from repoweave.repository import create_snapshot
from repoweave.repository_graph import build_graph
from repoweave.runs import service
from repoweave.runs.queue import TERMINAL, Queue, QueueFull
from repoweave.storage.database import Store


def create_app(settings: Settings | None = None, *, public_origin: str | None = None,
               max_pending: int | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configured = settings or load_settings()
        app.state.settings = configured
        app.state.queue = Queue(Store(configured.data_dir), max_pending=max_pending)
        app.state.fixtures = {name: path.absolute() for name, path in configured.fixtures.items()}
        yield

    app = FastAPI(title="RepoWeave local API", lifespan=lifespan)
    if public_origin is None:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"],
                           www_redirect=False)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if public_origin is not None:
            if request.scope.get("repoweave.authenticated") is not True:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            return await call_next(request)
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        origin = request.headers.get("origin")
        if (not local or request.headers.get("sec-fetch-site") == "cross-site"
                or (origin is not None and origin != str(request.base_url).rstrip("/"))):
            return JSONResponse({"detail": "Local same-origin access required"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(LookupError)
    async def missing(request, error):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def conflict(request, error):
        return JSONResponse({"detail": "Invalid request or conflicting run state"}, status_code=409)

    @app.exception_handler(OSError)
    async def unavailable(request, error):
        return JSONResponse({"detail": "Storage or worker unavailable"}, status_code=409)

    @app.exception_handler(QueueFull)
    async def full(request, error):
        return JSONResponse({"detail": "Project pending-job limit reached"}, status_code=429)

    def queue() -> Queue:
        return app.state.queue

    def view(identity: str) -> dict:
        row = queue().get(identity)
        return {"id": identity, "status": row["status"], "cancel_requested": bool(row["cancel"]),
                "operation": row["request"]["operation"],
                "repository_id": row["request"]["repository_id"],
                "artifacts": queue().downloads(identity)}

    @app.get("/health")
    def health():
        with queue().store.connect() as db:
            db.execute("SELECT 1")
        return {"status": "ready"}

    @app.post("/repositories", status_code=201)
    def register(request: Registration):
        root = app.state.fixtures.get(request.fixture)
        if root is None:
            raise LookupError("Unknown fixture")
        snapshot = create_snapshot(root, queue().store.root / "registered",
                                   exclude_paths=(queue().store.root,))
        identity = queue().register(snapshot, build_graph(snapshot).to_report())
        return {"id": identity, "snapshot": snapshot.to_report()}

    @app.get("/repositories/{identity}/graph")
    def repository_graph(identity: Identity):
        return queue().store.read_artifact(queue().repository(identity)["graph_id"])

    @app.post("/runs", status_code=202)
    def submit(request: Submission,
               idempotency_key: Annotated[str, Header(min_length=1, max_length=128,
                                                     pattern=r"^[a-zA-Z0-9_.:-]+$")]):
        if public_origin is not None and (
                request.repeats > 3 or request.warmups > 1
                or request.limits.encode_seconds > 300 or request.limits.decode_seconds > 300):
            raise ValueError("Hosted experiment exceeds budget")
        queue().repository(request.repository_id)
        service.registered_bundles(
            queue().store.root / "dictionaries", request.policy, tuple(request.dictionary_ids),
            request.repository_id, request.splits, request.partition_repository_id,
        )
        if request.execution == "daytona":
            app.state.settings.validate_daytona(
                dataset_id=request.repository_id,
                approved_dataset_ids=[request.approved_snapshot],
            )
        identity = queue().submit(idempotency_key, request.model_dump())
        return view(identity)

    @app.get("/runs")
    def runs():
        with queue().store.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM submissions ORDER BY rowid")]
        return [view(identity) for identity in ids]

    @app.get("/runs/{identity}")
    def run(identity: Identity):
        result = view(identity)
        # Metrics are available through registered, hash-verified report artifacts.
        for artifact in result["artifacts"]:
            if artifact["kind"] in {"benchmark", "decision"}:
                item = queue().download(artifact["id"])
                path = queue().store.root / item["path"]
                from repoweave.experiments.schemas import file_hash
                if file_hash(path) != item["sha256"]:
                    raise ValueError("Artifact changed")
                with open_regular(path) as stream:
                    result["metrics"] = json.loads(stream.read(MAX_ARTIFACT_BYTES + 1))
        return result

    @app.get("/runs/{identity}/events")
    async def events(identity: Identity, request: Request,
                     after: Annotated[int, Query(ge=0)] = 0,
                     last_event_id: Annotated[int | None, Header(ge=0)] = None,
                     follow: bool = True):
        queue().get(identity)

        async def stream():
            cursor = max(after, last_event_id or 0)
            while True:
                # Read status before events so the terminal transition cannot be missed.
                status = queue().get(identity)["status"]
                with queue().store.connect() as db:
                    rows = db.execute("SELECT sequence,kind,data FROM events WHERE run_id=? "
                                      "AND sequence>? ORDER BY sequence LIMIT 100",
                                      (identity, cursor)).fetchall()
                for row in rows:
                    cursor = row["sequence"]
                    payload = json.dumps({"kind": row["kind"], "data": json.loads(row["data"])})
                    yield f"id: {cursor}\nevent: progress\ndata: {payload}\n\n"
                if len(rows) == 100:
                    continue
                if not follow or status in TERMINAL | {"awaiting-approval", "interrupted"}:
                    return
                if await request.is_disconnected():
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/runs/{identity}/approve")
    async def approve(identity: Identity):
        if queue().get(identity)["status"] != "awaiting-approval":
            raise ValueError("Not awaiting approval")
        await service.approve(queue().store, identity)
        return view(identity)

    @app.post("/runs/{identity}/resume", status_code=202)
    def resume(identity: Identity):
        queue().resume(identity)
        return view(identity)

    @app.post("/runs/{identity}/cancel", status_code=202)
    def cancel(identity: Identity):
        queue().cancel(identity)
        return view(identity)

    @app.get("/artifacts/{identity}")
    def download(identity: Identity):
        record = queue().download(identity)
        path = queue().store.root / record["path"]
        if Path(record["path"]).is_absolute() or ".." in Path(record["path"]).parts:
            raise LookupError("Invalid artifact")
        opened = open_regular(path)
        stream = opened.__enter__()
        try:
            digest, size = hashlib.sha256(), 0
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES:
                    raise ValueError("Artifact exceeds limit")
                digest.update(chunk)
            if size != record["size"] or digest.hexdigest() != record["sha256"]:
                raise ValueError("Artifact changed")
            stream.seek(0)
        except BaseException:
            opened.__exit__(None, None, None)
            raise

        def chunks():
            try:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
            finally:
                stream.close()
        return StreamingResponse(chunks(), media_type="application/octet-stream",
                                 headers={"Content-Length": str(size),
                                          "Content-Disposition": f'attachment; filename="{identity}"'},
                                 background=BackgroundTask(opened.__exit__, None, None, None))

    @app.get("/workflow")
    def workflow():
        from repoweave.workflows.improve_compression.graph import build_graph as workflow_graph
        return workflow_graph().get_graph().to_json()

    return app


app = create_app()
