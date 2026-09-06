"""Atomic JSON artifact I/O with transparent deterministic gzip support."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


def inherit_output_permissions(staging: Path) -> None:
    """Restore the output parent's Windows ACL before publishing a new tree.

    Temporary directories can have an owner-only DACL. Renaming preserves it,
    leaving outputs unreadable to the user when a sandbox account created them.
    Reset only the new staging tree; failure must abort publication.
    """
    if os.name != "nt":
        return
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    subprocess.run(
        [str(executable), str(staging), "/reset", "/T", "/Q"],
        capture_output=True, check=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def file_cache_identity(path: str | Path) -> tuple[int, ...]:
    """Invalidate verified-content caches even when Windows mtime is restored.

    Windows stat.ctime is creation time, not metadata change time. Use the
    kernel's ChangeTime. If unavailable, return a fresh token so verification
    fails open only in performance: callers must rehash, never trust stale data.
    """
    artifact = Path(path)
    stat = artifact.stat()
    identity = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino)
    if os.name != "nt":
        return identity
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in (
            "CreationTime", "LastAccessTime", "LastWriteTime", "ChangeTime",
        )] + [("FileAttributes", wintypes.DWORD)]

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_info = kernel.GetFileInformationByHandleEx
        get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        get_info.restype = wintypes.BOOL
        with artifact.open("rb") as stream:
            info = FileBasicInfo()
            handle = msvcrt.get_osfhandle(stream.fileno())
            if get_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)) and info.ChangeTime:
                return (*identity, info.ChangeTime)
    except (OSError, ValueError):
        pass
    return (*identity, time.perf_counter_ns())


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


__all__ = [
    "file_cache_identity", "inherit_output_permissions", "read_json_object", "write_json_object",
]
