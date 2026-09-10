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


_CANONICAL_CHUNK_ASCII_LIMIT = 64 * 1024


def _key_budget(key: Any, remaining: int) -> int:
    # Bound the JSON object-key spelling, including its quotes.
    if type(key) is str:
        return remaining - ((6 if key.isascii() else 12) * len(key) + 2)
    if key is None or isinstance(key, bool):
        return remaining - 7
    if isinstance(key, int):
        return remaining - (int.bit_length(key) * 30103 // 100000 + 5)
    if isinstance(key, float):
        return remaining - 34
    return -1


def _encoding_budget(value: Any, remaining: int, active: set[int]) -> int:
    """Conservative ASCII size bound; stop as soon as a subtree exceeds it.

    No normalized deep copy or cross-call memo is constructed.  The 12-byte
    bound for a Unicode code point includes JSON surrogate-pair escaping.
    """
    if remaining < 0:
        return -1
    kind = type(value)
    if kind is str:
        return remaining - ((6 if value.isascii() else 12) * len(value) + 2)
    if value is None or kind is bool:
        return remaining - 5
    if kind is int:
        return remaining - (value.bit_length() * 30103 // 100000 + 3)
    if kind is float:
        return remaining - 32
    dataclass_instance = False
    if kind not in (dict, list, tuple, set, frozenset):
        if isinstance(value, Path):
            return remaining - (12 * len(str(value)) + 2)
        dataclass_instance = is_dataclass(value) and not isinstance(value, type)
        if not dataclass_instance:
            return -1
    marker = id(value)
    if marker in active:
        return -1
    active.add(marker)
    try:
        remaining -= 2
        if kind is dict:
            # Every item needs at least a quoted key, ':', a scalar and ','.
            if len(value) * 5 > remaining:
                return -1
            for key, item in value.items():
                remaining = _key_budget(key, remaining - 2)
                remaining = _encoding_budget(item, remaining, active)
                if remaining < 0:
                    return -1
        elif dataclass_instance:
            for item in fields(value):
                remaining = _key_budget(item.name, remaining - 2)
                remaining = _encoding_budget(getattr(value, item.name), remaining, active)
                if remaining < 0:
                    return -1
        else:
            if len(value) * 2 > remaining:
                return -1
            for item in value:
                remaining = _encoding_budget(item, remaining - 1, active)
                if remaining < 0:
                    return -1
        return remaining
    finally:
        active.remove(marker)


def _canonical_chunks(value: Any, encoder: _StateEncoder, active: set[int]):
    """Use public JSONEncoder.encode only for bounded complete subtrees."""
    limit = _CANONICAL_CHUNK_ASCII_LIMIT
    if _encoding_budget(value, limit, active) >= 0:
        yield encoder.encode(value)
        return
    if isinstance(value, str):
        # Escape complete code points; slicing cannot cut an escape sequence.
        yield '"'
        step = max(1, (limit - 2) // 12)
        for start in range(0, str.__len__(value), step):
            yield encoder.encode(str.__getitem__(value, slice(start, start + step)))[1:-1]
        yield '"'
        return
    kind = type(value)
    if kind not in (dict, list, tuple) and not (
        (is_dataclass(value) and not isinstance(value, type))
        or isinstance(value, Path) or kind in (set, frozenset)
    ):
        # Preserve the legacy encoder's errors and subclass/custom behavior.
        yield from encoder.iterencode(value)
        return
    marker = id(value)
    if marker in active:
        raise ValueError('Circular reference detected')
    active.add(marker)
    try:
        if kind in (list, tuple):
            yield '['
            batch = []
            remaining = limit - 2
            first = True
            for item in value:
                available = _encoding_budget(item, remaining - 1, active)
                if available < 0:
                    if batch:
                        if not first:
                            yield ','
                        yield encoder.encode(batch)[1:-1]
                        first = False
                        batch = []
                        remaining = limit - 2
                        available = _encoding_budget(item, remaining - 1, active)
                    if available < 0:
                        if not first:
                            yield ','
                        yield from _canonical_chunks(item, encoder, active)
                        first = False
                        continue
                batch.append(item)
                remaining = available
            if batch:
                if not first:
                    yield ','
                yield encoder.encode(batch)[1:-1]
            yield ']'
        elif kind is dict:
            yield '{'
            # Match JSONEncoder's sort-before-key-coercion semantics, including
            # TypeError for incomparable mixed key types. Sorting is inherent
            # to the existing contract; only references are duplicated here.
            items = sorted(value.items())
            batch = {}
            remaining = limit - 2
            first = True
            for key, item in items:
                available = _encoding_budget(item, _key_budget(key, remaining - 2), active)
                if available < 0:
                    if batch:
                        if not first:
                            yield ','
                        yield encoder.encode(batch)[1:-1]
                        first = False
                        batch = {}
                        remaining = limit - 2
                        available = _encoding_budget(item, _key_budget(key, remaining - 2), active)
                    if available < 0:
                        if not first:
                            yield ','
                        if isinstance(key, str):
                            yield from _canonical_chunks(key, encoder, active)
                        else:
                            # A one-key dict delegates exact bool/null/numeric
                            # key spelling and invalid-key errors to stdlib.
                            yield encoder.encode({key: None})[1:-6]
                        yield ':'
                        yield from _canonical_chunks(item, encoder, active)
                        first = False
                        continue
                batch[key] = item
                remaining = available
            if batch:
                if not first:
                    yield ','
                yield encoder.encode(batch)[1:-1]
            yield '}'
        else:
            yield from _canonical_chunks(encoder.default(value), encoder, active)
    finally:
        active.remove(marker)


def canonical_sha256(value: Any) -> str:
    encoder = _StateEncoder(
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )
    digest = hashlib.sha256()
    for chunk in _canonical_chunks(value, encoder, set()):
        digest.update(chunk.encode('ascii'))
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
