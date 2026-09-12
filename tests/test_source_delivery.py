"""Verify release source coverage, private-data exclusions and reproducible bytes."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


MODULE = Path(__file__).resolve().parents[1] / "tools/build_upgrade_sources.py"
spec = importlib.util.spec_from_file_location("build_upgrade_sources", MODULE)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def _git(root, *args):
    subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "-c", "user.name=Delivery test", "-c", "user.email=test@example.invalid", "-C", str(root), *args], check=True, capture_output=True)


def _write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init")
    for name, data in {
        "pyproject.toml": b'[build-system]\nrequires=["setuptools>=75"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="cad2gis-delivery-test"\nversion="0.7.0"\n[tool.setuptools.packages.find]\nwhere=["src"]\n',
        "src/example/__init__.py": b'VALUE = "old"\n',
        "plugins/cad2gis-agent/.codex-plugin/plugin.json": b'{"version":"0.7.0"}',
        "plugins/cad2gis-agent/.claude-plugin/plugin.json": b'{"version":"0.7.0"}',
        "plugins/cad2gis-agent/.mcp.json": b'{"mcpServers":{}}',
        "plugins/cad2gis-agent/skills/convert/SKILL.md": b'# Original skill\n',
        "plugins/cad2gis-agent/clients/codex.config.toml": b'[mcp_servers.cad2gis]\n',
        "baselines/site/config/project.json": b'{}',
        "baselines/site/delivery/view-with-source-SVG.qgz": b'qgis project fixture',
        "baselines/site/run/delivery.gpkg": b'generated run must not ship',
        "tests/data/source-fixture.gpkg": b'versioned test fixture',
        ".env": b'API_KEY=private-machine-value',
        "apps/android/development.keystore": b'private-signing-material',
        "raw/private.dwg": b'private-original-drawing',
        ".gitignore": b'dist/\n__pycache__/\n',
    }.items():
        _write(root, name, data)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    _write(root, "src/example/__init__.py", b'VALUE = "current-worktree"\n')
    _write(root, "src/example/new_feature.py", b'ADDED = True\n')
    _write(root, "docs/upgrade.md", b'# Current changes\n')
    _write(root, "packaging/redis.compose.yaml", b'services: {}\n')
    _write(root, "src/example/assets/ip.png", b'controlled-asset-bytes')
    _write(root, "output/browser-result.json", b'generated-evidence')
    return root


def test_current_bytes_new_sources_hidden_plugin_and_frozen_baselines_are_packaged(repository, tmp_path):
    result = delivery.build(repository, tmp_path / "release-one/source", tmp_path / "release-one/plugin")
    assert result["status"] == "verified" and result["workspace_dirty"]
    source = Path(result["archives"][0]["path"])
    with zipfile.ZipFile(source) as archive:
        prefix = "CAD2GIS-0.7.0/"
        assert archive.read(prefix + "src/example/__init__.py") == b'VALUE = "current-worktree"\n'
        manifest = json.loads(archive.read(prefix + delivery.MANIFEST))
        names = {item["path"] for item in manifest["files"]}
        assert {"src/example/new_feature.py", "docs/upgrade.md", "packaging/redis.compose.yaml", "src/example/assets/ip.png", "tests/data/source-fixture.gpkg", "baselines/site/config/project.json", "baselines/site/delivery/view-with-source-SVG.qgz"} <= names
        assert not {".env", "apps/android/development.keystore", "raw/private.dwg", "baselines/site/run/delivery.gpkg", "output/browser-result.json"} & names
        excluded = {item["path"] for item in manifest["excluded"]}
        assert {".env", "apps/android/development.keystore", "raw/private.dwg", "output/browser-result.json"} <= excluded
        assert any(item["path"] == "src/example/__init__.py" and item["status"] == " M" for item in manifest["changes"])
        assert manifest["tree_sha256"] == delivery.tree_hash(manifest["files"])
    with zipfile.ZipFile(result["archives"][1]["path"]) as archive:
        assert {"cad2gis-agent/.codex-plugin/plugin.json", "cad2gis-agent/.claude-plugin/plugin.json", "cad2gis-agent/.mcp.json", "cad2gis-agent/skills/convert/SKILL.md", "cad2gis-agent/clients/codex.config.toml"} <= set(archive.namelist())
    repeated = delivery.build(repository, tmp_path / "release-two/source", tmp_path / "release-two/plugin")
    assert [item["sha256"] for item in result["archives"]] == [item["sha256"] for item in repeated["archives"]]
    assert all(item["repeated_hash_verified"] for item in repeated["archives"])


def test_existing_delivery_is_never_replaced(repository, tmp_path):
    source_dir = tmp_path / "release/source"
    source_dir.mkdir(parents=True)
    archive = source_dir / "CAD2GIS-0.7.0-source.zip"
    archive.write_bytes(b'previous-release')
    with pytest.raises(delivery.DeliveryError, match="already exists"):
        delivery.build(repository, source_dir, tmp_path / "release/plugin")
    assert archive.read_bytes() == b'previous-release'
    assert not (tmp_path / "release/plugin").exists()


def test_limits_fail_loudly_instead_of_dropping_source(repository):
    with pytest.raises(delivery.DeliveryError, match="exceeds"):
        delivery.inventory(repository, max_file_bytes=2, max_total_bytes=100000)
    with pytest.raises(delivery.DeliveryError, match="exceeds"):
        delivery.inventory(repository, max_file_bytes=100000, max_total_bytes=2)


def test_changed_source_after_inventory_is_rejected(repository, tmp_path):
    plan = delivery.inventory(repository, max_file_bytes=100000, max_total_bytes=1000000)
    _write(repository, "src/example/new_feature.py", b'ADDED = False\n')
    with pytest.raises(delivery.DeliveryError, match="changed after inventory"):
        delivery.write_zip(repository, tmp_path / "staged.zip", plan["source"], max_file_bytes=100000)


def test_unexpected_archive_member_is_rejected(repository, tmp_path):
    result = delivery.build(repository, tmp_path / "release/source", tmp_path / "release/plugin")
    archive_path = Path(result["archives"][0]["path"])
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr("CAD2GIS-0.7.0/surprise.py", b'not in the manifest')
    with pytest.raises(delivery.DeliveryError, match="member list"):
        delivery.verify(archive_path)


def test_extracted_source_can_build_a_wheel_without_git(repository, tmp_path):
    result = delivery.build(repository, tmp_path / "release/source", tmp_path / "release/plugin")
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(result["archives"][0]["path"]) as archive:
        archive.extractall(unpacked)  # This archive was just verified by the builder.
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(unpacked / "CAD2GIS-0.7.0"), "--no-deps", "--no-build-isolation", "--wheel-dir", str(tmp_path / "wheels")], check=True, capture_output=True)
    wheel = next((tmp_path / "wheels").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("example/__init__.py") == b'VALUE = "current-worktree"\n'
        assert "example/new_feature.py" in archive.namelist()


def test_literal_private_key_inside_source_is_not_shipped(repository):
    _write(repository, "src/example/key_material.py", b'-----BEGIN PRIVATE KEY-----\n' + b'A' * 80 + b'\n-----END PRIVATE KEY-----')
    with pytest.raises(delivery.DeliveryError, match="Private key content"):
        delivery.inventory(repository, max_file_bytes=100000, max_total_bytes=1000000)


def test_late_content_change_without_new_git_status_prevents_publication(repository, tmp_path, monkeypatch):
    real_verify = delivery.verify

    def changed_after_verify(path, **kwargs):
        result = real_verify(path, **kwargs)
        if result["kind"] == "agent-plugin":
            _write(repository, "src/example/__init__.py", b'VALUE = "late-edit"\n')
        return result

    monkeypatch.setattr(delivery, "verify", changed_after_verify)
    with pytest.raises(delivery.DeliveryError, match="before publication"):
        delivery.build(repository, tmp_path / "release/source", tmp_path / "release/plugin")
    assert not (tmp_path / "release").exists()
