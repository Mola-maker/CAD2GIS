"""Replay verified native reader facts without starting a CAD reader again."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..reader.contracts import DWGRecordInventory

REPLAY_SCHEMA = "cad2gis.native_reader_replay.v1"


class _ReplayRecords(list):
    def __init__(self, rows, diagnostics):
        super().__init__(rows)
        self.diagnostics = dict(diagnostics)


def reader_identity() -> str:
    """Bind reader adapters and typed projection; normalize checkout line endings."""
    package = Path(__file__).resolve().parents[1]
    files = sorted((package / "reader").glob("*.py")) + [package / "cad2gis_v3/model.py"]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(package).as_posix().encode() + b"\0")
        digest.update(path.read_text(encoding="utf-8").encode("utf-8") + b"\0")
    return digest.hexdigest()


def load_native_snapshot(source_run: Path, source: Path) -> tuple[DWGRecordInventory, dict]:
    from .source_export import _sha256
    from .source_query import validate_source_snapshot

    root, manifest = validate_source_snapshot(source_run)
    provenance = manifest["reader_provenance"]
    if provenance.get("mode") != "native_reader" or provenance.get("authority") != "reader_complete":
        raise ValueError("Conversion replay requires a complete native reader snapshot, not injected records")
    if provenance.get("replay_schema") != REPLAY_SCHEMA or provenance.get("reader_identity") != reader_identity():
        raise ValueError("Source snapshot reader implementation is stale; export-source again before conversion")
    if manifest["source"]["sha256"] != _sha256(source):
        raise ValueError("Source snapshot belongs to a different DWG")
    protocol = provenance.get("diagnostics", {})
    if protocol.get("inventory_complete") is not True or protocol.get("skipped_rows", 0):
        raise ValueError("Source snapshot reader inventory is incomplete")
    artifact = manifest["artifacts"]["reader_records"]
    raw = Path(artifact["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
        raise ValueError("Source snapshot changed while reading its records")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    if len(rows) != manifest["entity_count"] or any(
        not isinstance(row, dict) or not row.get("entity_key")
        or row.get("source_sha256") != manifest["source"]["sha256"] for row in rows
    ):
        raise ValueError("Source snapshot record identity/count mismatch")
    receipt = {"schema_version": REPLAY_SCHEMA, "source_run": str(root),
               "snapshot_sha256": manifest["snapshot_sha256"], "reader_records_sha256": artifact["sha256"],
               "reader_identity": provenance["reader_identity"], "record_count": len(rows), "reader_invoked": False}
    return _ReplayRecords(rows, diagnostics=protocol), receipt
