"""Real summary-only Factory suggestion, evaluated on an already-approved public fixture."""

import json
import os
import uuid
from pathlib import Path

import pytest

from repoweave.agents.factory import analyze, propose
from repoweave.agents.schemas import summarize_benchmark
from repoweave.benchmarks.runner import benchmark
from repoweave.benchmarks.splits import Splits
from repoweave.config import load_settings
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy


@pytest.mark.live
@pytest.mark.asyncio
async def test_factory_public_dictionary_research():
    if os.environ.get('REPOWEAVE_FACTORY_RESEARCH') != '1':
        pytest.skip('Requires approved public dictionary sweep and two summary-only Factory calls')
    root = Path('.repoweave/dictionary-research-sympy-django').resolve()
    previous = json.loads((root / 'report.json').read_bytes())
    source = Path('.repoweave/live-extended/django-5.1.4-02af7d276e894f0a804ae275e96bc626/public-source')
    output = root / ('factory-' + uuid.uuid4().hex)
    output.mkdir()
    receipt = {'status': 'failed', 'scope': 'public validation; no source sent to Factory'}
    settings = load_settings().model_copy(update={
        'data_dir': root, 'max_candidates': 1, 'langsmith_tracing': False,
        'experiment_timeout_seconds': 90,
    })
    try:
        evidence = summarize_benchmark(previous['benchmark'])
        diagnosis = await analyze(evidence, settings)
        splits = Splits(train=['training'], validation=['validation'], test=['reserved-unseen'])
        dictionary_ids = tuple(row['policy']['dictionary_id'] for row in previous['sweep']
                               if row['policy']['dictionary_id'])
        proposals = await propose(evidence, diagnosis, Policy(), settings,
                                  dictionary_ids=dictionary_ids, splits=splits,
                                  repository_id='validation')
        assert len(proposals.candidates) == 1
        candidate = Policy.model_validate(proposals.candidates[0].policy.model_dump())
        snapshot = create_snapshot(source, output / 'captures')
        assert snapshot.snapshot_id == previous['validation_snapshot_id']
        report = benchmark(snapshot, candidate, data_dir=root, repeats=2, warmups=1,
                           splits=splits, repository_id='validation',
                           limits=Limits(encode_seconds=120., decode_seconds=120.))
        assert all(s['reconstructed'] for row in report['results'] for s in row['samples'])
        receipt.update(status='passed', proposal=proposals.model_dump(), benchmark=report,
                       diagnosis=diagnosis.model_dump())
    except Exception as error:  # noqa: BLE001 - never print SDK response payloads
        receipt['error_type'] = type(error).__name__
        raise pytest.fail.Exception(f'Factory research failed ({type(error).__name__})',
                                    pytrace=False) from None
    finally:
        write_json(output / 'report.json', receipt)
        print(f'Factory research receipt: {output / "report.json"}')
