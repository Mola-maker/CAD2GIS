"""Atomic JSON artifact I/O with transparent deterministic gzip support."""

from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path
from typing import Any


def read_json_object(
    path: str | Path, *, max_uncompressed_bytes: int | None = None,
) -> dict[str, Any]:
    artifact = Path(path)
    if artifact.suffix == ".gz":
        with gzip.open(artifact, "rb") as handle:
            payload_bytes = handle.read(
                None if max_uncompressed_bytes is None
                else max_uncompressed_bytes + 1
            )
    else:
        if (
            max_uncompressed_bytes is not None
            and artifact.stat().st_size > max_uncompressed_bytes
        ):
            raise ValueError(
                f"JSON artifact exceeds {max_uncompressed_bytes} bytes: {artifact.name}"
            )
        payload_bytes = artifact.read_bytes()
    if (
        max_uncompressed_bytes is not None
        and len(payload_bytes) > max_uncompressed_bytes
    ):
        raise ValueError(
            "Decompressed JSON artifact exceeds "
            f"{max_uncompressed_bytes} bytes: {artifact.name}"
        )
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact root must be an object: {artifact.name}")
    return payload


def write_json_object(path: str | Path, payload: dict[str, Any]) -> None:
    """Write canonical human-readable JSON, gzip-compressed for ``.gz``."""
    artifact = Path(path)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact.with_name(
        f".{artifact.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    encoded = json.dumps(
        payload, ensure_ascii=False, indent=2,
    ).encode("utf-8")
    try:
        if artifact.suffix == ".gz":
            # mtime=0 keeps compressed artifacts reproducible and therefore
            # content-addressable across identical conversion runs.
            with temporary.open("wb") as raw:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, mtime=0,
                ) as compressed:
                    compressed.write(encoded)
        else:
            temporary.write_bytes(encoded)
        os.replace(temporary, artifact)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = ["read_json_object", "write_json_object"]
