"""Fail-closed CAD drawing-unit and CRS-axis contract.

CAD entity coordinates are expressed in drawing units (``$INSUNITS``), while
PROJ/OSR operations consume coordinates in the source CRS axis unit and emit
coordinates in the target CRS axis unit.  Those three facts are deliberately
kept separate here.  A CRS identifier never supplies missing CAD unit or local
registration evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from pyproj import CRS
from pyproj.exceptions import CRSError


UNIT_CRS_CONTRACT_SCHEMA_VERSION = "cad2gis.unit-crs-contract.v1"


class UnitCrsContractError(ValueError):
    """Raised when conversion would require guessing units or registration."""


@dataclass(frozen=True)
class CadUnit:
    """One supported AutoCAD ``$INSUNITS`` declaration."""

    insunits: int
    name: str
    symbol: str
    metres_per_unit: float

    @property
    def scale_to_m(self) -> float:
        """Compatibility alias used by callers that prefer ``scale`` wording."""
        return self.metres_per_unit

    @property
    def meters_per_unit(self) -> float:
        """US-spelling compatibility alias."""
        return self.metres_per_unit

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "insunits": self.insunits,
            "name": self.name,
            "symbol": self.symbol,
            "metres_per_unit": self.metres_per_unit,
        }


@dataclass(frozen=True)
class CrsAxisUnit:
    """Linear unit declared by a CRS' horizontal axes."""

    name: str
    metres_per_unit: float

    @property
    def scale_to_m(self) -> float:
        return self.metres_per_unit

    @property
    def meters_per_unit(self) -> float:
        return self.metres_per_unit

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metres_per_unit": self.metres_per_unit,
        }


_SUPPORTED_INSUNITS = {
    # AutoCAD $INSUNITS enumeration.  Values not listed here are deliberately
    # rejected until their scale and test fixtures become part of this contract.
    1: CadUnit(1, "inch", "in", 0.0254),
    2: CadUnit(2, "foot", "ft", 0.3048),
    4: CadUnit(4, "millimetre", "mm", 0.001),
    5: CadUnit(5, "centimetre", "cm", 0.01),
    6: CadUnit(6, "metre", "m", 1.0),
}


def resolve_insunits(code: int) -> CadUnit:
    """Resolve a reviewed AutoCAD unit code without accepting unitless input."""
    if isinstance(code, bool) or not isinstance(code, int):
        raise UnitCrsContractError("dwg_insunits must be an integer $INSUNITS code")
    try:
        return _SUPPORTED_INSUNITS[code]
    except KeyError as exc:
        supported = ", ".join(str(item) for item in sorted(_SUPPORTED_INSUNITS))
        raise UnitCrsContractError(
            f"Unsupported or unitless dwg_insunits={code}; supported codes are {supported}"
        ) from exc


