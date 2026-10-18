"""The same topology serves the CLI and Studio; no import-time clients or database."""

from itertools import pairwise

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from . import nodes
from .context import Context
from .state import State


def build_graph(checkpointer=None):
    builder = StateGraph(State, context_schema=Context)

    def instrument(name):
        async def execute(state: State, runtime: Runtime[Context]):
            context = runtime.context
            context.runner.check_cancelled()
            context.store.event(state["run_id"], "node", {"node": name})
            async with context.tracer.span(name, run_id=state["run_id"]) as span:
                try:
                    result = await getattr(nodes, name)(state, context)
                except GraphInterrupt:
                    raise
                except Exception as error:  # noqa: BLE001 - checkpoint errors must be payload-free
                    context.store.event(state["run_id"], "node-failed", {
                        "node": name, "error_type": type(error).__name__,
                    })
                    raise ValueError("Workflow node failed") from None
                span.update(result)
                if "baseline_id" in result:
                    span["report_id"] = result["baseline_id"]
                if "decision_id" in result:
                    decision = context.store.read_artifact(result["decision_id"])
                    span.update({key: decision[key] for key in ("report_id", "candidate_id")})
                return result
        return execute

    names = ("observe", "diagnose", "propose", "experiment", "verify", "approve", "promote", "report")
    for name in names:
        builder.add_node(name, instrument(name))
    builder.add_edge(START, "observe")
    for source, target in pairwise(names[:5]):
        builder.add_edge(source, target)
    builder.add_conditional_edges("verify", lambda state: (
        "approve" if state["outcome"] == "awaiting-approval" else "report"
    ), ["approve", "report"])
    builder.add_edge("approve", "promote")
    builder.add_edge("promote", "report")
    builder.add_edge("report", END)
    return builder.compile(checkpointer=checkpointer)


graph = build_graph()
