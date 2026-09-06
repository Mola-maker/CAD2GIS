from __future__ import annotations

import re

import pytest

from cad2gis.cad2gis_v3.family_validation import (
    derive_family_from_samples,
    l1_validate_family,
    l2_validate_family_group,
)


def test_derive_family_preserves_mixed_hyphen_and_dot_separators() -> None:
    samples = [
        {"text": "DMPH-1.010.C07"},
        {"text": "DMPH-1.010.A03"},
        {"text": "DMPH-2.011.B04"},
    ]
    family = derive_family_from_samples("FAT", samples)[0]
    result = l1_validate_family(
        family,
        {"FAT": samples, "OTHER": [{"text": "unrelated"}]},
    )
    assert result["own_fraction"] == 1.0
    assert result["structure_fraction"] == 1.0
    assert result["passed"] is True


def test_derive_family_separates_width_families() -> None:
    samples = [
        {"text": "KLDYA.011.C01"},
        {"text": "KLDYA.011.C02"},
        {"text": "KLDYA.012.B10"},
    ]
    families = derive_family_from_samples("FAT_Info", samples)
    assert len(families) == 1
    assert families[0]["text_pattern"] == (
        r"^KLDYA[^A-Za-z0-9]+\d+[^A-Za-z0-9]+[A-Za-z]+\d+$"
    )


@pytest.mark.parametrize("layer,texts", [
    ("POLE ID", ["EXT.MR.MF.TMH.S02.P077", "EXT.MR.MF.TMH.S02.P079",
                 "MR.UP.TMH.EMR-46478.P001", "MR.UP.TMH.EMR-46478.P002"]),
    ("EXT POLE", ["EXT.MR.BLOR-05.P001", "EXT.MR.BLOR-06.P002",
                  "EXT.MR.GJM.S08.P026", "EXT.MR.GJM.S08.P027"]),
])
def test_equal_width_numeric_and_alphanumeric_fields_form_complete_disjoint_families(layer, texts):
    samples = [{"text": text, "aci_color": 1} for text in texts]
    families = derive_family_from_samples(layer, samples)
    assert len(families) == 2
    for text in texts:
        assert sum(re.fullmatch(family["text_pattern"], text) is not None for family in families) == 1
    assert all(l1_validate_family(family, {layer: samples})["passed"] for family in families)
    result = l2_validate_family_group(families, samples)
    assert result["passed"] is True
    assert result["unassigned_count"] == result["ambiguous_count"] == 0
    # Removing either observed family still fails closed; no catch-all pattern
    # or weaker L2 threshold is used to admit the previously rejected labels.
    incomplete = l2_validate_family_group(families[:1], samples)
    assert incomplete["passed"] is False
    assert incomplete["unassigned_count"] == 2


def test_shape_grouping_does_not_accept_unsupported_mixed_field_syntax():
    samples = [{"text": "REGION.12Q.P001"}, {"text": "REGION.15Q.P002"}]
    assert derive_family_from_samples("POLE ID", samples) == []


def test_derive_family_ignores_prose_with_numeric_tokens() -> None:
    samples = [
        {"text": "KLDYA.011.C01"},
        {"text": "KLDYA.011.C02"},
        {"text": "Power @1490 nm"},
        {"text": "Distance to FDT"},
    ]
    families = derive_family_from_samples("FAT_Info", samples)
    assert len(families) == 1
    assert families[0]["text_pattern"].startswith(r"^KLDYA")


def test_l2_grouping_skips_placeholders_and_prose() -> None:
    samples = [
        {"text": "KLDYA.011.C01"},
        {"text": "MR.DSBR.095.B01"},
        {"text": "MR.XXX.XXX.A010"},
        {"text": "Power @1490 nm"},
        {"text": "- dBm"},
    ]
    families = [
        {
            "family_id": "auto_fat_info",
            "text_pattern": (
                r"^KLDYA[^A-Za-z0-9]+\d+[^A-Za-z0-9]+[A-Za-z]+\d+$"
            ),
        },
        {
            "family_id": "auto_fat_info_2",
            "text_pattern": (
                r"^MR[^A-Za-z0-9]+DSBR[^A-Za-z0-9]+\d+[^A-Za-z0-9]+[A-Za-z]+\d+$"
            ),
        },
    ]
    result = l2_validate_family_group(families, samples)
    assert result["skipped_sample_count"] == 3
    assert result["ambiguous_count"] == 0
    assert result["unassigned_count"] == 0
    assert result["passed"] is True


def test_annotation_pattern_specificity_ranks_full_identifier_over_stub() -> None:
    from cad2gis.cad2gis_v3.family_validation import annotation_pattern_specificity

    stub = r"^[A-Za-z]+\d+$"
    full = r"^KLDYA[^A-Za-z0-9]+\d+[^A-Za-z0-9]+[A-Za-z]+\d+$"
    assert annotation_pattern_specificity(stub) == 1
    assert annotation_pattern_specificity(full) == 3
