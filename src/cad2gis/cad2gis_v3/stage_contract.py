"""Content-addressed stage receipts; no stage-result cache is enabled."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class _StateEncoder(json.JSONEncoder):
    def default(self, value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            # Avoid asdict's deep copy of large immutable reader inventories.
            return {item.name: getattr(value, item.name) for item in fields(value)}
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (set, frozenset)):
            return sorted(value, key=canonical_sha256)
        # Never hash repr/str of unknown objects: addresses and truncated
        # representations are neither stable nor complete state contracts.
        return super().default(value)


def canonical_sha256(value: Any) -> str:
    encoder = _StateEncoder(
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256()
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("ascii"))
    return digest.hexdigest()


@dataclass
class StageRecorder:
    """Record full state, not only counts, without caching Python instances.

    ``cache_key`` is a reserved identity, NOT permission to reuse a result.
    Side effects, external decisions and complete dependency closure still
    require a separate cache protocol.  Receipts therefore remain fail-closed
    (cacheable=False), even when a stage is known to be deterministic.
    """

    receipts: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    _context_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        self._context_sha256 = canonical_sha256(self.context)

    def run(
        self,
        name: str,
        *,
        version: str,
        inputs: dict[str, Any] | Callable[[], dict[str, Any]],
        operation: Callable[[], T],
        summarize: Callable[[T], dict[str, Any]],
        fingerprint: Callable[[T], Any] | None = None,
        deterministic: bool = False,
    ) -> T:
        input_started = time.perf_counter()
        input_sha256 = canonical_sha256({
            "context_sha256": self._context_sha256,
            "inputs": inputs() if callable(inputs) else inputs,
        })
        cache_key = canonical_sha256({
            "stage": name,
            "version": version,
            "input_sha256": input_sha256,
        })
        input_elapsed = time.perf_counter() - input_started
        started = time.perf_counter()
        result = operation()
        elapsed = time.perf_counter() - started
        fingerprint_started = time.perf_counter()
        output_summary = deepcopy(summarize(result))
        output_sha256 = canonical_sha256(
            result if fingerprint is None else fingerprint(result)
        )
        summary_sha256 = canonical_sha256(output_summary)
        fingerprint_elapsed = input_elapsed + time.perf_counter() - fingerprint_started
        self.receipts.append({
            "schema_version": "cad2gis.stage_contract.v2",
            "stage": name,
            "version": version,
            "deterministic": deterministic,
            "cacheable": False,
            "cache_status": "disabled_receipt_only",
            "cache_key": cache_key,
            "context_sha256": self._context_sha256,
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
            "output_fingerprint_scope": "result" if fingerprint is None else "explicit_state",
            "output_summary_sha256": summary_sha256,
            "output_summary": output_summary,
            "elapsed_seconds": round(elapsed, 6),
            "input_fingerprint_elapsed_seconds": round(input_elapsed, 6),
            "fingerprint_elapsed_seconds": round(fingerprint_elapsed, 6),
        })
        return result

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "cad2gis.stage_contracts.v2",
            "cache_status": "disabled_receipt_only",
            "stage_count": len(self.receipts),
            "total_elapsed_seconds": round(sum(
                float(item["elapsed_seconds"]) for item in self.receipts
            ), 6),
            "total_fingerprint_elapsed_seconds": round(sum(
                float(item["fingerprint_elapsed_seconds"]) for item in self.receipts
            ), 6),
            "stages": deepcopy(self.receipts),
        }


__all__ = ["StageRecorder", "canonical_sha256"]
