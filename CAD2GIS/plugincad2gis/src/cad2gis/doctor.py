"""Offline Accuracy Doctor prompt-package helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_offline_prompt(diagnostics_payload: dict[str, Any]) -> str:
    return (
        "# CAD2GIS Accuracy Doctor\n\n"
        "You are reviewing deterministic CAD-to-GIS diagnostics. Propose structured correction "
        "patches only. Do not request direct GIS mutation.\n\n"
        "Return JSON with this shape:\n\n"
        "```json\n"
        "{\n"
        '  "proposals": [\n'
        "    {\n"
        '      "patch_id": "patch-0001",\n'
        '      "patch_type": "apply_reviewed_label",\n'
        '      "source_handle": "CAD_HANDLE",\n'
        '      "after": {"feature_class": "duct"},\n'
        '      "evidence": {"reviewed_label": "duct"},\n'
        '      "reason": "Evidence-backed reason",\n'
        '      "confidence": 0.8,\n'
        '      "required_checks": ["schema_valid"]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        "Diagnostics payload:\n\n"
        "```json\n"
        f"{json.dumps(diagnostics_payload, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )


def write_empty_proposals(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"proposals": []}, ensure_ascii=False, indent=2), encoding="utf-8")


def write_prompt(path: str | Path, diagnostics_payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_offline_prompt(diagnostics_payload), encoding="utf-8")
