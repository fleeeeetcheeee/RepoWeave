import copy

import pytest

from repoweave.routing.objective import Limits, score


def valid():
    return {"status": "ok", "samples": [{"archive_bytes": 1234, "reconstructed": True,
            "encode_seconds": 1.0, "decode_seconds": 1.0,
            "encode_peak_rss_bytes": 1000, "decode_peak_rss_bytes": 1000}]}


def test_minimum_full_bytes_subject_to_constraints() -> None:
    result = valid()
    assert score(result, Limits()) == 1234
    assert score(result, Limits(peak_memory_bytes=999)) is None
    assert score(result, Limits(encode_seconds=0.5)) is None
    assert score(result, Limits(decode_seconds=0.5)) is None
    result["status"] = "failed"
    assert score(result, Limits()) is None


@pytest.mark.parametrize("key,value", [
    ("reconstructed", False), ("reconstructed", 1), ("archive_bytes", -1),
    ("archive_bytes", True), ("encode_seconds", float("nan")),
    ("decode_seconds", float("inf")), ("encode_peak_rss_bytes", None),
    ("decode_peak_rss_bytes", -1),
])
def test_invalid_metrics_cannot_win(key, value) -> None:
    result = valid()
    result["samples"][0][key] = value
    assert score(result, Limits()) is None


def test_nondeterministic_or_missing_samples_cannot_win() -> None:
    result = valid()
    second = copy.deepcopy(result["samples"][0])
    second["archive_bytes"] += 1
    result["samples"].append(second)
    assert score(result, Limits()) is None
    assert score({"status": "ok", "samples": []}, Limits()) is None
