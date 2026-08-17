from __future__ import annotations

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
