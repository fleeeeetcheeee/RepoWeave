"""Verify the real SDK's outgoing body, including automatic metadata injection."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from langsmith import Client

from repoweave.config import Settings
from repoweave.observability.tracing import Tracer, factory_span


@pytest.mark.asyncio
async def test_export_omits_source_secret_prose_and_environment(monkeypatch):
    sentinel = "private-source-and-secret-sentinel"
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", sentinel)
    monkeypatch.setenv("LANGSMITH_METADATA", json.dumps({"secret": sentinel}))
    calls = []

    def request(self, *args, **kwargs):
        calls.append((args, kwargs))
        return Mock(status_code=200, json=dict)

    monkeypatch.setattr(Client, "request_with_retries", request)
    settings = Settings(LANGSMITH_TRACING=True, LANGSMITH_API_KEY=sentinel,
                        REPOWEAVE_FACTORY_MODEL=sentinel)
    tracer = Tracer(settings)
    async with tracer.span("workflow", run_id="a" * 64, source=sentinel) as output:
        async with factory_span("analyst", settings):
            pass
        output.update({"prompt": sentinel, "report_id": "b" * 64})
    assert not tracer.failed
    bodies = [kwargs["request_kwargs"]["data"] for _, kwargs in calls]
    encoded = str(bodies)
    assert sentinel not in encoded
    assert "repoweave.analyst" in encoded
    assert "a" * 64 in encoded and "b" * 64 in encoded
    assert "duration_seconds" in encoded
    assert "parent_run_id" in encoded
    payloads = [json.loads(body) for body in bodies]
    posts = {item["id"]: item for item in payloads if "inputs" in item}
    for item in payloads:
        if "outputs" in item:
            assert item["end_time"]
            assert item["start_time"] == posts[item["id"]]["start_time"]
            assert item["trace_id"] == posts[item["id"]]["trace_id"]
            assert item["dotted_order"] == posts[item["id"]]["dotted_order"]
            assert item["session_name"] == posts[item["id"]]["session_name"]


@pytest.mark.asyncio
async def test_disabled_never_constructs_client(monkeypatch):
    monkeypatch.setattr("langsmith.Client", Mock(side_effect=AssertionError("should not initialize")))
    tracer = Tracer(Settings())
    async with tracer.span("workflow", source="sentinel"):
        pass
    assert tracer.client is None and tracer.ids == []


@pytest.mark.asyncio
async def test_trace_failure_does_not_change_result_or_exception():
    client = Mock()
    client.create_run.side_effect = RuntimeError("secret SDK payload")
    tracer = Tracer(Settings(LANGSMITH_TRACING=True), client)
    async with tracer.span("observe"):
        result = 42
    assert tracer.failed and result == 42
    with pytest.raises(asyncio.CancelledError):
        async with tracer.span("experiment"):
            raise asyncio.CancelledError
