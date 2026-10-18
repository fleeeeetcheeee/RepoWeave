"""Opt-in metadata-only tracing smoke; no model calls, source, or sandbox creation."""

import asyncio
import json
import os
import uuid

import pytest
from langsmith.utils import LangSmithNotFoundError

from repoweave.config import load_settings
from repoweave.observability.tracing import Tracer
from repoweave.routing.policy import content_id, write_json


@pytest.mark.live
@pytest.mark.asyncio
async def test_synthetic_langsmith_trace() -> None:
    if os.environ.get("REPOWEAVE_LIVE_CLOUD") != "1":
        pytest.skip("Set REPOWEAVE_LIVE_CLOUD=1 to authorize synthetic trace metadata")
    settings = load_settings().model_copy(update={"langsmith_tracing": True})
    identity = content_id(uuid.uuid4().hex)
    tracer = Tracer(settings)
    report = {"status": "failed", "run_id": identity}
    try:
        async with (
            tracer.span("workflow", run_id=identity, source="TRACE_REDACTION_SENTINEL"),
            tracer.span("observe", report_id="a" * 64),
        ):
            pass
        assert not tracer.failed
        async with asyncio.timeout(45):
            while True:
                try:
                    traces = [await asyncio.to_thread(tracer.client.read_run, item) for item in tracer.ids]
                except LangSmithNotFoundError:
                    traces = []  # Ingestion can be acknowledged before the read index catches up.
                if traces and all(t.end_time is not None for t in traces):
                    break
                await asyncio.sleep(2)
        assert "TRACE_REDACTION_SENTINEL" not in json.dumps([t.model_dump(mode="json") for t in traces], default=str)
        assert all(t.outputs["outcome"] == "completed" for t in traces)
        report.update({"status": "passed", "trace_ids": tracer.ids})
    except Exception as error:  # noqa: BLE001 - never expose SDK payloads
        report["error_type"] = type(error).__name__
        raise pytest.fail.Exception(f"Synthetic trace check failed ({type(error).__name__})",
                                    pytrace=False) from None
    finally:
        path = settings.data_dir.resolve() / "live-tracing" / f"{identity}.json"
        write_json(path, report)
        print(f"Live tracing receipt: {path}")
