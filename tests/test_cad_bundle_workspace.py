from pathlib import Path
import hashlib

import ezdxf
import pytest


def drawing(tmp_path):
    source = tmp_path / "源图 source.dxf"
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.saveas(source)
    return source


def test_workspace_keeps_source_hash(tmp_path):
    from cad2gis.cad_bundle_workspace import create_workspace, load_workspace

    source = drawing(tmp_path)
    original = source.read_bytes()
    result = create_workspace(source, tmp_path / "副本 workspace")
    assert result["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["current_revision"] == 0
    assert (
        load_workspace(Path(result["workspace_id"]))["current_sha256"]
        == result["source_sha256"]
    )
    assert source.read_bytes() == original


def test_workspace_rejects_existing_or_symlink(tmp_path):
    from cad2gis.cad_bundle_workspace import create_workspace

    source = drawing(tmp_path)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        create_workspace(source, occupied)
    assert (occupied / "keep.txt").read_text() == "keep"
    linked = tmp_path / "link"
    linked.symlink_to(occupied, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        create_workspace(source, linked)


def test_revision_conflict_preserves_current(tmp_path):
    from cad2gis.cad_bundle_workspace import (
        create_workspace,
        commit_revision,
        load_workspace,
    )

    source = drawing(tmp_path)
    root = tmp_path / "workspace"
    create_workspace(source, root)
    staged = root / "edited.dxf"
    doc = ezdxf.readfile(source)
    doc.modelspace().add_circle((5, 5), 2)
    doc.saveas(staged)
    result = commit_revision(root, staged, 0, {"tool": "entity_create_circle"})
    assert result["current_revision"] == 1
    assert len(ezdxf.readfile(root / "revisions/000001.dxf").modelspace()) == 2
    with pytest.raises(ValueError, match="revision"):
        commit_revision(root, staged, 0, {})
    assert load_workspace(root)["current_revision"] == 1
    assert len(ezdxf.readfile(source).modelspace()) == 1


def test_unicode_space_paths_and_comma_rejection(tmp_path):
    from cad2gis.cad_bundle_workspace import create_workspace

    source = drawing(tmp_path)
    with pytest.raises(ValueError, match="comma"):
        create_workspace(source, tmp_path / "unsafe,scope")
    assert not (tmp_path / "unsafe,scope").exists()


def test_tampered_or_linked_revision_is_rejected(tmp_path):
    from cad2gis.cad_bundle_workspace import create_workspace, load_workspace

    source = drawing(tmp_path)
    root = tmp_path / "workspace"
    create_workspace(source, root)
    revision = root / "revisions/000000.dxf"
    revision.chmod(
        0o600
    )  # Windows readonly flag must be cleared before deliberate tampering.
    revision.unlink()
    revision.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        load_workspace(root)


def test_invalid_dxf_or_oversize_source_is_rejected(tmp_path, monkeypatch):
    from cad2gis import cad_bundle_workspace as workspace

    bad = tmp_path / "bad.dxf"
    bad.write_text("not a DXF")
    with pytest.raises(ValueError, match="DXF"):
        workspace.create_workspace(bad, tmp_path / "bad-workspace")
    source = drawing(tmp_path)
    monkeypatch.setattr(workspace, "MAX_DXF_BYTES", 1)
    with pytest.raises(ValueError, match="size"):
        workspace.create_workspace(source, tmp_path / "large-workspace")


@pytest.mark.parametrize("failure", ["copy", "evidence", "manifest"])
def test_failed_revision_publication_can_retry(tmp_path, monkeypatch, failure):
    from cad2gis import cad_bundle_workspace as w

    source = drawing(tmp_path)
    root = tmp_path / "workspace"
    w.create_workspace(source, root)
    staged = root / "edited.dxf"
    doc = ezdxf.readfile(source)
    doc.modelspace().add_circle((2, 2), 1)
    doc.saveas(staged)
    original_write = w._write_json
    original_copy = w.shutil.copyfileobj

    def failed_write(path, data):
        if (failure == "evidence" and path.parent.name == "evidence") or (
            failure == "manifest" and path.name == "manifest.json"
        ):
            raise OSError("transient publication failure")
        return original_write(path, data)

    def failed_copy(*args, **kwargs):
        raise OSError("transient copy failure")

    monkeypatch.setattr(w, "_write_json", failed_write)
    if failure == "copy":
        monkeypatch.setattr(w.shutil, "copyfileobj", failed_copy)
    with pytest.raises(OSError):
        w.commit_revision(root, staged, 0, {"tool": "test"})
    assert w.load_workspace(root)["current_revision"] == 0
    monkeypatch.setattr(w, "_write_json", original_write)
    monkeypatch.setattr(w.shutil, "copyfileobj", original_copy)
    assert w.commit_revision(root, staged, 0, {"tool": "test"})["current_revision"] == 1
    assert (root / "source.dxf").read_bytes() == source.read_bytes()


def test_crashed_revision_lock_and_orphan_are_recoverable(tmp_path):
    import subprocess
    import sys
    import os
    from cad2gis import cad_bundle_workspace as w

    source = drawing(tmp_path)
    root = tmp_path / "workspace"
    w.create_workspace(source, root)
    staged = root / "edited.dxf"
    staged.write_bytes(source.read_bytes())
    env = {**os.environ, "PYTHONPATH": str(Path(w.__file__).resolve().parents[1])}
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import os; from cad2gis.cad_bundle_workspace import _revision_lock; "
            f"root=Path({str(root)!r}); "
            '\nwith _revision_lock(root):\n (root/"revisions/000001.dxf").write_bytes((root/"source.dxf").read_bytes())\n os._exit(9)',
        ],
        env=env,
        capture_output=True,
    )
    assert child.returncode == 9, child.stderr.decode()
    assert w.commit_revision(root, staged, 0, {})["current_revision"] == 1
