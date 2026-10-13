import json
import pytest
from scripts import build_pages


@pytest.mark.parametrize(
    "base", ["https://mola-maker.github.io/CAD2GIS/", "https://cad2gis.molamaker.cn/"]
)
def test_pages_and_custom_domain_metadata_resolve(tmp_path, monkeypatch, base):
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    output = tmp_path / "site"
    build_pages.build(output, public_base_url=base)
    html = (output / "index.html").read_text(encoding="utf-8")
    assert f'rel="canonical" href="{base}"' in html
    assert "application/ld+json" in html and "SoftwareApplication" in html
    assert f"{base}install.html" in (output / "sitemap.xml").read_text(encoding="utf-8")
    assert f"Sitemap: {base}sitemap.xml" in (output / "robots.txt").read_text(
        encoding="utf-8"
    )
    assert (
        json.loads((output / "build-manifest.json").read_text(encoding="utf-8"))[
            "public_base_url"
        ]
        == base
    )


def test_gallery_reuses_existing_urls_without_report_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    output = tmp_path / "site"
    build_pages.build(output)
    html = (output / "index.html").read_text(encoding="utf-8")
    for n in range(1, 10):
        assert (
            f"https://mola-maker.github.io/CAD2GIS/deliveries/drawing-{n:02}/process/source-delivery-overlay.png"
            in html
        )
    assert "drawing-10/process" not in html
    assert not list(output.rglob("*nine*report*.zip"))
    assert "CONDITIONAL" in html
