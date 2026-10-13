import hashlib
import json
from pathlib import Path
import pytest
from scripts import build_pages

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "base", ["https://mola-maker.github.io/CAD2GIS/", "https://cad2gis.molamaker.cn/"]
)
def test_pages_uses_official_archive_and_canonical_on_both_hosts(
    tmp_path, monkeypatch, base
):
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    output = tmp_path / "site"
    build_pages.build(output, public_base_url=base)
    html = (output / "index.html").read_text(encoding="utf8")
    assert "从图纸到证据：CAD2GIS" in html
    assert 'rel="canonical" href="https://cad2gis.molamaker.cn/"' in html
    assert "wiki/index.html" in html
    assert not (output / "assets/hero-tube.js").exists()
    assert not (output / "workspace").exists()
    manifest = json.loads((output / "build-manifest.json").read_text(encoding="utf8"))
    assert manifest["public_base_url"] == base
    assert manifest["canonical_base_url"] == "https://cad2gis.molamaker.cn/"
    assert (
        manifest["source_archive_sha256"]
        == "bd2772327a87b08718d3f12a77a5d44d62faa2a32ee989e3e7e59ed7705210fb"
    )
    for name in [
        "docs/assets/fonts.css",
        "docs/assets/theme.css",
        "docs/assets/docs.css",
    ]:
        assert (output / name).read_bytes() == (
            ROOT / "site/official" / name
        ).read_bytes()
    for n in range(1, 10):
        assert list((output / "docs/assets/img/first-open").glob(f"drawing-{n:02}*"))
    assert not list(output.rglob("*.dwg")) and not list(output.rglob("*.dxf"))
    assert not list(output.rglob("*.zip"))
    assert "https://cad2gis.molamaker.cn/wiki/" in (output / "sitemap.xml").read_text()


def test_source_manifest_binds_actual_public_assets():
    source = ROOT / "site/official"
    manifest = json.loads((ROOT / "site/source-manifest.json").read_text())
    for item in manifest["files"]:
        assert (
            hashlib.sha256((source / item["path"]).read_bytes()).hexdigest()
            == item["sha256"]
        )
    assert len(manifest["files"]) == 955


def test_bilingual_wiki_shares_design_and_explains_verified_advantages(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    output = tmp_path / "site"
    build_pages.build(output)
    for language, counterpart in [("index.html", "en.html"), ("en.html", "index.html")]:
        html = (output / "wiki" / language).read_text(encoding="utf8")
        assert f'href="{counterpart}"' in html
        for css in ["fonts.css", "theme.css", "docs.css"]:
            assert f"../docs/assets/{css}" in html
        for text in [
            "Apache-2.0",
            "CONDITIONAL",
            "15",
            "0.8.0",
            "call_cad_bundle_tool",
            "project_root",
            "SHA-256",
        ]:
            assert text in html
        assert "https://github.com/Mola-maker/CAD2GIS/wiki" in html


def test_public_builder_refuses_source_overlap_and_foreign_output(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="separate from source"):
        build_pages.build(ROOT / "site")
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    output = tmp_path / "user-files"
    output.mkdir()
    (output / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="unowned"):
        build_pages.build(output)
    assert (output / "keep.txt").read_text() == "keep"


def test_tampered_archive_asset_is_rejected_before_output_mutation(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("changed")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"files": [{"path": "index.html", "sha256": "0" * 64}]})
    )
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(build_pages, "SOURCE_ROOT", source)
    monkeypatch.setattr(build_pages, "SOURCE_MANIFEST", manifest)
    output = tmp_path / "site"
    with pytest.raises(ValueError, match="hash mismatch"):
        build_pages.build(output)
    assert not output.exists()


def test_wiki_names_actual_registered_cad_wrappers():
    from cad2gis.contracts import MCP_TOOL_NAMES

    wrappers = {
        "get_cad_bundle_status",
        "prepare_cad_bundle_workspace",
        "list_cad_bundle_tools",
        "call_cad_bundle_tool",
    }
    assert wrappers <= set(MCP_TOOL_NAMES)
    for name in ["Home.md", "Home-EN.md"]:
        guide = (ROOT / "docs/wiki" / name).read_text(encoding="utf8")
        for wrapper in wrappers:
            assert f"`{wrapper}`" in guide
        assert "cad_bundle_export_dxf" not in guide
        assert "`edited_source`" in guide


def test_unmanifested_directory_symlink_cannot_publish_external_files(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    external = tmp_path / "private"
    external.mkdir()
    (external / "private.txt").write_text("synthetic-not-for-publication")
    entries = []
    for name in ["index.html", "cad2gis-report.html", "docs/index.html"]:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b'<html><head></head><body><main class="doc-main"></main></body></html>'
        path.write_bytes(data)
        entries.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    try:
        (source / "unmanifested").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit directory symlinks")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"files": entries, "archive_sha256": "fixture", "prefix": "fixture"})
    )
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(build_pages, "SOURCE_ROOT", source)
    monkeypatch.setattr(build_pages, "SOURCE_MANIFEST", manifest)
    with pytest.raises(ValueError, match="symlink"):
        build_pages.build(tmp_path / "site")
    assert not (tmp_path / "site/unmanifested/private.txt").exists()


def test_homepage_primary_copy_tracks_current_code_not_historical_metrics(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(build_pages, "PROJECT_ROOT", tmp_path)
    output = tmp_path / "site"
    build_pages.build(output)
    html = (output / "index.html").read_text(encoding="utf8")
    current = html.split('<details id="historical-report"', 1)[0]
    assert "CAD2GIS / 0.8.0" in current
    assert "63 个 MCP 接口" in current
    assert "15 个已验证" in current
    assert "get_cad_bundle_status" in current
    assert "call_cad_bundle_tool" in current
    assert "Apache-2.0" in current
    assert "1661 passed" in current
    assert "50 个 MCP 工具" not in current
    assert "1,300 项通过" not in current
    assert "用 48 天、91 次提交" not in current
    assert 'data-goto="#current-install"' in current
    assert 'id="current-install"' in current
    assert "历史证据" in html


def test_bilingual_cad_tool_table_matches_current_allowed_names():
    from cad2gis.cad_bundle_policy import ALLOWED_TOOLS

    for name in ["Home.md", "Home-EN.md"]:
        guide = (ROOT / "docs/wiki" / name).read_text(encoding="utf8")
        for tool in ALLOWED_TOOLS:
            assert f"`{tool}`" in guide
