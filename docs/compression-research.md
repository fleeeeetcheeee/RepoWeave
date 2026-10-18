# Measured compression follow-up (2026-10-18)

Later local [solid-block](solid-blocks.md) and [metadata/LZMA](metadata-research.md)
studies extend these results. The latter records small matched tar-zstd wins on
three public releases, while tar-lzma remains smaller. Historical results below
refer to the earlier per-file engine and have not been rewritten.

This work tests full portable archive bytes and exact reconstruction. It does not
claim that RepoWeave generally beats zstd or that neural model training occurred.
Zstd dictionary training and Factory-guided policy selection are separate operations.
The decoder still needs no model, service credentials, training data, or cloud connection.

## Approved private data

The user approved local reads and Daytona uploads for `~/Documents/VIETA` and
`~/Documents/Portfolio`. The test selected complete CSV files only, at most 8 MiB
per file, 16 MiB and 64 files per dataset. It skipped hidden/configuration/source files,
symlinks and suspicious credential filenames, and assigned anonymous staged names.
It did not test all data in either folder. Raw data and archives remain only under
ignored, owner-accessible `.repoweave/` run roots; no contents were printed or committed.
Private data was not sent to Factory, LangSmith or a public hosting provider.

Two live tests passed in 110.17 seconds using `repoweave-engine-v1`, before changing
the writer. Each ran one warmup and two measured samples with 120-second encode/decode
budgets and 1 GiB RSS eligibility; the independent decoder received only its archive.
All four sandboxes were confirmed deleted. Local controls after the writer change
reproduced the same plain RPW and tar-zstd level-3 sizes exactly.

| Dataset subset | Files | Input bytes | RPW zstd 3 | tar-zstd 3 | RPW advantage |
| --- | ---: | ---: | ---: | ---: | ---: |
| VIETA CSV | 23 | 16,064,788 | 2,218,884 | 2,227,181 | 0.37% |
| Portfolio CSV | 28 | 1,514,294 | 624,141 | 638,239 | 2.21% |

These small level-3 wins are real on these selected inputs. They are a comparison of
per-file RPW versus normalized whole-tree tar-zstd, both using the same zstd level,
with all paths, supported metadata and framing counted. They do not imply an advantage
for other files, zstd settings, or data layouts.

Level 9 illustrates why matched controls matter:

| Dataset subset | RPW zstd 9 | tar-zstd 9 | Winner |
| --- | ---: | ---: | --- |
| VIETA CSV | 1,782,851 | 1,011,136 | tar-zstd |
| Portfolio CSV | 610,716 | 598,644 | tar-zstd |

RPW-9 was measured in Daytona; tar-9 and matching RPW-9 were measured locally with one
warmup and two samples, exact reconstruction, identical normalized tar settings and
checksum behavior. These local control runs measure wall time in one process and
make no fresh-process memory eligibility claim. The cloud tar-lzma results were
372,836 and 517,864 bytes respectively, smaller than the RPW candidates.

Receipts (ignored local artifacts):

- `.repoweave/live-extended/private-vieta-0946812ed5354f70bdea2b2874032e94/report.json`
- `.repoweave/live-extended/private-portfolio-133617abc0224733805237ca2cf41864/report.json`
- `.repoweave/private-controls-vieta/report.json`
- `.repoweave/private-controls-portfolio/report.json`

## Encoder improvement and trained dictionaries

Previously, dictionary policies compared a dictionary frame with stored bytes and
charged the first file the entire dictionary cost. That could both miss shared gains
and produce archives larger than ordinary zstd. The writer now compares stored and
plain zstd first, accumulates dictionary savings across files sharing an asset, and
checks the full final archive against the same-level plain result. Unhelpful assets
are omitted. Archive V1/V2 decoding is unchanged; scratch alternatives are disk-spooled.
This costs extra encoding work and scratch disk; it is not a speed optimization.

A generated repeated-content workload with separate training/validation snapshots
was tested remotely on the new `repoweave-engine-amortized-v2` snapshot:

| Method | Delivered bytes |
| --- | ---: |
| Plain RPW zstd 3 | 58,582 |
| RPW zstd 3 + trained dictionary | 19,920 |
| Whole-tree tar-zstd 3 | 1,532 |
| Whole-tree tar-lzma | 1,696 |

The dictionary candidate includes its 2,048-byte asset plus base64 and reference
metadata. It saves 66.00% against same-level plain RPW on this deliberately favorable
fixture, but loses to whole-tree tar-zstd. The live test passed in 29.46 seconds,
verified actual bytes in a second sandbox, promoted once in an isolated test store,
and deleted both sandboxes. Repeated approval/resume reused completed experiments.

