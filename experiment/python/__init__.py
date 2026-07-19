"""
CAD2GIS Converter Package — consolidated FTTH DWG-to-GeoPackage pipeline.

Core programs:
  cad_common     — DWG reading, geometry extraction, color parsing, CRS conversion (converter.py L1-L2)
  ftth_converter — FTTH classification, annotation binding, BOITE fusion (converter.py L3-L5)
  topology_repair — CABLE chaining, endpoint snapping, FDT domain tagging (topology_repair.py)
  style_exporter  — QML sidecar + layer_styles embedding + .qgz project (style_exporter.py)

Standalone components:
  legend_detector, layout_miner, evidence_ledger, evaluator, domain_vocab
"""
