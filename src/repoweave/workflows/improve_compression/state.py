from typing import TypedDict


class State(TypedDict, total=False):
    run_id: str
    baseline_id: str
    diagnosis_id: str
    proposals_id: str
    result_ids: list[str]
    decision_id: str
    outcome: str