Receipt: `.repoweave/live-extended/dictionary-promotion-38d3c30c89074c6fa5498cc4372ff38f/report.json`.
The original historical test used a stored baseline; the current test uses plain zstd.

## Public repository training and Factory

`scripts/evaluate_dictionaries.py` trained extension dictionaries of 1,024, 8,192 and
32,768 bytes on pinned SymPy 1.13.3 and evaluated pinned Django 5.1.4. Training never
used Django bytes. At zstd level 3 all three policies selected the plain fallback:
15,968,155 bytes. Training a dictionary does not by itself guarantee useful transfer.
These repositories are development/validation data, not an untouched final holdout.

Factory then received sanitized public measurements and allowed dictionary IDs,
with tools disabled by the existing adapter. Two real calls (analyst/proposer) returned
one candidate: level 9 plus the 8,192-byte extension dictionary. Actual local engine
measurements, one warmup and two samples, produced 15,092,783 bytes, a 5.48% reduction
from RPW level 3. Both measured samples reconstructed exactly. The live Factory test
passed in 166.93 seconds. This is policy search, not Factory model fine-tuning.

The whole-tree tar-zstd-3 baseline remained much smaller at 9,469,815 bytes; tar-lzma
was 6,803,120 bytes. The matched level-9 plain RPW control is **15,118,695 bytes**:
the trained dictionary saves **25,912 bytes (0.17%)** beyond plain same-level RPW zstd.
That is a small, measured gain on a real repository, including dictionary overhead.
Whole-tree tar-zstd at level 9 is still much smaller at **8,075,567 bytes**. Both local
control samples reconstructed exactly and had identical sizes. This experiment
supports a narrow dictionary gain, not superiority over whole-tree zstd on Django.

Receipts:

- `.repoweave/dictionary-research-sympy-django/report.json`
- `.repoweave/dictionary-research-sympy-django/factory-aa3edc4f421948bea0a70cd7d43148b4/report.json`
- `.repoweave/dictionary-research-sympy-django/same-level-controls/report.json`

## Reproduction and next experiments

The new engine snapshot is active with engine ID
`aeb10f24c26e1543e5757fca9b71c2795b05f3ac8dbd8a11f4d281e8389f0e45`.
Only an allowlisted engine wheel and locked dependencies were uploaded to build it.
The local `.env` snapshot name was updated to `repoweave-engine-amortized-v2`; no
credential values changed. The original snapshot remains available for old-run
reproduction. Both persistent snapshots remain; all six test sandboxes were deleted.
The reviewed build is `.repoweave/engine-amortized-v2-reviewed/`.

Commands actually run for private/live checks:

```bash
env REPOWEAVE_LIVE_EXTENDED=1 REPOWEAVE_PRIVATE_APPROVED=VIETA,Portfolio:daytona .venv/bin/pytest -m live tests/live/test_private_cloud.py -q -s --tb=short
env REPOWEAVE_LIVE_EXTENDED=1 REPOWEAVE_DAYTONA_SNAPSHOT=repoweave-engine-amortized-v2 .venv/bin/pytest -m live tests/live/test_extended_cloud.py::test_live_dictionary_transfer_and_approved_promotion -q -s --tb=short
env REPOWEAVE_FACTORY_RESEARCH=1 .venv/bin/pytest -m live tests/live/test_factory_research.py -q -s --tb=short
```

The opt-in Factory research test consumes the prior local public sweep artifacts.
Rerunning the private test uses the newly configured engine; the historical private
receipts above used v1. `scripts/compare_zstd_levels.py SOURCE OUTPUT` produces matched
level-3/9 controls; `scripts/evaluate_dictionaries.py TRAIN VALIDATION OUTPUT` performs
the bounded dictionary sweep and repeated validation benchmark. Outputs must be new.

Useful next work is a broader repository-family training corpus, bounded dictionary
size/level sweeps, and an untouched repository-level final test set. Learned routing
or model fine-tuning is not justified by these few validation results. Whole-tree or
bounded solid-block encoding could address the large gap to tar-zstd, but needs a
separate format/design task with decoder bounds and compatibility tests. The current
encoder deliberately retains independent file frames.

Zstd's own documentation describes dictionary training as especially useful for
similar small inputs: [official zstd manual](https://github.com/facebook/zstd/blob/dev/programs/zstd.1.md).
Factory supplies schema-constrained proposals through its [Python SDK](https://docs.factory.com/sdk/python);
Daytona supplies the isolated measurement/decoder environments, not a learned codec.
