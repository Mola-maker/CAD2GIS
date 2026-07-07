"""G1 smoke tests — package imports, CLI version, and synthetic DXF generation."""
from __future__ import annotations

import os

import pytest


def test_version_string():
    import cad2gis

    assert cad2gis.__version__


def test_cli_version(capsys):
    from cad2gis import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "cad2gis" in capsys.readouterr().out


def test_gen_samples(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")  # requires the conda env
    from cad2gis import samples

    path = samples.generate(str(tmp_path))
    assert os.path.exists(path)

    # Re-open and confirm our known layers survived the round-trip.
    doc = ezdxf.readfile(path)
    layer_names = {ly.dxf.name for ly in doc.layers}
    for expected in samples.GROUND_TRUTH["layers"]:
        assert expected in layer_names
