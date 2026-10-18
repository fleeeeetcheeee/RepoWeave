import asyncio
import json
import traceback
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from droid_sdk import HookExecution, ToolCall
from droid_sdk.errors import DroidConnectionError, RunTimeoutError
from pydantic import ValidationError

from repoweave.agents import factory
from repoweave.agents.factory import AgentRunError, analyze, propose
from repoweave.agents.report import report_comparison
from repoweave.agents.schemas import (
    Candidate,
    CandidatePolicy,
    Diagnosis,
    Evidence,
    Hypothesis,
    Measurement,
    MeasurementSample,
    Proposals,
    summarize_benchmark,
)
from repoweave.benchmarks.splits import Splits
from repoweave.config import Settings
from repoweave.dictionaries.train import train_dictionaries
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.schemas import Policy


@pytest.fixture
def evidence() -> Evidence:
    sample = MeasurementSample(archive_bytes=1000, reconstructed=True, encode_seconds=0.1,
                               decode_seconds=0.05, encode_peak_rss_bytes=10000,
                               decode_peak_rss_bytes=10000)
    return Evidence(snapshot_id="a" * 64, original_bytes=10000, limits=Limits(), results=[
        Measurement(result_id="b" * 64, method="rpw", policy_id=Policy().policy_id,
                    status="ok", samples=[sample, sample]),
    ])


@pytest.fixture
def diagnosis(evidence) -> Diagnosis:
    return Diagnosis(summary="Synthetic baseline only.", evidence_ids=["b" * 64], hypotheses=[
        Hypothesis(explanation="A different level may reduce bytes; this is unmeasured.",
                   uncertainty="high", evidence_ids=["b" * 64]),
    ])


@pytest.fixture
def proposals() -> Proposals:
    return Proposals(candidates=[Candidate(
        parent_policy_id=Policy().policy_id, policy=CandidatePolicy(zstd_level=6),
        rationale="Test an unmeasured level.", evidence_ids=["b" * 64],
    )])


@pytest.fixture
def sdk(monkeypatch, diagnosis):
    state = SimpleNamespace(output=diagnosis, success=True, interrupted=False,
                            error=None, setup_error=None, tools=[], servers=[],
                            restrict=frozenset({"Read"}), disabled=frozenset({"Read"}),
                            sessions=[], prompts=[], stream_closed=False, closed=False,
                            wait=False, events=[])

    class FakeSession:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.settings = SimpleNamespace(restrict_tools=state.restrict,
                                            disabled_tools=state.disabled)
            state.sessions.append(self)

        async def __aenter__(self):
            assert self.options["cwd"].is_dir()
            assert list(self.options["cwd"].iterdir()) == []
            if state.setup_error:
                raise state.setup_error
            return self

        async def __aexit__(self, *args):
            state.closed = True

        async def list_tools(self):
            return state.tools

        async def list_mcp_servers(self):
            return SimpleNamespace(servers=state.servers)

        @asynccontextmanager
        async def stream(self, prompt, **kwargs):
            state.prompts.append(prompt)

            async def events():
                if state.wait:
                    await asyncio.Event().wait()
                if state.error:
                    raise state.error
                for event in state.events:
                    yield event

            stream = SimpleNamespace(result=SimpleNamespace(
                success=state.success, interrupted=state.interrupted, output=state.output,
            ))

            class Stream:
                result = stream.result

                def __aiter__(self):
                    return events()

            try:
                yield Stream()
            finally:
                state.stream_closed = True

    monkeypatch.setattr(factory, "Session", FakeSession)
    return state


@pytest.mark.asyncio
async def test_success_config_and_secret_safe_payload(sdk, evidence, diagnosis):
    settings = Settings(FACTORY_API_KEY="sentinel-secret", REPOWEAVE_FACTORY_MODEL="")
    assert await analyze(evidence, settings) == diagnosis
    options = sdk.sessions[0].options
    config = options["config"]
    assert config.restrict_tools == config.disabled_tools == frozenset({"Read"})
    assert config.mcp_servers == ()
    assert config.disable_builtin_skills and config.auto_reject_permission_requests
    assert options["model"] is None
    assert options["api_key"] == "sentinel-secret"
    assert "supplied measurements" in config.system_prompt
    assert "sentinel-secret" not in sdk.prompts[0]
    assert "untrusted measurement data" in sdk.prompts[0]
    assert sdk.closed and sdk.stream_closed
    assert not options["cwd"].exists()


