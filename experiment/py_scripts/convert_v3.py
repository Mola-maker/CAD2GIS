#!/usr/bin/env python3
"""Compatibility wrapper for the canonical :mod:`cad2gis` command line."""

from cad2gis.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
