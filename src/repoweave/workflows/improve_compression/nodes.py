"""Side effects have durable receipts; graph checkpoints hold only references."""

from langgraph.types import interrupt

from repoweave.agents.factory import validate_diagnosis
from repoweave.agents.schemas import Diagnosis, Proposals, summarize_benchmark
from repoweave.benchmarks.splits import Splits
from repoweave.routing.schemas import Policy

from .context import Context
from .state import State


async def observe(state: State, context: Context) -> dict:
    store, runner = context.store, context.runner
    identity = store.phase(state["run_id"], "baseline")
    if identity is None:
        report = await runner.report(Policy.model_validate(runner.spec["parent"]))
        identity = store.phase(state["run_id"], "baseline", report)
    return {"baseline_id": identity}


async def diagnose(state: State, context: Context) -> dict:
    store = context.store
    identity = store.phase(state["run_id"], "diagnosis")
    if identity is None:
        evidence = summarize_benchmark(store.read_artifact(state["baseline_id"]))
        result = await context.agents.analyze(evidence, context.settings)
        validate_diagnosis(result, evidence)
        identity = store.phase(state["run_id"], "diagnosis", result.model_dump())
    return {"diagnosis_id": identity}


async def propose(state: State, context: Context) -> dict:
    store, spec = context.store, context.runner.spec
    identity = store.phase(state["run_id"], "proposals")
    if identity is None:
        result = await context.agents.propose(
            summarize_benchmark(store.read_artifact(state["baseline_id"])),
            Diagnosis.model_validate(store.read_artifact(state["diagnosis_id"])),
            Policy.model_validate(spec["parent"]), context.settings,
            dictionary_ids=tuple(spec["dictionary_ids"]),
            splits=Splits.model_validate(spec["splits"]) if spec["splits"] else None,
            repository_id=spec["repository_id"],
        )
        identity = store.phase(state["run_id"], "proposals", result.model_dump())
    # Revalidate stored or externally returned proposals before any execution.
    assert identity is not None
    proposals = Proposals.model_validate(store.read_artifact(identity))
    evidence = summarize_benchmark(store.read_artifact(state["baseline_id"]))
    if len(proposals.candidates) > spec["max_candidates"]:
        raise ValueError("Candidate budget exceeded")
    for candidate in proposals.candidates:
        if (candidate.parent_policy_id != spec["parent_id"]
                or not set(candidate.evidence_ids) <= evidence.result_ids
                or (candidate.policy.dictionary_id is not None
                    and candidate.policy.dictionary_id not in spec["dictionary_ids"])):
            raise ValueError("Invalid candidate references")
    return {"proposals_id": identity}


async def experiment(state: State, context: Context) -> dict:
    proposals = Proposals.model_validate(context.store.read_artifact(state["proposals_id"]))
    identities = []
    for candidate in proposals.candidates:
        context.runner.check_cancelled()
        phase = f"candidate-{candidate.policy.policy_id}"
        identity = context.store.phase(state["run_id"], phase)
        if identity is None:
            report = await context.runner.report(candidate.policy)
            identity = context.store.phase(state["run_id"], phase, report)
        identities.append(identity)
    return {"result_ids": identities}


async def verify(state: State, context: Context) -> dict:
    baseline = context.store.read_artifact(state["baseline_id"])
    baseline_score = baseline["results"][0]["score_bytes"]
    selected, best = baseline, baseline_score
    selected_id = state["baseline_id"]
    for identity in state["result_ids"]:
        report = context.store.read_artifact(identity)
        # Revalidate artifacts and exact reconstruction even after restart.
        report = await context.runner.report(Policy.model_validate(report["policy"]))
        value = report["results"][0]["score_bytes"]
        if baseline_score is not None and value is not None and value < best:
            selected, best, selected_id = report, value, identity
    await context.runner.independent_decode(selected)
    eligible = selected_id != state["baseline_id"]
    decision = {"eligible": eligible, "baseline_id": state["baseline_id"],
                "report_id": selected_id, "candidate_id": selected["policy_id"],
                "baseline_bytes": baseline_score, "selected_bytes": best}
    decision_id = context.store.phase(state["run_id"], "decision", decision)
    return {"decision_id": decision_id,
            "outcome": "awaiting-approval" if eligible else "completed-without-improvement"}


async def approve(state: State, context: Context) -> dict:
    context.store.status(state["run_id"], "awaiting-approval")
    approved = interrupt({"run_id": state["run_id"], "decision_id": state["decision_id"]})
    if approved is not True or not context.store.run(state["run_id"])["approved"]:
        raise ValueError("Persisted explicit approval is required")
    return {}


async def promote(state: State, context: Context) -> dict:
    decision = context.store.read_artifact(state["decision_id"])
    if not decision["eligible"]:
        raise ValueError("Cannot promote an ineligible policy")
    report = context.store.read_artifact(decision["report_id"])
    policy = Policy.model_validate(report["policy"])
    if policy.policy_id != decision["candidate_id"]:
        raise ValueError("Selected policy differs from the verified decision")
    policy.save(context.store.root / "policies")
    context.store.promote(state["run_id"], decision["candidate_id"])
    return {"outcome": "promoted"}


async def report(state: State, context: Context) -> dict:
    context.store.phase(state["run_id"], "summary", {
        "outcome": state["outcome"], "decision_id": state["decision_id"],
        "comparison": context.store.read_artifact(state["decision_id"]),
        "limitations": "Validation fixture only; timings are machine-specific; no general gain claimed.",
    })
    context.store.status(state["run_id"], state["outcome"])
    return {}
