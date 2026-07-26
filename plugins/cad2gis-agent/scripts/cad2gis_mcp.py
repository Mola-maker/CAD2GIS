"""Portable stdio launcher shared by Claude Code and Codex plugins."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_ROOT = PLUGIN_ROOT.parents[1]
SOURCE_ROOT = DEVELOPMENT_ROOT / "src"

if (SOURCE_ROOT / "cad2gis").is_dir() and str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cad2gis.agent_mcp import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
