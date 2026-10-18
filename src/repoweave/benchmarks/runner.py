"""Sequential fresh-process comparisons of one immutable snapshot."""

import importlib.metadata
import multiprocessing
import os
import platform
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Literal

import zstandard

from repoweave.archive.manifest import ArchiveFormatError
from repoweave.benchmarks.splits import Splits
from repoweave.dictionaries.train import load_bundle
from repoweave.repository import Snapshot
from repoweave.routing.objective import Limits, score
from repoweave.routing.schemas import Policy

from .worker import measure


def _measure(snapshot: Snapshot, artifact: Path, restored: Path, method: str, phase: str,
             policy: Policy, registry: Path, timeout: float) -> dict:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=measure, args=(sender, snapshot, artifact, restored, method,
                                                   phase, policy, registry))
    try:
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            return {"status": "failed", "error": "timeout"}
        try:
            result = receiver.recv()
        except EOFError:
            return {"status": "failed", "error": "child-exited"}
        process.join(timeout=5)
        if process.exitcode != 0:
            return {"status": "failed", "error": "child-exited"}
        return result
    finally:
        if process.is_alive():
            process.kill()
            process.join()
        receiver.close()
        sender.close()
        process.close()


def benchmark(
    snapshot: Snapshot, policy: Policy, *, data_dir: Path, repeats: int = 3, warmups: int = 1,
    limits: Limits | None = None, splits: Splits | None = None, repository_id: str | None = None,
    partition: Literal["validation", "test"] = "validation", final: bool = False,
    control_policy: Policy | None = None,
) -> dict:
    if not 2 <= repeats <= 20 or not 0 <= warmups <= 5:
        raise ValueError("Use 2–20 repetitions and 0–5 warmups")
    if (splits is None) != (repository_id is None):
        raise ValueError("Dataset splits and repository ID must be supplied together")
    if partition == "test" and (not final or splits is None):
        raise ValueError("Test data requires explicit final reporting and dataset splits")
    if final and partition != "test":
        raise ValueError("Final holdout reporting requires the test partition")
    if splits is not None and repository_id is not None:
        splits.require(repository_id, partition)
    limits = limits or Limits()
    if policy.grouping == "graph" and (control_policy is None or control_policy.grouping != "extension"):
        raise ValueError("Graph dictionary benchmarks require an extension-grouped control policy")
    if control_policy is not None and (control_policy.zstd_level != policy.zstd_level
                                       or control_policy.codec != policy.codec):
        raise ValueError("Grouping controls must use the same codec and level")
    registry = data_dir / "dictionaries"
    fallbacks: dict[str, list[str]] = {}
    asset_errors: list[str] = []
    provenance: list[set[tuple[str, str]]] = []
    configurations = [("candidate", "rpw", policy), ("rpw-zstd", "rpw", Policy()),
                      ("tar-zstd", "tar-zstd", Policy()), ("tar-lzma", "tar-lzma", Policy())]
    if control_policy is not None:
        configurations.insert(1, ("extension-control", "rpw", control_policy))
    for name, method, selected in configurations:
        if selected.dictionary_id is None:
            continue
        try:
            bundle = load_bundle(registry, selected.dictionary_id)
        except ArchiveFormatError:
            asset_errors.append(name)
        else:
            if splits is not None and bundle.splits != splits:
                raise ValueError("Dictionary and evaluation partitions differ")
            if (snapshot.snapshot_id in bundle.training_snapshots.values()
                    or repository_id in bundle.training_snapshots):
                raise ValueError("Training repositories cannot be used for evaluation")
            provenance.append(set(bundle.training_snapshots.items()))
            _, fallbacks[name] = bundle.for_snapshot(snapshot)
    if len(provenance) > 1 and any(value != provenance[0] for value in provenance[1:]):
        raise ValueError("Grouping controls must use the same training repositories and snapshots")
    data_dir.mkdir(parents=True, exist_ok=True)
    policy.save(data_dir / "policies")
    if control_policy is not None:
        control_policy.save(data_dir / "policies")
    results = []
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=data_dir) as temporary:
        work = Path(temporary)
        for name, method, selected in configurations:
            result: dict = {"name": name, "method": method, "status": "ok", "samples": [],
                            "policy_id": selected.policy_id if method == "rpw" else None}
            for repetition in range(warmups + repeats):
                artifact = work / f"{name}-{repetition}.archive"
                restored = work / f"{name}-{repetition}.restored"
                encoded = _measure(snapshot, artifact, restored, method, "encode", selected,
                                   registry, limits.encode_seconds)
                if encoded["status"] != "ok":
                    result.update(status="failed", error=encoded["error"], failed_phase="encode")
                    break
                decoded = _measure(snapshot, artifact, restored, method, "decode", selected,
                                   registry, limits.decode_seconds)
                if decoded["status"] != "ok":
                    result.update(status="failed", error=decoded["error"], failed_phase="decode")
                    break
                if repetition >= warmups:
                    result["samples"].append({
                        "archive_bytes": encoded["archive_bytes"], "reconstructed": True,
                        "encode_seconds": encoded["seconds"], "decode_seconds": decoded["seconds"],
                        "encode_peak_rss_bytes": encoded["peak_rss_bytes"],
                        "decode_peak_rss_bytes": decoded["peak_rss_bytes"],
                        "manifest_bytes": encoded.get("manifest_bytes"),
                        "payload_bytes": encoded.get("payload_bytes"),
                        "embedded_dictionary_bytes": encoded.get("embedded_dictionary_bytes", 0),
                        "dependencies": encoded.get("dependencies", []),
                        "external_dependency_bytes": 0,
                    })
            if result["status"] == "ok":
                result["timing"] = {
                    key: {"mean": statistics.mean(values), "variance": statistics.variance(values)}
                    for key in ("encode_seconds", "decode_seconds")
                    for values in [[sample[key] for sample in result["samples"]]]
                }
            result["score_bytes"] = score(result, limits)
            result["eligible"] = result["score_bytes"] is not None
            result["ineligible_reason"] = (None if result["eligible"] else
                                          "failed, unverified, nondeterministic, or over limits")
            results.append(result)
    eligible = [result for result in results if result["eligible"]]
    winner = min(eligible, key=lambda result: result["score_bytes"])["name"] if eligible else None
    return {
        "schema_version": 1, "snapshot": snapshot.to_report(), "policy": policy.model_dump(),
        "policy_id": policy.policy_id, "original_bytes": sum(e.size_bytes or 0 for e in snapshot.entries),
        "partition": partition if splits else "unpartitioned-exploratory", "final": final,
        "repository_id": repository_id, "splits": splits.model_dump() if splits else None,
        "repeats": repeats, "warmups": warmups, "limits": limits.model_dump(),
        "dictionary_fallback_groups": fallbacks, "asset_errors": asset_errors,
        "control_policy": control_policy.model_dump() if control_policy else None,
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                        "machine": platform.machine(), "processor": platform.processor(),
                        "cpu_count": os.cpu_count(), "zstandard": zstandard.__version__,
                        "libzstd": list(zstandard.ZSTD_VERSION),
                        "pydantic": importlib.metadata.version("pydantic"),
                        "repoweave": importlib.metadata.version("repoweave")},
        "measurement": {
            "execution": "sequential, fresh spawn process for every encode and decode sample",
            "memory": "resource.ru_maxrss process-lifetime peak including imports; bytes on macOS, "
                      "KiB converted to bytes on Linux; not incremental operation memory",
            "timing": "perf_counter; excludes process startup; RPW encode includes self-verification; "
                      "all decodes include exact byte/inventory comparison",
            "warmup": "discarded full encode/decode runs; filesystem caches are not flushed",
            "limits": "wall-clock child timeout; measured RSS eligibility gate, not a memory sandbox",
            "resources": "same host; no concurrent benchmark children; single-threaded codecs; "
                         "no CPU pinning; other machine activity is uncontrolled",
        },
        "results": results, "smallest_eligible": winner,
    }
