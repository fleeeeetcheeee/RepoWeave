"""Opt-in authentication and structured-output check using invented metrics only."""

import pytest

from repoweave.agents.factory import analyze, propose, validate_diagnosis
from repoweave.agents.schemas import Evidence, Measurement, MeasurementSample
from repoweave.config import load_settings
from repoweave.routing.objective import Limits
from repoweave.routing.schemas import Policy

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


async def test_factory_authentication_policy_and_structured_agents():
    settings = load_settings()
    parent = Policy()
    sample = MeasurementSample(archive_bytes=4096, reconstructed=True,
                               encode_seconds=0.02, decode_seconds=0.01,
                               encode_peak_rss_bytes=20_000_000, decode_peak_rss_bytes=20_000_000,
                               manifest_bytes=1024, payload_bytes=3072)
    evidence = Evidence(snapshot_id="a" * 64, original_bytes=16384, limits=Limits(), results=[
        Measurement(result_id="b" * 64, method="rpw", policy_id=parent.policy_id,
                    status="ok", samples=[sample, sample]),
    ])
    # Both calls verify the installed CLI's effective empty tool/MCP policy before prompting.
    diagnosis = await analyze(evidence, settings)
    validate_diagnosis(diagnosis, evidence)
    proposals = await propose(evidence, diagnosis, parent, settings)
    assert 1 <= len(proposals.candidates) <= min(3, settings.max_candidates)
    for candidate in proposals.candidates:
        assert candidate.parent_policy_id == parent.policy_id
        assert set(candidate.evidence_ids) <= evidence.result_ids
        assert candidate.policy.codec == "zstd"
        assert candidate.policy.dictionary_id is None
        assert candidate.policy.grouping == "none"
