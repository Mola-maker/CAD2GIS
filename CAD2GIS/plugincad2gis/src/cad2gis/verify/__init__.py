"""Verification subpackage — the accuracy protocol and labeled benchmarks.

Built FIRST (story G2), before the pipeline, per the independent review: the >=90%
conversion-accuracy claim is only defensible when measured against labeled ground
truth across multiple correctness dimensions.
"""
from .protocol import (  # noqa: F401
    AccuracyReport,
    BenchmarkSpec,
    DimensionScore,
    DIMENSION_WEIGHTS,
    DEFAULT_THRESHOLD,
    score,
)
from .per_feature import PerFeatureVerification, verify_per_feature  # noqa: F401
