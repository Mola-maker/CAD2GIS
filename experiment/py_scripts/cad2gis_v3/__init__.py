"""Evidence-first APD direct-DWG conversion package.

The public conversion symbols are loaded lazily so importing the independent
curation lane cannot import GIS/conversion code (and vice versa).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .pipeline import ConversionRequest, ConversionResult, convert

__all__ = ["ConversionRequest", "ConversionResult", "convert"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
