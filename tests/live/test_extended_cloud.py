"""Public-repository scale and owned dictionary/promotion tests; opt-in only."""

import hashlib
import os
import random
import tarfile
import uuid
from pathlib import Path, PurePosixPath

import pytest

from repoweave.agents.schemas import Candidate, CandidatePolicy, Diagnosis, Hypothesis, Proposals
from repoweave.benchmarks.splits import Splits
from repoweave.config import load_settings
from repoweave.dictionaries.train import train_dictionaries
from repoweave.execution.daytona import Remote
from repoweave.experiments.runner import Runner
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy
from repoweave.runs.service import approve, execute, submit
from repoweave.storage.database import Store

PUBLIC = [
    ("django-5.1.4", "https://codeload.github.com/django/django/tar.gz/refs/tags/5.1.4",
     "adbb3c6702033d417401ce1925f7307b70ee2d8bfca636c82c66a38c55b97dda"),
    ("sympy-1.13.3", "https://codeload.github.com/sympy/sympy/tar.gz/refs/tags/sympy-1.13.3",
     "3cfd34ee7393c3786356b8ce49c3d6d212960d4c87c61d47e29a381cb5f41f58"),
]


def configuration(label):
    if os.environ.get("REPOWEAVE_LIVE_EXTENDED") != "1":
        pytest.skip("Set REPOWEAVE_LIVE_EXTENDED=1 to authorize public/generated fixtures and cloud tests")
    configured = load_settings()
    output = configured.data_dir.resolve() / "live-extended" / f"{label}-{uuid.uuid4().hex}"
    output.mkdir(parents=True)
    settings = configured.model_copy(update={
        "data_dir": output / "state", "execution": "daytona", "allow_remote_data": True,
        "langsmith_tracing": False, "max_candidates": 1, "experiment_timeout_seconds": 180,
    })
    return output, settings