def _finite_positive(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise UnitCrsContractError(f"{field_name} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise UnitCrsContractError(f"{field_name} must be a finite positive number")
    return result


def _parse_crs(value: str | None, field_name: str) -> CRS:
    if value is None or not str(value).strip():
        raise UnitCrsContractError(f"{field_name} is required")
    try:
        return CRS.from_user_input(str(value).strip())
    except CRSError as exc:
        raise UnitCrsContractError(f"Invalid {field_name}: {value!r}") from exc


def _linear_axis_unit(crs: CRS, field_name: str) -> CrsAxisUnit:
    axes = tuple(crs.axis_info[:2])
    if len(axes) != 2:
        raise UnitCrsContractError(f"{field_name} must declare two horizontal axes")
    factors = tuple(
        _finite_positive(axis.unit_conversion_factor, f"{field_name} axis unit scale")
        for axis in axes
    )
    if not math.isclose(factors[0], factors[1], rel_tol=1e-12, abs_tol=0.0):
        raise UnitCrsContractError(
            f"{field_name} horizontal axes use different units; conversion is ambiguous"
        )
    names = tuple(str(axis.unit_name or "").strip() for axis in axes)
    if not names[0] or not names[1]:
        raise UnitCrsContractError(f"{field_name} has an unnamed horizontal axis unit")
    name = names[0] if names[0].casefold() == names[1].casefold() else f"{names[0]}/{names[1]}"
    return CrsAxisUnit(name=name, metres_per_unit=factors[0])


def _crs_token(crs: CRS) -> str:
    authority = crs.to_authority()
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_string()


@dataclass(frozen=True)
class UnitCrsContract:
    """Reviewed bridge from CAD drawing coordinates to delivery coordinates."""

    schema_version: str
    cad_unit: CadUnit
    source_crs: str | None
    target_crs: str
    source_crs_kind: str
    source_crs_axis_unit: CrsAxisUnit | None
    target_crs_axis_unit: CrsAxisUnit
    source_coordinate_scale_to_m: float
    source_coordinate_scale_reviewed: bool
    source_coordinate_scale_origin: str
    source_to_crs_axis_factor: float | None
    coordinate_mode: str
    local_registration_strategy: str | None
    local_registration_reviewed: bool

    @property
    def dwg_insunits(self) -> int:
        return self.cad_unit.insunits

    @property
    def source_geometry_unit(self) -> CadUnit:
        return self.cad_unit

    @property
    def target_coordinate_scale_to_m(self) -> float:
        return self.target_crs_axis_unit.metres_per_unit

    @property
    def can_direct_transform(self) -> bool:
        return self.coordinate_mode == "direct_crs"

    def source_length_to_m(self, value: float) -> float:
        return float(value) * self.source_coordinate_scale_to_m

    def target_length_to_m(self, value: float) -> float:
        return float(value) * self.target_crs_axis_unit.metres_per_unit

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": "reviewed" if self.can_direct_transform else "registration_required",
            "coordinate_mode": self.coordinate_mode,
            "source_geometry_unit": self.cad_unit.to_manifest_dict(),
            "source_coordinate_scale_to_m": self.source_coordinate_scale_to_m,
            "source_coordinate_scale_reviewed": self.source_coordinate_scale_reviewed,
            "source_coordinate_scale_origin": self.source_coordinate_scale_origin,
            "source_crs": self.source_crs,
            "source_crs_kind": self.source_crs_kind,
            "source_crs_axis_unit": (
                None
                if self.source_crs_axis_unit is None
                else self.source_crs_axis_unit.to_manifest_dict()
            ),
            "source_to_crs_axis_factor": self.source_to_crs_axis_factor,
            "target_crs": self.target_crs,
            "target_crs_axis_unit": self.target_crs_axis_unit.to_manifest_dict(),
            "local_registration_strategy": self.local_registration_strategy,
            "local_registration_reviewed": self.local_registration_reviewed,
            "provenance": {
                "dwg_insunits": "DWG_DIRECT:$INSUNITS",
                "source_coordinate_scale": self.source_coordinate_scale_origin,
                "source_crs": "PROJECT_PROFILE:reviewed",
                "target_crs": "PROJECT_PROFILE:reviewed",
                "local_registration": (
                    "PROJECT_PROFILE:reviewed-authoritative-registration"
                    if self.local_registration_reviewed
                    else None
                ),
            },
        }

    def as_dict(self) -> dict[str, Any]:
        return self.to_manifest_dict()


def build_unit_crs_contract(
    dwg_insunits,
    source_crs,
    target_crs,
    source_coordinate_scale_to_m=None,
    source_coordinate_scale_reviewed=False,
    local_registration_strategy=None,
    local_registration_reviewed=False,
) -> UnitCrsContract:
    """Build a unit/CRS contract, rejecting every implicit scale or CRS guess.

    A metre drawing may safely derive its identity scale from ``$INSUNITS=6``.
    Every other supported drawing unit requires an explicit reviewed scale and
    that scale must agree with the AutoCAD enumeration.  Projected source CRSs
    can then be used directly.  Missing, geographic, engineering/local, or
    otherwise unusable source CRSs require an explicitly reviewed registration
    strategy; the returned registration contract is evidence for that separate
    stage and cannot be passed off as a direct CRS operation.
    """
    cad_unit = resolve_insunits(dwg_insunits)
    if not isinstance(source_coordinate_scale_reviewed, bool):
        raise UnitCrsContractError("source_coordinate_scale_reviewed must be boolean")
    if not isinstance(local_registration_reviewed, bool):
        raise UnitCrsContractError("local_registration_reviewed must be boolean")

    if source_coordinate_scale_to_m is None:
        if cad_unit.metres_per_unit != 1.0:
            raise UnitCrsContractError(
                "Non-metre CAD drawing units require explicit "
                "source_coordinate_scale_to_m and source_coordinate_scale_reviewed=true"
            )
        scale_to_m = 1.0
        scale_origin = "DWG_DIRECT:$INSUNITS-6-metre-identity"
    else:
        scale_to_m = _finite_positive(
            source_coordinate_scale_to_m, "source_coordinate_scale_to_m"
        )
        if not math.isclose(
            scale_to_m, cad_unit.metres_per_unit, rel_tol=1e-12, abs_tol=0.0
        ):
            raise UnitCrsContractError(
                "source_coordinate_scale_to_m does not match dwg_insunits "
                f"({scale_to_m!r} != {cad_unit.metres_per_unit!r})"
            )
        if cad_unit.metres_per_unit != 1.0 and not source_coordinate_scale_reviewed:
            raise UnitCrsContractError(
                "Non-metre CAD drawing scale must be explicitly reviewed"
            )
        scale_origin = (
            "PROJECT_PROFILE:reviewed-source-coordinate-scale"
            if source_coordinate_scale_reviewed
            else "DWG_DIRECT:$INSUNITS-6-metre-identity"
        )

    registration_strategy = None
    if local_registration_strategy is not None:
        if not isinstance(local_registration_strategy, str) or not local_registration_strategy.strip():
            raise UnitCrsContractError(
                "local_registration_strategy must be null or a non-empty string"
            )
        registration_strategy = local_registration_strategy.strip()
    if local_registration_reviewed and registration_strategy is None:
        raise UnitCrsContractError(
            "local_registration_reviewed=true requires local_registration_strategy"
        )
    if registration_strategy is not None and not local_registration_reviewed:
        raise UnitCrsContractError(
            "local_registration_strategy requires local_registration_reviewed=true"
        )

    target = _parse_crs(target_crs, "target_crs")
    if not target.is_projected:
        raise UnitCrsContractError(
            "target_crs must be a projected CRS with linear axes; geographic targets "
            "cannot satisfy metric delivery-length fields"
        )
    target_axis = _linear_axis_unit(target, "target_crs")
    target_token = _crs_token(target)

    source = None
    source_failure = None
    if source_crs is not None and str(source_crs).strip():
        try:
            source = CRS.from_user_input(str(source_crs).strip())
        except CRSError as exc:
            source_failure = exc

    if source is not None and source.is_projected:
        if registration_strategy is not None:
            raise UnitCrsContractError(
                "local_registration_strategy is only valid when source_crs is "
                "missing, geographic, or local/engineering"
            )
        source_axis = _linear_axis_unit(source, "source_crs")
        source_to_axis = scale_to_m / source_axis.metres_per_unit
        if not math.isfinite(source_to_axis) or source_to_axis <= 0.0:
            raise UnitCrsContractError("source-to-CRS-axis scale is invalid")
        return UnitCrsContract(
            schema_version=UNIT_CRS_CONTRACT_SCHEMA_VERSION,
            cad_unit=cad_unit,
            source_crs=_crs_token(source),
            target_crs=target_token,
            source_crs_kind="projected",
            source_crs_axis_unit=source_axis,
            target_crs_axis_unit=target_axis,
            source_coordinate_scale_to_m=scale_to_m,
            source_coordinate_scale_reviewed=source_coordinate_scale_reviewed,
            source_coordinate_scale_origin=scale_origin,
            source_to_crs_axis_factor=source_to_axis,
            coordinate_mode="direct_crs",
            local_registration_strategy=None,
            local_registration_reviewed=False,
        )

    if registration_strategy is None:
        if source_failure is not None:
            raise UnitCrsContractError(
                f"Invalid source_crs: {source_crs!r}; a reviewed authoritative local "
                "registration is required"
            ) from source_failure
        if source is None:
            description = "missing"
        elif source.is_geographic:
            description = "geographic"
        elif source.is_engineering:
            description = "engineering/local"
        else:
            description = "non-projected"
        raise UnitCrsContractError(
            f"{description} source_crs cannot be guessed; provide a reviewed "
            "authoritative local registration"
        )

    if source is None:
        source_kind = "missing" if source_failure is None else "unresolved_local"
        source_token = None if source_crs is None else str(source_crs).strip() or None
        source_axis = None
    else:
        source_kind = "geographic" if source.is_geographic else (
            "engineering_local" if source.is_engineering else "non_projected"
        )
        source_token = _crs_token(source)
        # Geographic angular axes intentionally do not get represented as a
        # linear scale.  Engineering CRS axes are useful evidence but are not
        # sufficient to authorize a direct CRS operation.
        source_axis = _linear_axis_unit(source, "source_crs") if source.is_engineering else None

    return UnitCrsContract(
        schema_version=UNIT_CRS_CONTRACT_SCHEMA_VERSION,
        cad_unit=cad_unit,
        source_crs=source_token,
        target_crs=target_token,
        source_crs_kind=source_kind,
        source_crs_axis_unit=source_axis,
        target_crs_axis_unit=target_axis,
        source_coordinate_scale_to_m=scale_to_m,
        source_coordinate_scale_reviewed=source_coordinate_scale_reviewed,
        source_coordinate_scale_origin=scale_origin,
        source_to_crs_axis_factor=None,
        coordinate_mode="reviewed_authoritative_registration",
        local_registration_strategy=registration_strategy,
        local_registration_reviewed=True,
    )


__all__ = [
    "CadUnit",
    "CrsAxisUnit",
    "UnitCrsContract",
    "UnitCrsContractError",
    "UNIT_CRS_CONTRACT_SCHEMA_VERSION",
    "build_unit_crs_contract",
    "resolve_insunits",
]