@pytest.mark.asyncio
async def test_separate_workspaces_and_configured_model(sdk, evidence):
    settings = Settings(REPOWEAVE_FACTORY_MODEL="account-model")
    await asyncio.gather(analyze(evidence, settings), analyze(evidence, settings))
    assert sdk.sessions[0].options["cwd"] != sdk.sessions[1].options["cwd"]
    assert all(s.options["model"] == "account-model" for s in sdk.sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("restrict", None), ("restrict", frozenset()), ("disabled", frozenset()),
    ("tools", [SimpleNamespace(allowed=True)]), ("servers", [object()]),
])
async def test_effective_policy_fails_closed(sdk, evidence, field, value):
    setattr(sdk, field, value)
    with pytest.raises(AgentRunError, match="Factory"):
        await analyze(evidence, Settings())
    assert not sdk.prompts
    assert sdk.closed
    assert not sdk.sessions[0].options["cwd"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["interrupted", "failed", "missing", "malformed"])
async def test_partial_or_invalid_outputs_are_rejected(sdk, evidence, failure):
    if failure in ("interrupted", "failed"):
        sdk.success = False
        sdk.interrupted = failure == "interrupted"
    elif failure == "missing":
        sdk.output = None
    else:
        sdk.output = sdk.output.model_copy(update={"evidence_ids": []})
    with pytest.raises(AgentRunError):
        await analyze(evidence, Settings())
    assert sdk.closed and sdk.stream_closed
    assert not sdk.sessions[0].options["cwd"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,field,category", [
    (DroidConnectionError("sentinel-secret"), "setup_error", "setup or transport"),
    (DroidConnectionError("sentinel-secret"), "error", "setup or transport"),
    (RunTimeoutError("sentinel-secret"), "error", "timed out"),
])
async def test_errors_are_sanitized_and_workspaces_removed(sdk, evidence, error, field, category):
    setattr(sdk, field, error)
    with pytest.raises(AgentRunError, match=category) as captured:
        await analyze(evidence, Settings())
    assert "sentinel-secret" not in "".join(traceback.format_exception(captured.value))
    assert not sdk.sessions[0].options["cwd"].exists()


@pytest.mark.asyncio
async def test_cancellation_propagates_and_cleans_up(sdk, evidence):
    sdk.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await analyze(evidence, Settings())
    assert sdk.closed and sdk.stream_closed
    assert not sdk.sessions[0].options["cwd"].exists()


@pytest.mark.asyncio
async def test_overall_timeout_cleans_up(sdk, evidence):
    sdk.wait = True
    with pytest.raises(AgentRunError, match="timed out"):
        await analyze(evidence, Settings(REPOWEAVE_EXPERIMENT_TIMEOUT_SECONDS=1))
    assert sdk.closed and sdk.stream_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [ToolCall(name="Read", tool_use_id="test", input={}),
                                   HookExecution(hook_id="test")])
async def test_unexpected_execution_event_aborts(sdk, evidence, event):
    sdk.events = [event]
    with pytest.raises(AgentRunError, match="unexpected tool or hook"):
        await analyze(evidence, Settings())
    assert sdk.closed and sdk.stream_closed


@pytest.mark.asyncio
async def test_payload_byte_limit_precedes_session_creation(sdk):
    with pytest.raises(AgentRunError, match="byte limit"):
        await factory._call("analyst", {"oversized": "x" * (128 * 1024)}, Diagnosis, Settings())
    assert not sdk.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["diagnosis", "hypothesis"])
