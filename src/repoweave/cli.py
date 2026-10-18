import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from repoweave.archive.manifest import ArchiveFormatError
from repoweave.codecs.base import CodecError
from repoweave.config import ConfigurationError, load_settings
from repoweave.repository import RepositoryError, create_snapshot
from repoweave.repository.inventory import Exclusion


def _analyze(input_root: Path, output: Path, data_dir: Path) -> None:
    if input_root.is_symlink() or not input_root.is_dir():
        raise RepositoryError("Input must be an existing directory, not a symlink")
    if output.exists() or output.is_symlink():
        raise RepositoryError("Analysis output already exists; choose a new report path")
    output.parent.mkdir(parents=True, exist_ok=True)
    root = input_root.resolve()
    report_parent = output.parent.resolve()
    report_exclusion = (
        report_parent if report_parent != root and report_parent.is_relative_to(root) else output
    )
    snapshot = create_snapshot(
        input_root, data_dir / "snapshots", exclude_paths=(data_dir, report_exclusion),
    )
    from repoweave.repository_graph import build_graph, context_order, group_files

    graph = build_graph(snapshot)
    report = snapshot.to_report()
    report["repository_graph"] = graph.to_report()
    report["candidate_context"] = context_order(graph)
    report["groups"] = {strategy: group_files(graph, strategy) for strategy in ("graph", "extension")}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent) as temporary:
        json.dump(report, temporary, sort_keys=True, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        # Publish without replacing an existing file, including one created during capture.
        os.link(temporary.name, output)
    print(f"Snapshot: {snapshot.snapshot_id}\nFiles: {snapshot.files_root}\nReport: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="repoweave",
        description="Repository-aware lossless compression experiments",
    )
    commands = parser.add_subparsers(dest="command")
    analyze = commands.add_parser("analyze", help="Capture and analyze a local repository")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--output", type=Path, required=True)
    packing = commands.add_parser("pack", help="Capture a repository and write a verified RPW archive")
    packing.add_argument("input", type=Path)
    packing.add_argument("-o", "--output", type=Path, required=True)
    selection = packing.add_mutually_exclusive_group()
    selection.add_argument("--policy", type=Path)
    selection.add_argument("--codec", choices=("stored", "zstd", "lzma"), default="zstd")
    unpacking = commands.add_parser("unpack", help="Verify and restore into a new directory")
    unpacking.add_argument("archive", type=Path)
    unpacking.add_argument("-o", "--output", type=Path, required=True)
    verification = commands.add_parser("verify", help="Compare supported paths, bytes, and executable bits")
    verification.add_argument("original", type=Path)
    verification.add_argument("restored", type=Path)
    verification.add_argument("--exclude", action="append", default=[], metavar="RELATIVE_PATH")
    benchmarking = commands.add_parser("benchmark", help="Measure verified archives in fresh processes")
    benchmarking.add_argument("input", type=Path)
    benchmarking.add_argument("--policy", type=Path, required=True)
    benchmarking.add_argument("--control-policy", type=Path)
    benchmarking.add_argument("--output", type=Path, required=True)
    benchmarking.add_argument("--repeats", type=int, default=3)
    benchmarking.add_argument("--warmups", type=int, default=1)
    benchmarking.add_argument("--max-encode-seconds", type=float, default=300.0)
    benchmarking.add_argument("--max-decode-seconds", type=float, default=300.0)
    benchmarking.add_argument("--max-memory-bytes", type=int, default=1024 * 1024 * 1024)
    benchmarking.add_argument("--splits", type=Path)
    benchmarking.add_argument("--repository-id")
    benchmarking.add_argument("--partition", choices=("validation", "test"), default="validation")
    benchmarking.add_argument("--final", action="store_true")
    training = commands.add_parser("train-dictionaries", help="Train only designated training repositories")
    training.add_argument("--splits", type=Path, required=True)
    training.add_argument("--repository", action="append", required=True, metavar="ID=PATH")
    training.add_argument("--grouping", choices=("none", "extension", "graph"), default="none")
    training.add_argument("--dictionary-size", type=int, default=8192)
    optimizing = commands.add_parser("optimize", help="Run bounded, measured policy optimization")
    optimizing.add_argument("input", type=Path)
    optimizing.add_argument("--policy", type=Path, required=True)
    optimizing.add_argument("--execution", choices=("local", "daytona"))
    optimizing.add_argument("--run-id")
    optimizing.add_argument("--repeats", type=int, default=3)
    optimizing.add_argument("--warmups", type=int, default=1)
    optimizing.add_argument("--approve-snapshot", help="Exact snapshot ID authorized for remote staging")
    optimizing.add_argument("--dictionary-id", action="append", default=[])
    optimizing.add_argument("--splits", type=Path)
    optimizing.add_argument("--repository-id")
    runs = commands.add_parser("runs", help="Inspect and control persisted optimization runs")
    operations = runs.add_subparsers(dest="operation", required=True)
    operations.add_parser("list")
    for operation in ("show", "approve", "resume", "cancel", "cleanup"):
        operations.add_parser(operation).add_argument("run_id")
    arguments = parser.parse_args()
    try:
        if arguments.command == "unpack":
            from repoweave.archive.decode import unpack

            manifest = unpack(arguments.archive, arguments.output)
            print(f"Restored {len(manifest.entries)} entries to {arguments.output}")
            return
        settings = load_settings()
        if arguments.command in ("optimize", "runs"):
            from repoweave.runs import service
            from repoweave.storage.database import Store

            store = Store(settings.data_dir)
            if arguments.command == "optimize":
                from repoweave.benchmarks.splits import load_splits
                from repoweave.routing.schemas import load_policy

                if arguments.execution:
                    settings = settings.model_copy(update={"execution": arguments.execution})
                run_id = service.submit(
                    arguments.input, load_policy(arguments.policy), settings,
                    run_id=arguments.run_id, repeats=arguments.repeats, warmups=arguments.warmups,
                    approved_snapshot=arguments.approve_snapshot,
                    dictionary_ids=tuple(arguments.dictionary_id),
                    splits=load_splits(arguments.splits) if arguments.splits else None,
                    repository_id=arguments.repository_id,
                )
                print(f"Run: {run_id}", flush=True)
                result = asyncio.run(service.execute(store, run_id, settings))
                print(f"Status: {result['status']}")
            elif arguments.operation == "list":
                print(json.dumps(store.runs(), indent=2))
            elif arguments.operation == "show":
                row = store.run(arguments.run_id)
                print(json.dumps({"id": row["id"], "status": row["status"],
                                  "events": store.events(arguments.run_id)}, indent=2))
            elif arguments.operation == "cancel":
                store.cancel(arguments.run_id)
            elif arguments.operation == "approve":
                asyncio.run(service.approve(store, arguments.run_id))
            elif arguments.operation == "resume":
                row = asyncio.run(service.execute(store, arguments.run_id, settings))
                print(f"Status: {row['status']}")
            elif arguments.operation == "cleanup":
                from repoweave.execution.daytona import Remote

                asyncio.run(Remote(store, arguments.run_id, settings).close())
            return
        if arguments.command == "analyze":
            _analyze(arguments.input, arguments.output, settings.data_dir)
        elif arguments.command == "pack":
            from repoweave.archive.encode import pack
            from repoweave.routing.schemas import Policy, load_policy

            policy = load_policy(arguments.policy) if arguments.policy else Policy(codec=arguments.codec)
            snapshot = pack(
                arguments.input, arguments.output, data_dir=settings.data_dir,
                codec_id=policy.codec, parameters=policy.parameters,
                policy=policy,
            )
            print(f"Archive: {arguments.output}\nSnapshot: {snapshot.snapshot_id}")
            _show_exclusions(snapshot.exclusions)
            if policy.dictionary_id:
                from repoweave.dictionaries.train import load_bundle

                bundle = load_bundle(settings.data_dir / "dictionaries", policy.dictionary_id)
                _, fallbacks = bundle.for_snapshot(snapshot)
                print(f"Dictionary groups falling back to plain Zstd: {fallbacks!r}")
        elif arguments.command == "train-dictionaries":
            from repoweave.benchmarks.splits import load_splits
            from repoweave.dictionaries.train import train_dictionaries

            splits = load_splits(arguments.splits)
            repositories = {}
            roots: set[Path] = set()
            for specification in arguments.repository:
                repository_id, separator, path = specification.partition("=")
                if not separator or not path or repository_id in repositories:
                    raise ArchiveFormatError("Use unique repository ID=PATH arguments")
                splits.require(repository_id, "train")
                root = Path(path).resolve()
                if root in roots:
                    raise ArchiveFormatError("Duplicate training repository root")
                roots.add(root)
                repositories[repository_id] = create_snapshot(
                    Path(path), settings.data_dir / "snapshots", exclude_paths=(settings.data_dir,),
                )
            bundle = train_dictionaries(repositories, splits, grouping=arguments.grouping,
                                        dictionary_size=arguments.dictionary_size)
            output = bundle.save(settings.data_dir / "dictionaries")
            print(f"Dictionary bundle: {bundle.dictionary_id}\nArtifact: {output}")
            if not bundle.groups:
                print("No nonempty training samples; all groups fall back to plain Zstd")
            for key, group in bundle.groups.items():
                print(f"Group {key!r}: {group.status}")
        elif arguments.command == "benchmark":
            from repoweave.archive.filesystem import open_directory, require_absent
            from repoweave.benchmarks.runner import benchmark
            from repoweave.benchmarks.splits import load_splits
            from repoweave.routing.objective import Limits
            from repoweave.routing.policy import write_json
            from repoweave.routing.schemas import load_policy

            if arguments.input.is_symlink() or not arguments.input.is_dir():
                raise RepositoryError("Input must be an existing directory, not a symlink")
            with open_directory(arguments.output.parent, create=True) as parent:
                require_absent(parent, arguments.output.name)
            root, output_parent = arguments.input.resolve(), arguments.output.parent.resolve()
            excluded_output = (output_parent if output_parent != root and output_parent.is_relative_to(root)
                               else arguments.output)
            snapshot = create_snapshot(arguments.input, settings.data_dir / "snapshots",
                                       exclude_paths=(settings.data_dir, excluded_output))
            report = benchmark(
                snapshot, load_policy(arguments.policy), data_dir=settings.data_dir,
                repeats=arguments.repeats, warmups=arguments.warmups,
                limits=Limits(encode_seconds=arguments.max_encode_seconds,
                              decode_seconds=arguments.max_decode_seconds,
                              peak_memory_bytes=arguments.max_memory_bytes),
                splits=load_splits(arguments.splits) if arguments.splits else None,
                repository_id=arguments.repository_id, partition=arguments.partition,
                final=arguments.final,
                control_policy=load_policy(arguments.control_policy) if arguments.control_policy else None,
            )
            write_json(arguments.output, report)
            print(f"Benchmark report: {arguments.output}")
            for result in report["results"]:
                print(f"{result['name']}: {result['status']}; eligible={result['eligible']}; "
                      f"archive_bytes={result['score_bytes']}")
        elif arguments.command == "verify":
            from repoweave.archive.integrity import verify

            original_exclusions, restored_exclusions = verify(
                arguments.original, arguments.restored, data_dir=settings.data_dir,
                exclude_paths=tuple(arguments.exclude),
            )
            print("Verified: supported paths, bytes, and executable bits match")
            _show_exclusions(original_exclusions, "Original")
            _show_exclusions(restored_exclusions, "Restored")
    except (ConfigurationError, RepositoryError, ArchiveFormatError, CodecError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    except OSError:
        parser.exit(2, f"{parser.prog}: Filesystem operation failed; check paths and permissions\n")
    except ValueError:
        parser.exit(2, f"{parser.prog}: Invalid request or operation failed; inspect run events for optimization errors\n")
    except (KeyboardInterrupt, asyncio.CancelledError):
        parser.exit(130, f"{parser.prog}: Interrupted\n")


def _show_exclusions(exclusions: Iterable[Exclusion], label: str = "Input") -> None:
    for exclusion in exclusions:
        print(f"{label} excluded: {exclusion.path!r} ({exclusion.reason})")


if __name__ == "__main__":
    main()
