#!/usr/bin/env python3
"""Read-only LibreDWG INSERT / BLOCK_HEADER field probe.

Issue 4 (``docs/INSERT_TRANSFORM_FACTS_AND_BLOCK_ATTRIBUTES_SPEC.md``)
Phase 0.  This tool never changes production code paths; it loads the same
ctypes bridge as ``cad2gis.reader.libredwg``, traverses raw LibreDWG objects
for every DWG passed on the command line, cross-checks the ``dwgread -O json``
side channel, and writes a Markdown report to ``.omc`` (or ``--out``).

For every ``DWG_TYPE_INSERT`` it records:

* ``entity.tio.INSERT.ins_pt.x/y/z``
* ``entity.tio.INSERT.scale.x/y/z``
* ``entity.tio.INSERT.rotation``
* ``entity.tio.INSERT.extrusion`` / ``normal``
* owner handle and block-header reference

For every ``DWG_TYPE_BLOCK_HEADER`` it records:

* ``name``
* ``base_pt.x/y/z`` (the dynapi field is ``base_pt``, not ``base_point``)

Exit code 0 even when some facts are unreadable: this is a probe, not a gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cad2gis.reader import libredwg as lib


@dataclass
class InsertSample:
    handle: int
    owner: int | None
    entmode: int | None
    ins_pt: Any
    scale: Any
    rotation: Any
    extrusion: Any
    block_header_ref: int | None
    block_name: str
    errors: list[str] = field(default_factory=list)


@dataclass
class HeaderSample:
    handle: int
    name: str
    base_pt: Any
    errors: list[str] = field(default_factory=list)


def _ref_absolute(ref: Any) -> int | None:
    if ref is None:
        return None
    for attr in ("absolute_ref", "obj"):
        try:
            value = getattr(ref, attr)
        except Exception:
            continue
        if value is None:
            continue
        try:
            handle = getattr(value, "handle", None)
            if handle is not None:
                return int(handle.value)
        except Exception:
            continue
    try:
        return int(ref.absolute_ref)
    except Exception:
        return None


def _raw_value(value: Any) -> Any:
    """Return a JSON-safe scalar / list snapshot for a Swig struct value."""
    if value is None:
        return None
    if isinstance(value, (bool, int, str)) and not isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return str(value)
    if hasattr(value, "x"):
        try:
            return [float(value.x), float(value.y), float(value.z)]
        except Exception:
            return str(value)
    try:
        return float(value)
    except Exception:
        return str(value)


def _probe_dwg(source: Path, out_dir: Path) -> dict[str, Any]:
    lib._require_libredwg()
    LibreDWG = lib.LibreDWG
    Dwg_Data = LibreDWG.Dwg_Data
    Dwg_Object_Array_getitem = LibreDWG.Dwg_Object_Array_getitem
    new_Dwg_Object_Array = LibreDWG.new_Dwg_Object_Array
    dwg_read_file = LibreDWG.dwg_read_file
    DWG_SUPERTYPE_ENTITY = LibreDWG.DWG_SUPERTYPE_ENTITY
    DWG_TYPE_INSERT = LibreDWG.DWG_TYPE_INSERT
    DWG_TYPE_BLOCK_HEADER = LibreDWG.DWG_TYPE_BLOCK_HEADER

    source = source.resolve()
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    data = Dwg_Data()
    data.object = new_Dwg_Object_Array(500000)
    read_err = dwg_read_file(str(source), data)

    inserts: list[InsertSample] = []
    headers: list[HeaderSample] = []
    header_by_handle: dict[int, HeaderSample] = {}

    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue

        if obj.type == DWG_TYPE_BLOCK_HEADER:
            try:
                bh = obj.tio.object.tio.BLOCK_HEADER
                ptr = int(bh.this)
                name = lib._entity_utf8_text(ptr, "BLOCK_HEADER", "name")
            except Exception:
                name = ""
            base_error = ""
            try:
                base_pt = _raw_value(bh.base_pt)
            except Exception as exc:
                base_pt = None
                base_error = f"base_pt_unreadable[{type(exc).__name__}]"
            header = HeaderSample(
                handle=obj.handle.value,
                name=name,
                base_pt=base_pt,
                errors=[] if base_pt is not None else [base_error],
            )
            headers.append(header)
            header_by_handle[header.handle] = header
            continue

        if obj.supertype != DWG_SUPERTYPE_ENTITY or obj.type != DWG_TYPE_INSERT:
            continue

        errors: list[str] = []
        try:
            entity = obj.tio.entity
            ent = entity.tio.INSERT
            owner = _ref_absolute(getattr(entity, "ownerhandle", None))
            entmode = getattr(entity, "entmode", None)
        except Exception as exc:
            errors.append(f"entity_unreadable[{type(exc).__name__}]")
            continue

        try:
            ins_pt = _raw_value(ent.ins_pt)
        except Exception as exc:
            ins_pt = None
            errors.append(f"ins_pt_unreadable[{type(exc).__name__}]")
        try:
            scale = _raw_value(ent.scale)
        except Exception as exc:
            scale = None
            errors.append(f"scale_unreadable[{type(exc).__name__}]")
        try:
            rotation_value = getattr(ent, "rotation", None)
            if rotation_value is None:
                raise AttributeError("INSERT.rotation is None")
            rotation = _raw_value(rotation_value)
        except Exception as exc:
            rotation = None
            errors.append(f"rotation_unreadable[{type(exc).__name__}]")
        try:
            extrusion_value = getattr(ent, "extrusion", None)
            if extrusion_value is None:
                raise AttributeError("INSERT.extrusion is None")
            extrusion = _raw_value(extrusion_value)
        except Exception as exc:
            extrusion = None
            errors.append(f"extrusion_unreadable[{type(exc).__name__}]")
        try:
            normal_value = getattr(ent, "normal", None)
            normal = _raw_value(normal_value)
        except Exception as exc:
            normal = None
            errors.append(f"normal_unreadable[{type(exc).__name__}]")

        try:
            block_ref = getattr(ent, "block_header", None)
            block_header_ref = _ref_absolute(block_ref)
        except Exception as exc:
            block_header_ref = None
            errors.append(f"block_header_unreadable[{type(exc).__name__}]")

        block_name = ""
        if block_header_ref is not None:
            try:
                block_name = lib._entity_utf8_text(
                    int(
                        block_ref.obj.tio.object.tio.BLOCK_HEADER.this
                    ),
                    "BLOCK_HEADER",
                    "name",
                )
            except Exception as exc:
                errors.append(f"block_name_unreadable[{type(exc).__name__}]")

        inserts.append(InsertSample(
            handle=obj.handle.value,
            owner=owner,
            entmode=int(entmode) if entmode is not None else None,
            ins_pt=ins_pt,
            scale=scale,
            rotation=rotation,
            extrusion=extrusion,
            block_header_ref=block_header_ref,
            block_name=block_name,
            errors=errors,
        ))

    # Cross-check with dwgread JSON (same side channel as the reader).
    json_inserts = 0
    json_inserts_with_extrusion = 0
    json_headers = 0
    json_headers_with_base_pt = 0
    try:
        proc = subprocess.run(
            ["dwgread", "-O", "json", str(source)],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            doc = json.loads(proc.stdout.decode("utf-8", errors="replace"))

            def _walk(node: Any) -> None:
                nonlocal json_inserts, json_inserts_with_extrusion
                nonlocal json_headers, json_headers_with_base_pt
                if isinstance(node, dict):
                    entity = node.get("entity")
                    obj_kind = node.get("object")
                    if entity == "INSERT":
                        json_inserts += 1
                        if isinstance(node.get("extrusion"), list):
                            json_inserts_with_extrusion += 1
                    if obj_kind == "BLOCK_HEADER":
                        json_headers += 1
                        if isinstance(node.get("base_pt"), list):
                            json_headers_with_base_pt += 1
                    for value in node.values():
                        _walk(value)
                elif isinstance(node, list):
                    for value in node:
                        _walk(value)

            _walk(doc)
    except Exception:
        pass

    anon_names, _dimension_texts = lib._read_anon_block_names_json(
        source, source_sha256,
    )

    def _fact_counts(items: list[Any]) -> Counter:
        counts: Counter = Counter()
        for item in items:
            if item is None:
                counts["unavailable"] += 1
            elif isinstance(item, list):
                counts["available"] += 1
            elif isinstance(item, (int, float)):
                counts["available"] += 1
            elif isinstance(item, str):
                counts["available" if item else "unavailable"] += 1
            else:
                counts["unavailable"] += 1
        return counts

    model_inserts = [
        item for item in inserts if item.entmode == 2
    ]
    nested_inserts = [
        item for item in inserts if item.entmode == 0
    ]

    report: dict[str, Any] = {
        "source": str(source),
        "source_sha256": source_sha256,
        "libredwg_read_error": read_err,
        "total_objects": int(data.num_objects),
        "insert_count": len(inserts),
        "model_insert_count": len(model_inserts),
        "block_definition_insert_count": len(nested_inserts),
        "block_header_count": len(headers),
        "availability": {
            "model_inserts": {
                "ins_pt_xyz": dict(_fact_counts([
                    item.ins_pt for item in model_inserts
                ])),
                "scale_xyz": dict(_fact_counts([
                    item.scale for item in model_inserts
                ])),
                "rotation": dict(_fact_counts([
                    item.rotation for item in model_inserts
                ])),
                "extrusion": dict(_fact_counts([
                    item.extrusion for item in model_inserts
                ])),
                "block_header_ref": dict(_fact_counts([
                    item.block_header_ref for item in model_inserts
                ])),
                "block_name": dict(_fact_counts([
                    item.block_name for item in model_inserts
                ])),
            },
            "nested_inserts": {
                "ins_pt_xyz": dict(_fact_counts([
                    item.ins_pt for item in nested_inserts
                ])),
                "scale_xyz": dict(_fact_counts([
                    item.scale for item in nested_inserts
                ])),
                "rotation": dict(_fact_counts([
                    item.rotation for item in nested_inserts
                ])),
                "extrusion": dict(_fact_counts([
                    item.extrusion for item in nested_inserts
                ])),
                "block_header_ref": dict(_fact_counts([
                    item.block_header_ref for item in nested_inserts
                ])),
                "block_name": dict(_fact_counts([
                    item.block_name for item in nested_inserts
                ])),
            },
            "block_headers": {
                "name": dict(_fact_counts([
                    item.name for item in headers
                ])),
                "base_pt_xyz": dict(_fact_counts([
                    item.base_pt for item in headers
                ])),
            },
        },
        "model_insert_samples": [
            {
                "handle": item.handle,
                "owner_handle": item.owner,
                "block_header_handle": item.block_header_ref,
                "block_name": item.block_name,
                "ins_pt": item.ins_pt,
                "scale": item.scale,
                "rotation": item.rotation,
                "extrusion": item.extrusion,
                "errors": item.errors,
            }
            for item in model_inserts[:8]
        ],
        "nested_insert_samples": [
            {
                "handle": item.handle,
                "owner_handle": item.owner,
                "block_header_handle": item.block_header_ref,
                "block_name": item.block_name,
                "ins_pt": item.ins_pt,
                "scale": item.scale,
                "rotation": item.rotation,
                "extrusion": item.extrusion,
                "errors": item.errors,
            }
            for item in nested_inserts[:8]
        ],
        "block_header_samples": [
            {
                "handle": item.handle,
                "name": item.name,
                "base_pt": item.base_pt,
                "errors": item.errors,
            }
            for item in headers[:12]
        ],
        "unreadable_inserts": [
            {
                "handle": item.handle,
                "block_name": item.block_name,
                "errors": item.errors,
            }
            for item in inserts
            if item.errors
        ][:20],
        "side_channel": {
            "dwgread_json_inserts": json_inserts,
            "dwgread_json_inserts_with_extrusion": json_inserts_with_extrusion,
            "dwgread_json_block_headers": json_headers,
            "dwgread_json_block_headers_with_base_pt": json_headers_with_base_pt,
            "anon_block_names_resolved": len(anon_names),
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{source.stem}.insert_probe.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    report["json_report"] = str(json_path)
    return report


def _markdown_line(name: str, report: dict[str, Any]) -> str:
    availability = report["availability"]
    model = availability["model_inserts"]
    nested = availability["nested_inserts"]
    headers = availability["block_headers"]

    def avail(mapping: dict[str, dict[str, int]]) -> str:
        parts = []
        for key, counts in mapping.items():
            parts.append(f"{key}={counts.get('available', 0)}/{sum(counts.values())}")
        return ", ".join(parts)

    lines = [
        f"### {name}",
        "",
        f"- source: `{report['source']}`",
        f"- LibreDWG read error: `{report['libredwg_read_error']}`",
        f"- objects: {report['total_objects']}; INSERT: {report['insert_count']}"
        f" (model {report['model_insert_count']}, nested "
        f"{report['block_definition_insert_count']}); BLOCK_HEADER: "
        f"{report['block_header_count']}",
        f"- model INSERT availability: {avail(model)}",
        f"- nested INSERT availability: {avail(nested)}",
        f"- BLOCK_HEADER availability: {avail(headers)}",
        f"- dwgread side channel: INSERT {report['side_channel']['dwgread_json_inserts']}"
        f" (extrusion {report['side_channel']['dwgread_json_inserts_with_extrusion']}),"
        f" BLOCK_HEADER {report['side_channel']['dwgread_json_block_headers']}"
        f" (base_pt {report['side_channel']['dwgread_json_block_headers_with_base_pt']}),"
        f" anonymous names {report['side_channel']['anon_block_names_resolved']}",
        f"- raw JSON: `{report['json_report']}`",
        "",
        "| kind | handle | owner | block header | name | ins_pt | scale | rotation | extrusion | errors |",
        "|---|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for sample in (
        report["model_insert_samples"][:3]
        + report["nested_insert_samples"][:3]
    ):
        lines.append(
            f"| INSERT | {sample['handle']} | {sample['owner_handle']} | "
            f"{sample['block_header_handle']} | {sample['block_name']} | "
            f"{sample['ins_pt']} | {sample['scale']} | {sample['rotation']} | "
            f"{sample['extrusion']} | {'; '.join(sample['errors'])} |"
        )
    for sample in report["block_header_samples"][:3]:
        lines.append(
            f"| BLOCK_HEADER | {sample['handle']} | | | "
            f"{sample['name']} | base_pt={sample['base_pt']} | | | | "
            f"{'; '.join(sample['errors'])} |"
        )
    if report["unreadable_inserts"]:
        lines.append("")
        lines.append(f"Unreadable INSERT sample count (capped 20): "
                     f"{len(report['unreadable_inserts'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "sources", nargs="+", type=Path,
        help="One or more DWG files to probe.",
    )
    parser.add_argument(
        "--out", type=Path,
        default=Path(".omc/libredwg_insert_probe.md"),
        help="Markdown report path (default: .omc/libredwg_insert_probe.md)",
    )
    arguments = parser.parse_args()

    reports = [_probe_dwg(source, arguments.out.parent) for source in arguments.sources]
    lines = [
        "# LibreDWG INSERT transform-fact probe (Phase 0)",
        "",
        "Read-only field evidence for issue 4.  Production code is unchanged.",
        "",
    ]
    for report in reports:
        lines.append(_markdown_line(report["source"], report))
        lines.append("")
    try:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        print("\n".join(lines))
        return 1
    print(f"probe report: {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
