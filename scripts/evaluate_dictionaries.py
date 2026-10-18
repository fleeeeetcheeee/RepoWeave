"""Bounded training-only dictionary sweep; validation results are not final holdout evidence."""

import argparse
import tempfile
from pathlib import Path

from repoweave.archive.decode import unpack
from repoweave.benchmarks.runner import benchmark
from repoweave.benchmarks.splits import Splits
from repoweave.benchmarks.worker import _compare
from repoweave.dictionaries.train import train_dictionaries
from repoweave.repository import create_snapshot
from repoweave.routing.encode import encode_policy
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy


def evaluate(training: Path, validation: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    training_snapshot = create_snapshot(training, output / 'captures')
    validation_snapshot = create_snapshot(validation, output / 'captures')
    if training_snapshot.snapshot_id == validation_snapshot.snapshot_id:
        raise ValueError('Training and validation must be distinct repositories')
    splits = Splits(train=['training'], validation=['validation'], test=['reserved-unseen'])
    rows = []
    policies = [Policy()]
    for size in (1024, 8192, 32768):
        bundle = train_dictionaries({'training': training_snapshot}, splits,
                                    grouping='extension', dictionary_size=size)
        bundle.save(output / 'dictionaries')
        policies.append(Policy(name=f'extension-{size}', dictionary_id=bundle.dictionary_id,
                               grouping='extension'))
    for policy in policies:
        with tempfile.TemporaryDirectory(dir=output) as work:
            archive = Path(work) / 'candidate.rpw'
            manifest = encode_policy(validation_snapshot, archive, policy, output / 'dictionaries')
            restored = Path(work) / 'restored'
            unpack(archive, restored)
            _compare(validation_snapshot, restored)
            rows.append({'policy': policy.model_dump(), 'archive_bytes': archive.stat().st_size,
                         'assets_used': len(manifest.assets), 'reconstructed': True})
    winner = min(rows, key=lambda row: row['archive_bytes'])
    policy = Policy.model_validate(winner['policy'])
    report = benchmark(validation_snapshot, policy, data_dir=output, repeats=2, warmups=1,
                       splits=splits, repository_id='validation',
                       limits=Limits(encode_seconds=120., decode_seconds=120.))
    receipt = {'scope': 'exploratory validation; no final holdout or general superiority claim',
               'training_snapshot_id': training_snapshot.snapshot_id,
               'validation_snapshot_id': validation_snapshot.snapshot_id,
               'sweep': rows, 'benchmark': report}
    write_json(output / 'report.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('training', type=Path)
    parser.add_argument('validation', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = evaluate(args.training, args.validation, args.output)
    print({'report': str(args.output / 'report.json'),
           'sizes': [row['archive_bytes'] for row in result['sweep']],
           'winner': result['benchmark']['smallest_eligible']})
