"""Domain packs: the delivery vocabulary a conversion targets.

The conversion core (ingest, plan domain, geometry, topology mechanics,
georeferencing, evidence and publication) is domain-neutral. What a drawing
*means* — which delivery layers exist, which class carries network routes,
which classes are supports or nodes — belongs to a domain pack.

``FTTH_APD`` is the only pack today and reproduces the FiberHome FTTH
eight-layer contract exactly. Core modules read roles from the pack instead
of spelling class names. Not yet moved behind the pack (still FTTH-specific
code): the classification rules in ``semantics.py``, the field schemas in
``schema_config.py`` and the symbology in ``styles.py``. See
``docs/DOMAIN_PACKS.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class DomainPack:
    name: str
    description: str
    # Ordered delivery layers. Optional layers are written only when emitted,
    # inserted immediately before ``optional_layers_before``.
    delivery_layers: tuple[str, ...]
    optional_layers: tuple[str, ...]
    optional_layers_before: str
    # Class that carries network routes (polylines split into delivery segments).
    route_class: str
    # Point classes a route endpoint may bridge to.
    route_support_classes: frozenset[str]
    # Support class that must never be promoted to an optical/network node.
    passive_support_class: str
    # Topology roles: supports, distribution boxes and central sites.
    support_class: str
    box_class: str
    site_class: str
    # Point classes whose extent defines the required GCP coverage envelope.
    control_coverage_classes: frozenset[str]
    # Generic semantic roles a committed semantic revision may confirm.
    role_classes: Mapping[str, frozenset[str]]

    def classes_for(self, role: str) -> frozenset[str]:
        return self.role_classes.get(role, frozenset())


FTTH_APD = DomainPack(
    name="ftth_apd",
    description="FiberHome FTTH As-Plan-Drawing delivery (eight-layer GeoPackage contract)",
    delivery_layers=("SITE", "BOITE", "PTECH", "IMB", "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO"),
    optional_layers=("EMR",),
    optional_layers_before="IMB",
    route_class="CABLE",
    route_support_classes=frozenset({"PTECH", "EMR"}),
    passive_support_class="PTECH",
    support_class="PTECH",
    box_class="BOITE",
    site_class="SITE",
    control_coverage_classes=frozenset({"PTECH", "BOITE", "SITE", "EMR"}),
    role_classes=MappingProxyType({
        "CENTRAL_SITE": frozenset({"SITE"}),
        "SUPPORT": frozenset({"PTECH"}),
        "DISTRIBUTION_NODE": frozenset({"BOITE"}),
        "ACCESS_NODE": frozenset({"BOITE"}),
        "PREMISE": frozenset({"IMB"}),
        "NETWORK_ROUTE": frozenset({"CABLE"}),
        "NETWORK_SEGMENT": frozenset({"CABLE"}),
        "ZONE": frozenset({"ZPM", "ZNRO"}),
    }),
)

_PACKS = {FTTH_APD.name: FTTH_APD}
# "auto" and "generic" select the only implemented pack; the request records
# the requested mode separately, so this never claims a generic delivery.
_ALIASES = {"auto": FTTH_APD.name, "generic": FTTH_APD.name}


def get_domain_pack(name: str = "auto") -> DomainPack:
    key = _ALIASES.get(name, name)
    try:
        return _PACKS[key]
    except KeyError:
        raise ValueError(f"Unknown domain pack {name!r}; available: {sorted(_PACKS)}") from None


__all__ = ["DomainPack", "FTTH_APD", "get_domain_pack"]
