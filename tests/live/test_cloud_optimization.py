"""Explicitly opt-in: synthetic Factory/LangSmith/Daytona optimization and decoder."""

import asyncio
import json
import os
import re
import uuid

import pytest
from langsmith.utils import LangSmithNotFoundError

from repoweave.config import load_settings
from repoweave.observability.tracing import Tracer
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy
from repoweave.runs.service import execute, submit
from repoweave.storage.database import Store


@pytest.mark.live
@pytest.mark.asyncio
async def test_full_cloud_optimization() -> None:
    if os.environ.get("REPOWEAVE_LIVE_CLOUD") != "1":
        pytest.skip("Set REPOWEAVE_LIVE_CLOUD=1 to authorize the generated fixture and two sandboxes")
    configured = load_settings()
    if configured.langsmith_api_key is None:
        pytest.fail("LANGSMITH_API_KEY is required", pytrace=False)
    recheck = os.environ.get("REPOWEAVE_LIVE_CLOUD_RECHECK")
    if recheck and not re.fullmatch(r"[0-9a-f]{32}", recheck):
        pytest.fail("REPOWEAVE_LIVE_CLOUD_RECHECK must identify an existing synthetic run", pytrace=False)
    output = configured.data_dir.resolve() / "live-cloud" / (recheck or uuid.uuid4().hex)
    settings = configured.model_copy(update={
        "data_dir": output / "state", "execution": "daytona", "allow_remote_data": True,
        "langsmith_tracing": True, "max_candidates": 1, "experiment_timeout_seconds": 90,
    })
    source = output / "owned-fixture"
    source.mkdir(parents=True, exist_ok=bool(recheck))
    sentinel = "REPOWEAVE_SYNTHETIC_SOURCE_MUST_NOT_APPEAR_IN_TRACES"
    if not recheck:
        (source / "sample.py").write_bytes((f"# {sentinel}\nVALUE = 123\n".encode()) * 1000)
        (source / "sample.py").chmod(0o755)
        (source / "binary.bin").write_bytes(bytes(range(256)) * 4)
        (source / "empty").mkdir()
        (source / "unicode-λ.txt").write_text("owned synthetic fixture\n", encoding="utf-8")
    snapshot = create_snapshot(source, output / "captures")
    report = {"status": "failed", "fixture_snapshot_id": snapshot.snapshot_id}
    tracer = Tracer(settings)
    store = Store(settings.data_dir)
    try:
        if recheck:
            from langsmith import Client

            previous = json.loads((output / "report.json").read_text())
            run_id = previous["run_id"]
            row = store.run(run_id)
            assert row["spec"]["snapshot_id"] == snapshot.snapshot_id
            trace_event = [e for e in store.events(run_id) if e["kind"] == "tracing"][-1]["data"]
            tracer.ids, tracer.failed = trace_event["span_ids"], trace_event["failed"]
            tracer.client = Client(api_key=settings.langsmith_api_key.get_secret_value(),
                                   api_url=settings.langsmith_endpoint, auto_batch_tracing=False,
                                   omit_traced_runtime_info=True)
        else:
            run_id = submit(source, Policy(), settings, approved_snapshot=snapshot.snapshot_id,
                            repeats=2, warmups=1,
                            limits=Limits(encode_seconds=30.0, decode_seconds=30.0,
                                          peak_memory_bytes=1024 * 1024 * 1024))
            report["run_id"] = run_id
            row = await execute(store, run_id, settings, tracer=tracer)
        report["run_id"] = run_id
        assert row["status"] in ("awaiting-approval", "completed-without-improvement")
        assert store.phase(run_id, "independent-decode") is not None
        roles = {role: store.sandbox(run_id, role) for role in ("experiment", "decoder")}
        assert all(value["status"] == "deleted" for value in roles.values())
        assert roles["experiment"]["sandbox_id"] != roles["decoder"]["sandbox_id"]
        assert not tracer.failed, "LangSmith export failed"
        async with asyncio.timeout(45):
            while True:
                try:
                    traces = [await asyncio.to_thread(tracer.client.read_run, identity) for identity in tracer.ids]
                except LangSmithNotFoundError:
                    traces = []  # Ingestion can be acknowledged before the read index catches up.
                if traces and all(t.end_time is not None for t in traces):
                    break
                await asyncio.sleep(2)
        assert {"repoweave.workflow", "repoweave.analyst", "repoweave.proposer"} <= {t.name for t in traces}
        assert all(t.end_time is not None for t in traces)
        assert sentinel not in json.dumps([t.model_dump(mode="json") for t in traces], default=str)
        decision_id = store.phase(run_id, "decision")
        report.update({"status": "passed", "outcome": row["status"], "sandboxes": roles,
                       "decision_id": decision_id, "decision": store.read_artifact(decision_id),
                       "trace_ids": tracer.ids, "trace_count": len(traces),
                       "independent_decode_id": store.phase(run_id, "independent-decode")})
    except Exception as error:  # noqa: BLE001 - SDK bodies and secrets stay out of test output
        report["error_type"] = type(error).__name__
        raise pytest.fail.Exception(
            f"Cloud optimization failed ({type(error).__name__}); see local receipt", pytrace=False,
        ) from None
    finally:
        if "run_id" in report:
            report["sandboxes"] = {role: store.sandbox(report["run_id"], role)
                                   for role in ("experiment", "decoder")}
        receipt = output / (f"recheck-{uuid.uuid4().hex}.json" if recheck else "report.json")
        write_json(receipt, report)
        print(f"Live cloud receipt: {receipt}")
