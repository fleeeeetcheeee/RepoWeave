# RepoWeave

**Smaller code archives. AI-guided experiments. Every byte verified.**

Built for **HackHarvard 2026**.

RepoWeave compresses a code repository into a portable `.rpw` archive and checks that
it can restore the supported files exactly. Its dashboard lets you compare compression
methods, watch Factory suggest improvements, and download the verified result.

Our question: **can we make repository archives smaller by learning from measured
results and sharing compression across files?**

## How it works

1. **Capture the repository.** Save a fixed copy of the supported files, record their
   paths and fingerprints, and map Python imports for the dashboard.
2. **Compress across files.** Group multiple files into bounded blocks so repeated
   content can be compressed together. Use Zstd or LZMA, then shrink the archive's
   file index with compressed or compact metadata.
3. **Measure the options.** Compare complete archive sizes, compression time,
   restoration time, and memory use—including whole-folder tar baselines.
4. **Restore and check.** Decode the archive and compare the restored files byte for
   byte. A smaller result must also pass verification and resource limits before
   it can be offered for approval.

The archive includes the information needed to decode it. Restoring files works
without Factory, an AI model, or a cloud account.

## Where Factory fits

**Factory is the experiment planner.** Its analyst reviews benchmark results and
suggests what might help. Its proposer turns those ideas into bounded settings:
compression method, Zstd level, shared-block size, and metadata format. Registered
compression dictionaries are another supported option.

Factory receives measurement summaries, such as archive sizes and timings. Source
file contents stay out of its prompts. RepoWeave validates each proposal, runs the
actual compressor, and checks the result. You approve a verified improvement before
it becomes the active configuration. If nothing improves, the current policy stays.

## What we measured

With compact metadata and shared Zstd blocks, RepoWeave produced slightly smaller
archives than **tar + Zstd** on three public repository snapshots:

| Repository | tar + Zstd | RepoWeave | Smaller by |
| --- | ---: | ---: | ---: |
| Django 5.1.4 | 9.47 MB | 9.44 MB | 0.35% |
| SymPy 1.13.3 | 7.92 MB | 7.90 MB | 0.30% |
| Zstd 1.5.7 | 2.20 MB | 2.20 MB | 0.26% |

Sizes are rounded decimal MB; percentages use exact bytes. These comparisons cover
supported working-tree files, with exclusions documented in the
[full results](docs/metadata-research.md).

The gains are small and cost roughly **2.2–2.5× the compression time**. Two smaller
fixtures still favored tar + Zstd, and tar + LZMA remained smaller than our LZMA
archives on all five tested inputs. We have not established a universal winner.

In the live Factory demo, a measured proposal reduced an already-compressed archive
from **1,187 to 1,119 bytes—about 5.7% smaller** on our small synthetic fixture.
The updated project passed **579 local tests**, three live V4 cloud cases, and the
live browser showcase. See [verification details](docs/implementation-status.md).

## The live demo

**Compress remotely → reconstruct in a fresh environment → verify exact bytes.**

Daytona runs the compression experiment in an isolated sandbox. A second, fresh
sandbox receives only the archive and restores it. RepoWeave downloads the restored
files, checks them against the original snapshot, and confirms both sandboxes were
deleted. The dashboard shows the results, verification receipts, and cleanup status.

This demonstrates that the archive can travel to a clean machine and still restore
its contents correctly.

## Try it locally

You'll need Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 24 with npm.

```bash
uv sync --locked
npm --prefix web ci
bash scripts/run_demo.sh
```

Open **http://127.0.0.1:5173**, capture the included demo fixture, then create an
archive or run a benchmark. Packing and benchmarking require no service credentials.

For AI optimization, configure [Factory](docs/factory-agents.md). For the cloud
showcase, configure the [Daytona engine snapshot](docs/daytona-snapshot.md) and launch
with `bash scripts/run_demo.sh --live`. Credentials stay on the backend.

See the [demo walkthrough](docs/dashboard.md) and
[archive usage guide](docs/archive-usage.md) for more. RepoWeave currently archives
supported working-tree files; Git history and excluded files are outside that scope.
