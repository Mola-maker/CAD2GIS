"""Public C encoding must preserve the legacy canonical state byte stream."""

from __future__ import annotations

import hashlib
import json
import math
import tracemalloc
from dataclasses import dataclass, fields, is_dataclass
from enum import IntEnum
from pathlib import Path, PurePosixPath

import pytest

from cad2gis.cad2gis_v3 import stage_contract as stage


class LegacyStateEncoder(json.JSONEncoder):
    def default(self, value):
        if is_dataclass(value) and not isinstance(value, type):
            return {item.name: getattr(value, item.name) for item in fields(value)}
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (set, frozenset)):
            return sorted(value, key=legacy_sha256)
        return super().default(value)


def encoder(cls):
    return cls(
        ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def legacy_sha256(value):
    digest = hashlib.sha256()
    for chunk in encoder(LegacyStateEncoder).iterencode(value):
        digest.update(chunk.encode("ascii"))
    return digest.hexdigest()


def assert_same_bytes(value):
    expected = "".join(encoder(LegacyStateEncoder).iterencode(value))
    chunks = list(stage._canonical_chunks(value, encoder(stage._StateEncoder), set()))
    assert "".join(chunks) == expected
    assert (
        stage.canonical_sha256(value)
        == hashlib.sha256(expected.encode("ascii")).hexdigest()
    )
    return chunks


@dataclass(frozen=True)
class FrozenPoint:
    x: float
    label: str


@dataclass
class Record:
    path: Path
    points: tuple
    flags: set
    metadata: dict


class Number(IntEnum):
    VALUE = 7


class IteratedList(list):
    def __iter__(self):
        return iter(("custom iterator", 7))


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        -1,
        10**4000,
        -0.0,
        1.0,
        1e-16,
        1e200,
        math.nextafter(0.0, 1.0),
        '引号" backslash\\ control\x00\n\t 😀\ud800\udfff\u2028',
        Path("测试/path with spaces"),
        (),
        [],
        {},
        set(),
        frozenset(),
        {"a": [1, (2, False, None)], "z": {"嵌套": "\ud800"}},
        {2: "second", -1: "first", 0.5: "float", False: "boolean"},
        {None: "null key"},
        {True: "bool key"},
        {-0.0: "negative zero key"},
        {"one", 2, ("pair", 3), Path("relative")},
        frozenset([FrozenPoint(1.25, "甲"), FrozenPoint(-0.0, "乙")]),
        Record(
            Path("source.dwg"), ((1.0, 2.0), (3.0, -0.0)), {"a", "b"}, {"full": True}
        ),
        Number.VALUE,
        {Number.VALUE: "enum key"},
        IteratedList([1, 2, 3]),
    ],
)
def test_canonical_byte_compatibility_for_supported_types(value):
    assert_same_bytes(value)


def test_bounded_batches_preserve_late_data_and_complete_structure():
    shared = {"vertex": (1.0, -0.0), "text": "中文\\label"}
    rows = [{"key": i, "facts": shared, "optional": [None, False]} for i in range(2200)]
    state = {"rows": rows, "lookup": {i: row for i, row in enumerate(rows)}}
    chunks = assert_same_bytes(state)
    assert max(map(len, chunks)) <= stage._CANONICAL_CHUNK_ASCII_LIMIT
    before = stage.canonical_sha256(state)
    rows[-1]["optional"][-1] = True
    assert stage.canonical_sha256(state) != before
    assert stage.canonical_sha256(state) == legacy_sha256(state)


def test_string_escaping_is_exact_across_chunk_boundaries():
    value = '😀\ud800\udfff\x00\n\\"中文' * 9000
    state = {value: [value, value]}
    chunks = assert_same_bytes(state)
    assert max(map(len, chunks)) <= stage._CANONICAL_CHUNK_ASCII_LIMIT


@pytest.mark.parametrize(
    "value",
    [
        object(),
        PurePosixPath("unsupported-pure-path"),
        float("nan"),
        float("inf"),
        -float("inf"),
        {"value": float("nan")},
        {float("nan"): "key"},
        {1: "numeric", "1": "string"},
        {None: "null", 1: "int"},
        {(1, 2): "tuple key"},
        {"large": list(range(40000)), "invalid": object()},
        {"large": list(range(40000)), "invalid": [float("inf")]},
    ],
)
def test_unsupported_values_preserve_legacy_failure_class(value):
    with pytest.raises((TypeError, ValueError)) as old:
        legacy_sha256(value)
    with pytest.raises(type(old.value)):
        stage.canonical_sha256(value)


@pytest.mark.parametrize("kind", ["list", "dict", "dataclass"])
def test_cycles_still_fail_without_publishing_a_digest(kind):
    if kind == "list":
        value = [list(range(20000))]
        value.append(value)
    elif kind == "dict":
        value = {"large": list(range(20000))}
        value["self"] = value
    else:
        value = Record(Path("a"), (), set(), {})
        value.metadata["self"] = value
    with pytest.raises(ValueError, match="Circular reference"):
        legacy_sha256(value)
    with pytest.raises(ValueError, match="Circular reference"):
        stage.canonical_sha256(value)


def test_large_sequence_peak_memory_does_not_scale_with_json_document_size():
    payload = {"text": "x" * 8192, "points": [(1.5, -0.0), (2.5, 3.5)]}
    small = [payload] * 32
    large = [payload] * 4096  # More than 32 MiB of JSON, allocated before tracing.

    def peak(value):
        tracemalloc.start()
        try:
            digest = stage.canonical_sha256(value)
            return digest, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    small_hash, small_peak = peak(small)
    large_hash, large_peak = peak(large)
    assert small_hash != large_hash
    assert large_peak < 1024 * 1024
    assert large_peak < small_peak + 512 * 1024
    assert large_hash == legacy_sha256(large)


@pytest.mark.parametrize(
    "key", [None, True, False, -17, 1.25, -0.0, Number.VALUE, "😀\ud800"]
)
def test_oversize_object_members_keep_stdlib_key_spelling(monkeypatch, key):
    monkeypatch.setattr(stage, "_CANONICAL_CHUNK_ASCII_LIMIT", 128)
    chunks = assert_same_bytes(
        {key: {"text": "long\\\n" * 100, "tuple": (1, None, False)}}
    )
    assert max(map(len, chunks)) <= 128
