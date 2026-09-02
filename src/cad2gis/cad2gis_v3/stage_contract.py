"""Deterministic, cache-addressable conversion stage receipts."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar


T = TypeVar("T")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class StageRecorder:
    """Record immutable contracts without caching Python object instances.

    A future or external cache may use ``cache_key`` only after validating the
    declared output hash.  This deliberately avoids unsafe pickle caches and
    keeps every conversion stage auditable.
    """

    receipts: list[dict[str, Any]] = field(default_factory=list)

    def run(
        self,
        name: str,
        *,
        version: str,
        inputs: dict[str, Any],
        operation: Callable[[], T],
        summarize: Callable[[T], dict[str, Any]],
    ) -> T:
        input_sha256 = canonical_sha256(inputs)
        cache_key = canonical_sha256({
            "stage": name,
            "version": version,
            "input_sha256": input_sha256,
        })
        started = time.perf_counter()
        result = operation()
        elapsed = time.perf_counter() - started
        output_summary = summarize(result)
        self.receipts.append({
            "schema_version": "cad2gis.stage_contract.v1",
            "stage": name,
            "version": version,
            "deterministic": True,
            "cacheable": True,
            "cache_key": cache_key,
            "input_sha256": input_sha256,
            "output_sha256": canonical_sha256(output_summary),
            "output_summary": output_summary,
            "elapsed_seconds": round(elapsed, 6),
        })
        return result

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "cad2gis.stage_contracts.v1",
            "stage_count": len(self.receipts),
            "total_elapsed_seconds": round(sum(
                float(item["elapsed_seconds"]) for item in self.receipts
            ), 6),
            "stages": list(self.receipts),
        }


__all__ = ["StageRecorder", "canonical_sha256"]