def extract_public(archive: Path, destination: Path, digest: str) -> None:
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    destination.mkdir()
    total = 0
    with tarfile.open(archive, "r:gz") as stream:
        for index, member in enumerate(stream):
            assert index < 20000
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and ".." not in path.parts
            if member.isdir() or not (member.isfile()):
                continue  # Supported inputs are regular files; never follow links from downloads.
            total += member.size
            assert member.size <= 64 * 1024 * 1024 and total <= 256 * 1024 * 1024
            target = destination.joinpath(*path.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = stream.extractfile(member)
            assert reader is not None
            with reader, target.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
            target.chmod(0o600 | (member.mode & 0o111))


def cleanup_receipt(store, run_id):
    roles = {role: store.sandbox(run_id, role) for role in ("experiment", "decoder")}
    assert all(row["status"] == "deleted" for row in roles.values())
    assert roles["experiment"]["sandbox_id"] != roles["decoder"]["sandbox_id"]
    return roles


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize("name,url,digest", PUBLIC, ids=[row[0] for row in PUBLIC])
async def test_public_repository_cloud(name, url, digest):
    output, settings = configuration(name)
    receipt = {"status": "failed", "public_url": url, "download_sha256": digest}
    runner = None
    try:
        archive = Path(os.environ.get("REPOWEAVE_PUBLIC_ARCHIVES", "/tmp")) / f"repoweave-{name}.tar.gz"
        source = output / "public-source"
        extract_public(archive, source, digest)
        snapshot = create_snapshot(source, output / "captures")
        receipt["snapshot"] = snapshot.to_report()
        assert len(snapshot.entries) > 1000
        run_id = submit(source, Policy(), settings, repeats=2, warmups=1,
                        approved_snapshot=snapshot.snapshot_id,
                        limits=Limits(encode_seconds=120., decode_seconds=120.,
                                      peak_memory_bytes=1024 * 1024 * 1024))
        receipt["run_id"] = run_id
        store = Store(settings.data_dir)
        remote = Remote(store, run_id, settings)
        runner = Runner(store, run_id, settings, remote=remote)
        store.status(run_id, "running")
        await runner.prepare()
        report = await runner.report(Policy())
        assert all(r["eligible"] and all(s["reconstructed"] for s in r["samples"])
                   for r in report["results"])
        assert all(len({s["archive_bytes"] for s in r["samples"]}) == 1 for r in report["results"])
        await runner.independent_decode(report)
        await runner.close()
        store.status(run_id, "completed-without-improvement")
        receipt.update(status="passed", benchmark=report, sandboxes=cleanup_receipt(store, run_id),
                       independent_decode_id=store.phase(run_id, "independent-decode"))
    except Exception as error:  # noqa: BLE001 - SDK payloads must stay out of logs
        receipt["error_type"] = type(error).__name__
        raise pytest.fail.Exception(f"Public cloud test failed ({type(error).__name__}); see receipt",
                                    pytrace=False) from None
    finally:
        if runner:
            try:
                await runner.close()
            except Exception:  # noqa: BLE001 - preserve cleanup receipt without SDK payloads
                receipt["cleanup_pending"] = True
            receipt["sandboxes"] = {r: runner.store.sandbox(runner.run_id, r)
                                    for r in ("experiment", "decoder")}
        write_json(output / "report.json", receipt)
        print(f"Extended cloud receipt: {output / 'report.json'}")


class DictionaryProposal:
    """Fixed reasoning boundary: this test targets actual cloud assets and promotion."""
    def __init__(self, policy):
        self.policy = policy

    async def analyze(self, evidence, settings):
        ids = [evidence.results[0].result_id]
        return Diagnosis(summary="Owned dictionary transfer experiment", evidence_ids=ids,
                         hypotheses=[Hypothesis(explanation="Measure trained dictionary vs plain zstd",
                                                uncertainty="high", evidence_ids=ids)])

    async def propose(self, evidence, diagnosis, parent, settings, **kwargs):
        return Proposals(candidates=[Candidate(parent_policy_id=parent.policy_id,
                         policy=CandidatePolicy.model_validate(self.policy.model_dump()),
                         rationale="Exercise a registered training-only dictionary",
                         evidence_ids=diagnosis.evidence_ids)])


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_dictionary_transfer_and_approved_promotion():
    output, settings = configuration("dictionary-promotion")
    receipt = {"status": "failed", "agent_boundary": "fixed proposal; real cloud engine and workflow"}
    try:
        shared = random.Random(7).randbytes(800).hex()
        for label, offset in (("training", 0), ("validation", 1)):
            root = output / label
            root.mkdir()
            for index in range(50):
                body = (f"import module{(index + 1) % 50:02d}\n"
                        f"def function_{index}(value):\n    return value + {index + offset}\n")
                (root / f"module{index:02d}.py").write_text(body + f"COMMON = '{shared}'\n")
        splits = Splits(train=["training"], validation=["validation"], test=["untouched"])
        train = create_snapshot(output / "training", output / "captures")
        validation = create_snapshot(output / "validation", output / "captures")
        bundle = train_dictionaries({"training": train}, splits, grouping="extension", dictionary_size=2048)
        assert all(g.status == "trained" for g in bundle.groups.values())
        bundle.save(settings.data_dir / "dictionaries")
        candidate = Policy(dictionary_id=bundle.dictionary_id, grouping="extension")
        run_id = submit(output / "validation", Policy(), settings,
                        approved_snapshot=validation.snapshot_id, repeats=2, warmups=1,
                        dictionary_ids=(bundle.dictionary_id,), splits=splits, repository_id="validation",
                        limits=Limits(encode_seconds=30., decode_seconds=30.,
                                      peak_memory_bytes=1024 * 1024 * 1024))
        receipt.update(run_id=run_id, dictionary_id=bundle.dictionary_id,
                       training_snapshot_id=train.snapshot_id, validation_snapshot_id=validation.snapshot_id)
        store = Store(settings.data_dir)
        agents = DictionaryProposal(candidate)
        assert (await execute(store, run_id, settings, agents=agents))["status"] == "awaiting-approval"
        receipt["sandboxes"] = cleanup_receipt(store, run_id)
        decision_id = store.phase(run_id, "decision")
        decision = store.read_artifact(decision_id)
        report = store.read_artifact(decision["report_id"])
        assert decision["eligible"] and decision["selected_bytes"] < decision["baseline_bytes"]
        assert decision["candidate_id"] == candidate.policy_id
        assert all(s["embedded_dictionary_bytes"] > 0 for s in report["results"][0]["samples"])
        plain = next(row for row in report["results"] if row["name"] == "rpw-zstd")
        assert report["results"][0]["score_bytes"] < plain["score_bytes"]
        jobs = {p: p.stat().st_mtime_ns for p in (store.root / "runs" / run_id / "jobs").glob("*/result.json")}
        # The user requested this promotion test; activation is isolated to this test's store.
        await approve(store, run_id)
        assert (await execute(Store(store.root), run_id, settings, agents=agents))["status"] == "promoted"
        assert (await execute(Store(store.root), run_id, settings, agents=agents))["status"] == "promoted"
        assert all(p.stat().st_mtime_ns == stamp for p, stamp in jobs.items())
        assert len([e for e in store.events(run_id) if e["kind"] == "promoted"]) == 1
        with store.connect() as db:
            assert db.execute("SELECT policy_id FROM active WHERE scope=?",
                              (validation.snapshot_id,)).fetchone()[0] == candidate.policy_id
        receipt.update(status="passed", outcome="promoted", decision=decision, benchmark=report,
                       sandboxes=cleanup_receipt(store, run_id),
                       independent_decode_id=store.phase(run_id, "independent-decode"),
                       promotion_events=1, jobs_reexecuted_after_approval=0)
    except Exception as error:  # noqa: BLE001 - SDK payloads must stay out of logs
        receipt["error_type"] = type(error).__name__
        raise pytest.fail.Exception(f"Dictionary/promotion live test failed ({type(error).__name__}); see receipt",
                                    pytrace=False) from None
    finally:
        if "run_id" in receipt:
            store = Store(settings.data_dir)
            receipt["sandboxes"] = {r: store.sandbox(receipt["run_id"], r) for r in ("experiment", "decoder")}
        write_json(output / "report.json", receipt)
        print(f"Extended cloud receipt: {output / 'report.json'}")
