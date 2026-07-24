"""Reader contract shared by autocad (legacy) and libredwg (cross-platform).

Defines the v3 reader protocol:
- ``extract_dwg_records(source_path) -> DWGRecordInventory``
- ``DWGRecordInventory``: list-like with ``.diagnostics`` attribute
- diagnostics keys: ``skipped_rows``, ``inventory_complete``,
  ``extraction_backend``, ``metadata_evidence``,
  ``unsupported_reason_counts``
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class ReaderUnavailableError(RuntimeError):
    """The configured reader cannot provide an authoritative inventory."""


@dataclasses.dataclass(frozen=True)
class ReaderCapability:
    """Serializable availability report for an optional reader backend."""

    backend: str
    available: bool
    detail: str
    remediation: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this capability."""
        return dataclasses.asdict(self)


@runtime_checkable
class DWGRecordInventory(Protocol):
    """Flat record inventory with reader-protocol diagnostics attached."""

    diagnostics: dict[str, Any]

    def __iter__(self): ...
    def __len__(self): ...
    def __getitem__(self, idx): ...


@runtime_checkable
class ReaderContract(Protocol):
    """Callable reader boundary used by ingest."""

    def __call__(self, source_path: str | Path) -> DWGRecordInventory: ...
