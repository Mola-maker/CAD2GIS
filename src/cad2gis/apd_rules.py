"""Compatibility exports for the historical single-drawing APD rules.

Drawing-specific rules are loaded only when explicitly requested. General
coordinate code should import :mod:`cad2gis.coordinate_runtime` directly.
"""

from .coordinate_runtime import set_traditional_axis_order

_LEGACY_EXPORTS = (
    "APD_ANONYMOUS_BLOCKS",
    "classify_insert_block",
    "is_telecom_block",
    "link_annotations",
    "classify_annotation_target",
    "link_apd_annotations",
)
__all__ = [*_LEGACY_EXPORTS, "set_traditional_axis_order"]


def __getattr__(name):
    if name in _LEGACY_EXPORTS:
        from .legacy import apd_rules

        return getattr(apd_rules, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
