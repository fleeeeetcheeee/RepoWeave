"""Deterministic explanation of verified comparisons; no policy mutation."""

from typing import Literal

from repoweave.routing.objective import score

from .schemas import Evidence


def report_comparison(
    evidence: Evidence, baseline_id: str, candidate_id: str,
    decision: Literal["accepted", "rejected"],
) -> str:
    evidence = Evidence.model_validate(evidence.model_dump())
    by_id = {result.result_id: result for result in evidence.results}
    if (baseline_id == candidate_id or baseline_id not in by_id or candidate_id not in by_id
            or decision not in ("accepted", "rejected")):
        raise ValueError("Invalid comparison references or decision")
    baseline, candidate = by_id[baseline_id], by_id[candidate_id]
    baseline_score = score(baseline.model_dump(), evidence.limits)
    candidate_score = score(candidate.model_dump(), evidence.limits)
    improvement = (baseline_score is not None and candidate_score is not None
                   and candidate_score < baseline_score)
    if decision == "accepted" and not improvement:
        raise ValueError("Cannot accept an unverified, ineligible, or non-improving candidate")
    return (
        f"Baseline {baseline_id}: eligible archive bytes={baseline_score}.\n"
        f"Candidate {candidate_id}: eligible archive bytes={candidate_score}.\n"
        f"Exact reconstruction verified for all candidate samples: "
        f"{candidate.status == 'ok' and bool(candidate.samples) and all(s.reconstructed for s in candidate.samples)}.\n"
        f"Decision: {decision}. No policy was promoted by this report.\n"
        "Only supplied measurements are compared. Timing is machine-specific; RSS is a "
        "process-lifetime peak. No unmeasured or general compression gain is claimed."
    )
