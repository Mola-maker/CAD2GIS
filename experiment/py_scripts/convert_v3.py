#!/usr/bin/env python3
"""Thin executable wrapper; conversion logic lives in cad2gis_v3.pipeline."""

from cad2gis_v3.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
