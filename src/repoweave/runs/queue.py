"""Durable API submissions and explicitly published downloads in the existing ledger."""

import json
from pathlib import Path

from repoweave.experiments.schemas import file_hash
from repoweave.routing.policy import content_id
from repoweave.storage.database import Store

TERMINAL = {"completed", "promoted", "completed-without-improvement", "failed", "cancelled"}


class QueueFull(ValueError):
    """The configured project's bounded backlog is full."""


class Queue:
    def __init__(self, store: Store, *, max_pending: int | None = None):
        self.store = store
        self.max_pending = max_pending
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS repositories (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, report_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS submissions (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
                    cancel INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, path TEXT NOT NULL,
                    sha256 TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL);
            """)

    def repository(self, identity: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM repositories WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise LookupError("Unknown repository")
        return dict(row)

    def register(self, snapshot, graph: dict) -> str:
        report_id = self.store.artifact(snapshot.to_report())
        graph_id = self.store.artifact(graph)
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO repositories VALUES (?,?,?,?)", (
                snapshot.snapshot_id, str(snapshot.files_root.relative_to(self.store.root)),
                report_id, graph_id,
            ))
        return snapshot.snapshot_id

    def submit(self, key: str, request: dict) -> str:
        self.repository(request["repository_id"])
        identity = content_id({"api_idempotency_key": key})
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT request FROM submissions WHERE id=?", (identity,)).fetchone()
            if row:
                if json.loads(row[0]) != request:
                    raise ValueError("Idempotency key belongs to another request")
                return identity
            if self.max_pending is not None:
                pending = db.execute("SELECT COUNT(*) FROM submissions WHERE status IN "
                                     "('queued','running','awaiting-approval','interrupted')").fetchone()[0]
                if pending >= self.max_pending:
                    raise QueueFull("Pending-job limit reached")
            db.execute("INSERT INTO submissions(id,request,status) VALUES (?,?,?)",
                       (identity, json.dumps(request), "queued"))
            self._event(db, identity, "queued")
        return identity

    @staticmethod
    def _event(db, identity: str, status: str) -> None:
        db.execute("INSERT INTO events(run_id,kind,data) VALUES (?,?,?)",
                   (identity, "status", json.dumps({"status": status})))

    def get(self, identity: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM submissions WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise LookupError("Unknown submission")
        return {**dict(row), "request": json.loads(row["request"])}

    def take(self) -> dict | None:
        # Caller holds worker.lock for the entire execution, including crash recovery.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM submissions WHERE status IN ('queued','running') "
                             "ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE submissions SET status='running' WHERE id=?", (row[0],))
            self._event(db, row[0], "running")
        return self.get(row[0])

    def finish(self, identity: str, status: str) -> None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT cancel FROM submissions WHERE id=?", (identity,)).fetchone()
            if row[0] and status not in {"promoted", "failed"}:
                status = "cancelled"
            db.execute("UPDATE submissions SET status=? WHERE id=?", (status, identity))
            self._event(db, identity, status)

    def cancel(self, identity: str) -> None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM submissions WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise LookupError("Unknown submission")
            if row[0] in TERMINAL:
                raise ValueError("Run is already terminal")
            # Promotion's CAS uses this same database transaction boundary.
            if db.execute("SELECT 1 FROM promotions WHERE run_id=?", (identity,)).fetchone():
                raise ValueError("Run has already promoted")
            status = "running" if row[0] == "running" else "cancelled"
            db.execute("UPDATE submissions SET cancel=1,status=? WHERE id=?", (status, identity))
            db.execute("UPDATE runs SET cancel=1 WHERE id=?", (identity,))
            self._event(db, identity, "cancel-requested" if status == "running" else status)

    def resume(self, identity: str) -> None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,cancel FROM submissions WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise LookupError("Unknown submission")
            if row[1] or row[0] not in {"interrupted", "awaiting-approval"}:
                raise ValueError("Run is not resumable")
            if row[0] == "awaiting-approval":
                approved = db.execute("SELECT approved FROM runs WHERE id=?", (identity,)).fetchone()
                if not approved or not approved[0]:
                    raise ValueError("Explicit approval is required")
            db.execute("UPDATE submissions SET status='queued' WHERE id=?", (identity,))
            self._event(db, identity, "queued")

    def publish(self, identity: str, path: Path, kind: str) -> str:
        relative = str(path.relative_to(self.store.root))
        digest, size = file_hash(path), path.stat().st_size
        artifact = content_id({"run_id": identity, "sha256": digest, "kind": kind})
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO downloads VALUES (?,?,?,?,?,?)",
                       (artifact, identity, relative, digest, size, kind))
        return artifact

    def downloads(self, identity: str) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,sha256,size,kind FROM downloads WHERE run_id=? ORDER BY kind,id", (identity,))]

    def download(self, identity: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM downloads WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise LookupError("Unknown artifact")
        if self.get(row["run_id"])["status"] not in TERMINAL | {"awaiting-approval"}:
            raise LookupError("Artifact is not available")
        return dict(row)
