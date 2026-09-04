"""Compatibility entrypoint forwarding legacy v3 flags to the canonical CLI.

``--input``, ``--run-dir`` and the explicit profile options are already
accepted by ``cad2gis convert``. Keep the historical module invocation while
sharing configuration validation, JSON result fields and error handling.
"""

from __future__ import annotations

import sys


def main(argv=None):
    from ..cli import main as canonical_main

    arguments = list(sys.argv[1:] if argv is None else argv)
    return canonical_main(["convert", "--json", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
