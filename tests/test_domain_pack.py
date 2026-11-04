from __future__ import annotations

import pytest

from cad2gis.cad2gis_v3 import schema_config, warehouse
from cad2gis.cad2gis_v3.domain_pack import FTTH_APD, get_domain_pack


def test_ftth_pack_reproduces_the_reviewed_eight_layer_contract():
    assert FTTH_APD.delivery_layers == (
        "SITE", "BOITE", "PTECH", "IMB", "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO",
    )
    assert set(FTTH_APD.delivery_layers) == set(schema_config.REQUIRED_LAYERS)
    assert set(FTTH_APD.optional_layers) == set(schema_config.OPTIONAL_LAYERS)
    assert warehouse.LAYER_ORDER == FTTH_APD.delivery_layers
    assert warehouse.OPTIONAL_LAYER_ORDER == FTTH_APD.optional_layers
    assert FTTH_APD.route_class == "CABLE"
    assert FTTH_APD.route_support_classes == {"PTECH", "EMR"}
    assert FTTH_APD.control_coverage_classes == {"PTECH", "BOITE", "SITE", "EMR"}
    assert (FTTH_APD.support_class, FTTH_APD.box_class, FTTH_APD.site_class) == ("PTECH", "BOITE", "SITE")


def test_semantic_roles_map_only_to_delivery_classes():
    assert set(FTTH_APD.role_classes) == {
        "CENTRAL_SITE", "SUPPORT", "DISTRIBUTION_NODE", "ACCESS_NODE",
        "PREMISE", "NETWORK_ROUTE", "NETWORK_SEGMENT", "ZONE",
    }
    allowed = set(FTTH_APD.delivery_layers) | set(FTTH_APD.optional_layers)
    for classes in FTTH_APD.role_classes.values():
        assert classes <= allowed
    assert FTTH_APD.classes_for("ZONE") == {"ZPM", "ZNRO"}
    assert FTTH_APD.classes_for("UNKNOWN") == frozenset()
    with pytest.raises(TypeError):
        FTTH_APD.role_classes["NEW"] = frozenset()  # read-only mapping


def test_domain_modes_resolve_to_the_only_implemented_pack():
    for mode in ("auto", "generic", "ftth_apd"):
        assert get_domain_pack(mode) is FTTH_APD
    with pytest.raises(ValueError, match="Unknown domain pack"):
        get_domain_pack("power_grid")
