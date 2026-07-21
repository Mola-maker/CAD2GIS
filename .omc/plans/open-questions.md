## ralplan-consolidation-fidelity - 2026-07-19
- [ ] Should `converter.py` shim be permanent or temporary? — Depends on whether external scripts import from `python.converter`. If so, keep permanently. If only internal, remove after verification.
- [ ] Should `evaluator.py` be updated to import from `ftth_converter`? — Currently it only reads GPKG files (no Python imports). No change needed unless it calls converter functions directly.
- [ ] Should the old `topology_builder.py` and `style_builder.py` be deleted immediately? — Keep for one release cycle as transitional copies, then remove.