async def test_invented_diagnosis_evidence(sdk, evidence, diagnosis, where):
    data = diagnosis.model_dump()
    target = data if where == "diagnosis" else data["hypotheses"][0]
    target["evidence_ids"] = ["c" * 64]
    sdk.output = Diagnosis.model_validate(data)
    with pytest.raises(AgentRunError, match="unknown evidence"):
        await analyze(evidence, Settings())


@pytest.mark.asyncio
async def test_valid_proposals_and_budget(sdk, evidence, diagnosis, proposals):
    sdk.output = proposals
    settings = Settings(REPOWEAVE_MAX_CANDIDATES=100)
    assert await propose(evidence, diagnosis, Policy(), settings) == proposals
    payload = json.loads(sdk.prompts[0].split("\n", 1)[1])
    assert payload["allowed"]["max_candidates"] == 3
    assert payload["allowed"]["dictionaries"] == {}
    assert payload["evidence"]["limits"] == evidence.limits.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("change,match", [
    ({"parent_policy_id": "c" * 64}, "wrong parent"),
    ({"evidence_ids": ["c" * 64]}, "unknown evidence"),
    ({"policy": {"dictionary_id": "c" * 64, "grouping": "graph"}}, "unavailable dictionary"),
])
async def test_invalid_proposal_references(sdk, evidence, diagnosis, proposals, change, match):
    data = proposals.model_dump()
    data["candidates"][0].update(change)
    sdk.output = Proposals.model_validate(data)
    with pytest.raises(AgentRunError, match=match):
        await propose(evidence, diagnosis, Policy(), Settings())


@pytest.mark.asyncio
async def test_configured_candidate_budget(sdk, evidence, diagnosis, proposals):
    sdk.output = Proposals(candidates=proposals.candidates * 2)
    with pytest.raises(AgentRunError, match="candidate budget"):
        await propose(evidence, diagnosis, Policy(), Settings(REPOWEAVE_MAX_CANDIDATES=1))


@pytest.mark.asyncio
async def test_invalid_inputs_fail_before_call(sdk, evidence, diagnosis):
    with pytest.raises(AgentRunError, match="no supplied measurements"):
        await propose(evidence, diagnosis, Policy(zstd_level=8), Settings())
    with pytest.raises(AgentRunError, match="dictionary bundle"):
        await propose(evidence, diagnosis, Policy(), Settings(), dictionary_ids=("c" * 64,))
    assert not sdk.sessions


@pytest.mark.parametrize("policy", [
    {"codec": "lzma"}, {"zstd_level": 23}, {"zstd_level": "9"},
    {"dictionary_id": "../escape"}, {"grouping": "graph"}, {"command": "echo injected"},
])
def test_candidate_policy_rejects_unsupported_values(policy):
    with pytest.raises(ValidationError):
        CandidatePolicy.model_validate(policy)


def test_bounded_strict_outputs(proposals, diagnosis):
    with pytest.raises(ValidationError):
        Proposals(candidates=proposals.candidates * 4)
    with pytest.raises(ValidationError):
        Diagnosis.model_validate({**diagnosis.model_dump(), "command": "execute"})


def test_report_projection_excludes_source_names_errors_and_secrets(evidence):
    result = evidence.results[0].model_dump()
    result.update(name="sentinel-secret", error="sentinel-secret")
    result["samples"][0]["dependencies"] = ["sentinel-secret"]
    report = {"snapshot": {"snapshot_id": evidence.snapshot_id, "files": "sentinel-secret"},
              "original_bytes": 10000, "limits": Limits().model_dump(),
              "results": [result], "environment": "sentinel-secret", "final": False,
              "partition": "validation"}
    sanitized = summarize_benchmark(report)
    assert "sentinel-secret" not in sanitized.model_dump_json()
    assert summarize_benchmark(report) == sanitized
    report["final"] = True
    with pytest.raises(ValueError, match="holdout"):
        summarize_benchmark(report)


@pytest.mark.parametrize("change", [{"encode_seconds": float("nan")}, {"archive_bytes": -1},
                                     {"reconstructed": "true"}])
