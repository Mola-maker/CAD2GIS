"""Opt-in real DWG regression for attribute ownership (input is never committed)."""
import os

import pytest


def test_empty_insert_cannot_consume_next_insert_attributes():
    source = os.environ.get("CAD2GIS_ATTRIBUTE_REGRESSION_DWG")
    if os.name != "nt" or not source:
        pytest.skip("Set CAD2GIS_ATTRIBUTE_REGRESSION_DWG to the reviewed Semarang DWG")
    from cad2gis.reader.autocad import extract_dwg_records
    records = {r["handle"]: r for r in extract_dwg_records(source, timeout=600)}
    assert records["21D70"]["block_attributes"] == {}
    assert records["21D70"]["text"] == ""
    assert records["21D78"]["block_attributes"]["ID"] == "Slack Cable 48C"
