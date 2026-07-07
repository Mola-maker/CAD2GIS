from __future__ import annotations

import os

from qgis_plugin.__init__ import _resolve_repo_src


def test_qgis_plugin_resolves_repo_src_from_workspace():
    src = _resolve_repo_src()

    assert os.path.basename(src) == "src"
    assert os.path.isdir(os.path.join(src, "cad2gis"))