def test_invalid_measurements(evidence, change):
    data = evidence.results[0].samples[0].model_dump()
    with pytest.raises(ValidationError):
        MeasurementSample.model_validate({**data, **change})


@pytest.mark.asyncio
async def test_dictionary_provenance_and_post_call_recheck(
    sdk, evidence, diagnosis, proposals, tmp_path, monkeypatch,
):
    root = tmp_path / "training"
    root.mkdir()
    for index in range(100):
        (root / f"sample{index}.py").write_text(
            f"def sample_{index}():\n    return 'repeated training text {index}'\n" * 8,
        )
    snapshot = create_snapshot(root, tmp_path / "snapshots")
    splits = Splits(train=["train"], validation=["validation"], test=["heldout"])
    bundle = train_dictionaries({"train": snapshot}, splits, grouping="extension", dictionary_size=512)
    assert any(group.status == "trained" for group in bundle.groups.values())
    bundle_path = bundle.save(tmp_path / "dictionaries")
    data = proposals.model_dump()
    data["candidates"][0]["policy"].update(dictionary_id=bundle.dictionary_id, grouping="extension")
    sdk.output = Proposals.model_validate(data)
    settings = Settings(REPOWEAVE_DATA_DIR=tmp_path)
    kwargs = {"dictionary_ids": (bundle.dictionary_id,), "splits": splits,
              "repository_id": "validation"}
    assert await propose(evidence, diagnosis, Policy(), settings, **kwargs) == sdk.output
    for invalid in (
        {**kwargs, "repository_id": "heldout"},
        {**kwargs, "splits": Splits(train=["other"], validation=["validation"], test=[])},
        {**kwargs, "dictionary_ids": ("c" * 64,)},
    ):
        with pytest.raises(AgentRunError, match="dictionary bundle"):
            await propose(evidence, diagnosis, Policy(), settings, **invalid)
    overlap = evidence.model_copy(update={"snapshot_id": snapshot.snapshot_id})
    with pytest.raises(AgentRunError, match="dictionary bundle"):
        await propose(overlap, diagnosis, Policy(), settings, **kwargs)

    async def corrupt_during_call(*args):
        bundle_path.write_bytes(b"corrupt")
        return sdk.output

    monkeypatch.setattr(factory, "_call", corrupt_during_call)
    with pytest.raises(AgentRunError, match="dictionary bundle"):
        await propose(evidence, diagnosis, Policy(), settings, **kwargs)


def test_reporter_checks_verified_improvement_and_preserves_rejection(evidence):
    data = evidence.results[0].model_dump()
    data["result_id"] = "c" * 64
    for sample in data["samples"]:
        sample["archive_bytes"] = 900
    evidence = Evidence(**{**evidence.model_dump(), "results": [
        evidence.results[0].model_dump(), data,
    ]})
    accepted = report_comparison(evidence, "b" * 64, "c" * 64, "accepted")
    assert "900" in accepted and "Decision: accepted" in accepted
    assert "No policy was promoted" in accepted
    assert "Decision: rejected" in report_comparison(evidence, "b" * 64, "c" * 64, "rejected")
    for change in ({"reconstructed": False}, {"encode_seconds": 400.0}, {"archive_bytes": 1100}):
        altered = evidence.model_dump()
        altered["results"][1]["samples"][0].update(change)
        with pytest.raises(ValueError, match="Cannot accept"):
            report_comparison(Evidence.model_validate(altered), "b" * 64, "c" * 64, "accepted")


@pytest.mark.asyncio
async def test_failure_does_not_change_policy_files(sdk, evidence, diagnosis, tmp_path):
    parent = Policy()
    path = parent.save(tmp_path)
    active = tmp_path / "active.json"
    active.write_text(json.dumps({"policy_id": parent.policy_id}))
    before = {file: file.read_bytes() for file in tmp_path.iterdir()}
    sdk.success = False
    with pytest.raises(AgentRunError):
        await propose(evidence, diagnosis, parent, Settings(REPOWEAVE_DATA_DIR=tmp_path))
    assert path.exists()
    assert {file: file.read_bytes() for file in tmp_path.iterdir()} == before
