"""Small transactional run ledger. Each operation owns its SQLite connection."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from repoweave.routing.policy import content_id, write_json


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runs.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, status TEXT NOT NULL,
                    cancel INTEGER NOT NULL DEFAULT 0, approved INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    run_id TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL,
                    result_id TEXT, PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS phases (
                    run_id TEXT NOT NULL, phase TEXT NOT NULL, artifact_id TEXT NOT NULL,
                    PRIMARY KEY(run_id,phase));
                CREATE TABLE IF NOT EXISTS active (scope TEXT PRIMARY KEY, policy_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS promotions (run_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sandboxes (
                    run_id TEXT NOT NULL, role TEXT NOT NULL, name TEXT NOT NULL UNIQUE,
                    sandbox_id TEXT, status TEXT NOT NULL, PRIMARY KEY(run_id,role));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_run(self, run_id: str, spec: dict) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT spec FROM runs WHERE id=?", (run_id,)).fetchone()
            if existing:
                if json.loads(existing[0]) != spec:
                    raise ValueError("Run ID already belongs to another request")
                return False
            db.execute("INSERT OR IGNORE INTO active VALUES (?,?)", (spec["scope_id"], spec["parent_id"]))
            active = db.execute("SELECT policy_id FROM active WHERE scope=?", (spec["scope_id"],)).fetchone()[0]
            if active != spec["parent_id"]:
                raise ValueError("Requested parent differs from the active policy")
            db.execute("INSERT INTO runs(id,spec,status) VALUES (?,?,?)",
                       (run_id, json.dumps(spec), "queued"))
        self.event(run_id, "queued")
        return True

    def run(self, run_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown run ID")
        return {**dict(row), "spec": json.loads(row["spec"])}

    def runs(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,status,cancel,approved FROM runs ORDER BY rowid")]

    def status(self, run_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        self.event(run_id, "status", {"status": status})

    def event(self, run_id: str, kind: str, data: dict | None = None) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO events(run_id,kind,data) VALUES (?,?,?)",
                       (run_id, kind, json.dumps(data or {})))

    def events(self, run_id: str) -> list[dict]:
        with self.connect() as db:
            return [{**dict(row), "data": json.loads(row["data"])} for row in
                    db.execute("SELECT * FROM events WHERE run_id=? ORDER BY sequence", (run_id,))]

    def cancel(self, run_id: str) -> None:
        self.run(run_id)
        with self.connect() as db:
            db.execute("UPDATE runs SET cancel=1 WHERE id=?", (run_id,))
        self.event(run_id, "cancel-requested")

    def approve(self, run_id: str) -> None:
        with self.connect() as db:
            updated = db.execute("UPDATE runs SET approved=1 WHERE id=? AND status='awaiting-approval' AND cancel=0",
                                 (run_id,))
            if updated.rowcount != 1:
                raise ValueError("Run is not awaiting approval")
        self.event(run_id, "approved")

    def promote(self, run_id: str, candidate_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or not row["approved"] or row["cancel"]:
                raise ValueError("Promotion requires uncancelled persisted approval")
            previous = db.execute("SELECT policy_id FROM promotions WHERE run_id=?", (run_id,)).fetchone()
            if previous:
                if previous[0] != candidate_id:
                    raise ValueError("Run already promoted another policy")
                return
            spec = json.loads(row["spec"])
            changed = db.execute("UPDATE active SET policy_id=? WHERE scope=? AND policy_id=?",
                                 (candidate_id, spec["scope_id"], spec["parent_id"]))
            if changed.rowcount != 1:
                raise ValueError("Active policy changed; refusing stale promotion")
            db.execute("INSERT INTO promotions VALUES (?,?)", (run_id, candidate_id))
            db.execute("INSERT INTO events(run_id,kind,data) VALUES (?,?,?)",
                       (run_id, "promoted", json.dumps({"policy_id": candidate_id})))

    def artifact(self, value: dict) -> str:
        identity = content_id(value)
        path = self.root / "artifacts" / f"{identity}.json"
        if not path.exists():
            write_json(path, value)
        elif self.read_artifact(identity) != value:
            raise ValueError("Artifact identity collision")
        return identity

    def read_artifact(self, identity: str) -> dict:
        if len(identity) != 64 or any(char not in "0123456789abcdef" for char in identity):
            raise ValueError("Invalid artifact identity")
        from repoweave.archive.filesystem import open_regular

        with open_regular(self.root / "artifacts" / f"{identity}.json") as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("Artifact exceeds byte limit")
        value = json.loads(data)
        if not isinstance(value, dict) or content_id(value) != identity:
            raise ValueError("Artifact identity mismatch")
        return value

    def phase(self, run_id: str, phase: str, value: dict | None = None) -> str | None:
        with self.connect() as db:
            if value is not None:
                identity = self.artifact(value)
                db.execute("INSERT OR IGNORE INTO phases VALUES (?,?,?)", (run_id, phase, identity))
            row = db.execute("SELECT artifact_id FROM phases WHERE run_id=? AND phase=?", (run_id, phase)).fetchone()
            return row[0] if row else None

    def claim_job(self, run_id: str, job_id: str) -> bool:
        with self.connect() as db:
            return db.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?,NULL)",
                              (run_id, job_id, "running")).rowcount == 1

    def finish_job(self, run_id: str, job_id: str, result: dict) -> None:
        identity = self.artifact(result)
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='complete',result_id=? WHERE run_id=? AND id=?",
                       (identity, run_id, job_id))

    def job(self, run_id: str, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE run_id=? AND id=?", (run_id, job_id)).fetchone()
            return dict(row) if row else None

    def sandbox(self, run_id: str, role: str, **values) -> dict:
        if role not in ("experiment", "decoder"):
            raise ValueError("Unknown sandbox role")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO sandboxes VALUES (?,?,?,NULL,'planned')",
                       (run_id, role, f"repoweave-{run_id[:32]}-{role}"))
            for key, value in values.items():
                if key not in ("sandbox_id", "status"):
                    raise ValueError("Unknown sandbox field")
                db.execute(f"UPDATE sandboxes SET {key}=? WHERE run_id=? AND role=?", (value, run_id, role))
            return dict(db.execute("SELECT * FROM sandboxes WHERE run_id=? AND role=?", (run_id, role)).fetchone())
