"""Public verification APIs.

The verification package is intentionally dependency-free and read-only.  It
can therefore be used by ``cad2gis verify`` before optional GDAL/QGIS
dependencies are installed.
"""

from .claims import (
    CLAIM_CROSS_ABSOLUTE,
    CLAIM_CROSS_FIDELITY,
    CLAIM_CROSS_NOMINAL_CRS,
    CLAIM_DIMENSION_ONLY,
    CLAIM_INVENTORY_ONLY,
    CLAIM_SINGLE_ABSOLUTE,
    CLAIM_SINGLE_FIDELITY,
    CLAIM_SINGLE_NOMINAL_CRS,
    CORE_FIDELITY_DIMENSIONS,
    strongest_allowed_claim,
)
from .matrix import (
    DIMENSIONS,
    MATRIX_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    SUPPORTED_MATRIX_SCHEMAS,
    STATUSES,
    VerificationMatrixError,
    evaluate_matrix,
)

__all__ = [
    "CLAIM_CROSS_ABSOLUTE",
    "CLAIM_CROSS_FIDELITY",
    "CLAIM_CROSS_NOMINAL_CRS",
    "CLAIM_DIMENSION_ONLY",
    "CLAIM_INVENTORY_ONLY",
    "CLAIM_SINGLE_ABSOLUTE",
    "CLAIM_SINGLE_FIDELITY",
    "CLAIM_SINGLE_NOMINAL_CRS",
    "CORE_FIDELITY_DIMENSIONS",
    "DIMENSIONS",
    "MATRIX_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "SUPPORTED_MATRIX_SCHEMAS",
    "STATUSES",
    "VerificationMatrixError",
    "evaluate_matrix",
    "strongest_allowed_claim",
]
