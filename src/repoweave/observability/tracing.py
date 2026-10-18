"""Explicit, references-only spans. Never serialize arguments, state, or exceptions."""

import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic

from repoweave.config import Settings
from repoweave.routing.policy import content_id

_current: ContextVar["Tracer | None"] = ContextVar("repoweave_tracer", default=None)
_parent: ContextVar[tuple[str, str] | None] = ContextVar("repoweave_span", default=None)
NODES = frozenset({"workflow", "observe", "diagnose", "propose", "experiment", "verify",
                   "approve", "promote", "report", "analyst", "proposer"})
OUTCOMES = frozenset({"completed", "failed", "cancelled", "awaiting-approval", "promoted",
                      "completed-without-improvement", "rejected"})


def references(values: dict) -> dict:
    """Allow only opaque content identities and fixed application outcomes."""
    safe = {}
    for key in ("run_id", "snapshot_id", "report_id", "candidate_id", "model_id"):
        value = values.get(key)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            safe[key] = value
    if isinstance(values.get("outcome"), str) and values["outcome"] in OUTCOMES:
        safe["outcome"] = values["outcome"]
    return safe


class Tracer:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client
        self.failed = False
        self.ids: list[str] = []

    async def send(self, method: str, *args, **kwargs) -> None:
        if not self.settings.langsmith_tracing or self.failed:
            return
        try:
            if self.client is None:
                from langsmith import Client

                self.settings.validate_tracing()
                assert self.settings.langsmith_api_key is not None
                self.client = Client(
                    api_key=self.settings.langsmith_api_key.get_secret_value(),
                    api_url=self.settings.langsmith_endpoint,
                    auto_batch_tracing=False, timeout_ms=3000,
                    omit_traced_runtime_info=True, hide_metadata=True,
                    hide_inputs=references, hide_outputs=references,
                    tracing_sampling_rate=1.0,
                )
            await asyncio.to_thread(getattr(self.client, method), *args, **kwargs)
        except Exception:  # noqa: BLE001 - tracing must not change execution outcomes
            self.failed = True

    @asynccontextmanager
    async def span(self, node: str, **values):
        if node not in NODES:
            raise ValueError("Unknown trace node")
        identity = str(uuid.uuid4())
        start = datetime.now(UTC)
        clock = monotonic()
        parent = _parent.get()
        order = start.strftime("%Y%m%dT%H%M%S%fZ") + identity
        if parent:
            order = parent[1] + "." + order
        kwargs = {"id": identity, "start_time": start, "dotted_order": order,
                  "trace_id": order.split(".")[0][-36:]}
        if parent:
            kwargs["parent_run_id"] = parent[0]
        await self.send("create_run", name=f"repoweave.{node}", run_type="chain",
                        inputs=references(values), project_name=self.settings.langsmith_project,
                        **kwargs)
        if self.settings.langsmith_tracing:
            self.ids.append(identity)
        token = _parent.set((identity, order))
        active = _current.set(self)
        outcome = {"outcome": "completed"}
        try:
            yield outcome
        except BaseException as error:
            from langgraph.errors import GraphInterrupt

            outcome = {"outcome": "awaiting-approval" if isinstance(error, GraphInterrupt) else
                       "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"}
            raise
        finally:
            _parent.reset(token)
            _current.reset(active)
            # Duration is derived here; never accept a duration from an untrusted payload.
            await self.send("update_run", identity, end_time=datetime.now(UTC),
                            start_time=start, trace_id=kwargs["trace_id"], dotted_order=order,
                            parent_run_id=parent[0] if parent else None,
                            session_name=self.settings.langsmith_project,
                            outputs=references({**values, **outcome}),
                            extra={"duration_seconds": monotonic() - clock})


@asynccontextmanager
async def factory_span(role: str, settings: Settings):
    tracer = _current.get() or Tracer(settings)
    async with tracer.span(role, model_id=content_id(settings.factory_model or "factory-default")):
        yield
