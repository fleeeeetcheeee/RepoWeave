"""Minimum complete archive bytes, subject to reconstruction and explicit limits."""

import math

from pydantic import BaseModel, ConfigDict, Field


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    encode_seconds: float = Field(default=300.0, gt=0)
    decode_seconds: float = Field(default=300.0, gt=0)
    peak_memory_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)


def score(result: dict, limits: Limits) -> int | None:
    samples = result.get("samples", [])
    if result.get("status") != "ok" or not samples:
        return None
    sizes = set()
    for sample in samples:
        if sample.get("reconstructed") is not True:
            return None
        size = sample.get("archive_bytes")
        if type(size) is not int or size < 0:
            return None
        sizes.add(size)
        for key, limit in (("encode_seconds", limits.encode_seconds),
                           ("decode_seconds", limits.decode_seconds),
                           ("encode_peak_rss_bytes", limits.peak_memory_bytes),
                           ("decode_peak_rss_bytes", limits.peak_memory_bytes)):
            value = sample.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= limit:
                return None
    return next(iter(sizes)) if len(sizes) == 1 else None
