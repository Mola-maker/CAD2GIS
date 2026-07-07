"""Semantic mapping subpackage (story G6) — CAD entity -> GIS feature class.

Rules-first and deterministic. `MappingEngine` is the authoritative classifier; any
LLM assistance only proposes new rules for human review, never runs in the accuracy path.
"""
from .engine import BlockCode, MappingEngine, MappingResult, Rule  # noqa: F401
