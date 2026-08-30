from __future__ import annotations

from pathlib import Path


PROFILE_HELPER = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "cad2gis-agent"
    / "skills"
    / "convert-cad-to-gis"
    / "scripts"
    / "export-autocad-profile.lsp"
)


def test_autocad_profile_helper_has_a_narrow_export_only_contract() -> None:
    source = PROFILE_HELPER.read_text(encoding="utf-8").lower()

    assert "(vl-load-com)" in source
    assert "vla-get-activeprofile" in source
    assert "vla-exportprofile" in source
    assert "cad2gis_profile_exported" in source
    assert "(findfile target-path)" in source

    forbidden = (
        "vla-importprofile",
        "vla-put-activeprofile",
        "vla-resetprofile",
        "vl-registry-write",
        "(command ",
        "(vl-cmdf ",
    )
    assert not any(item in source for item in forbidden)
