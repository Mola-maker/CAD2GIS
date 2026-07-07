"""End-to-end conversion pipeline (wires G3-G9) — the canonical runner.

    parse (G3) -> classify (G6) -> reconcile geometry -> close rings + clean (G5/G7)
      -> build network (G8) -> classify CRS (G4) -> score vs benchmark (G2)

The QGIS plugin and CLI both call `run()` so behavior is identical everywhere. Warehousing
to GeoPackage (G10) and GCP georeferencing (G9) are added as later stages; this function
returns the cleaned FeatureCollection + a structured run report including the accuracy score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .crs import classify_extent
from .feature_context import FeatureContext, extract_insert_contexts
from .mapping import MappingEngine
from .model import Feature, FeatureCollection
from .network import build_network
from .parse import parse_dxf
from .profile import profile_dxf
from .refine import refine_topology
from .topology import clean_collection, close_unclosed_lines_to_polygons


@dataclass
class RunReport:
    source: str
    parse: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)
    counts_raw: dict = field(default_factory=dict)
    counts_final: dict = field(default_factory=dict)
    topology: dict = field(default_factory=dict)
    refine: dict = field(default_factory=dict)
    network: dict = field(default_factory=dict)
    crs: dict = field(default_factory=dict)
    georef: dict = field(default_factory=dict)
    warehouse: dict = field(default_factory=dict)
    attributes_added: int = 0
    per_feature: dict = field(default_factory=dict)
    accuracy: Optional[dict] = None
    unmapped: int = 0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def _scope_to_layers(
    coll: FeatureCollection,
    target_layers: set[str],
    engine: Optional[MappingEngine] = None,
    ctx_by_handle: Optional[dict] = None,
) -> FeatureCollection:
    """Keep comms features: those on a target layer, PLUS INSERT point-symbols whose reviewed
    block-code resolves to a comms facility regardless of layer (classification-authority).

    Real drawings mix disciplines in one 80k-entity file. Layer-scoping alone both (a) risks an
    O(n^2) topology blowup and (b) is WRONG at the edges: genuine comms duct symbols (gc170/gc013*)
    live on generic ZBTZ/GXYZ annotation layers, while paving symbols share those same layers. So
    for INSERT symbols we defer to the evidence-gated block-code table (classify_context) — a duct
    symbol is rescued onto a non-comms layer, a paving symbol on GXYZ is dropped. Non-INSERT
    linework still scopes by layer (that's where cables/ducts as polylines legitimately live).
    """
    ctx_by_handle = ctx_by_handle or {}
    out = FeatureCollection(crs=coll.crs, source_file=coll.source_file, metadata=dict(coll.metadata))
    for f in coll.features:
        on_target = f.source.layer in target_layers
        # For INSERT symbols we defer to the reviewed block-code table (classification-authority):
        if engine is not None and f.source.entity_type == "INSERT":
            ctx = ctx_by_handle.get(f.source.handle)
            if ctx is not None:
                r = engine.classify_context(ctx)
                ev = getattr(r, "evidence", {}) or {}
                if r.mapped:
                    out.add(f)          # comms facility -> keep regardless of layer
                    continue
                if ev.get("decision") == "rejected":
                    continue            # reviewed NON-comms (paving) -> drop even on a comms layer
                # gate_failed / paving_veto / unknown: keep only if on a comms layer, for review
                if on_target:
                    out.add(f)
                continue
        if on_target:
            out.add(f)
    return out


def _reconcile_geometry(
    coll: FeatureCollection,
    engine: MappingEngine,
    ctx_by_handle: Optional[dict] = None,
) -> FeatureCollection:
    """Classify each feature and reconcile geometry with the class's target geom type.

    INSERT features are classified with the full hit vector (`classify_context`) when a
    FeatureContext (nearest-text + fingerprint) is available for their handle — this is what
    resolves the opaque gc* duct symbols. Everything else uses the deterministic rule engine.
    """
    ctx_by_handle = ctx_by_handle or {}
    out = FeatureCollection(crs=coll.crs, source_file=coll.source_file, metadata=dict(coll.metadata))
    for f in coll.features:
        ctx = ctx_by_handle.get(f.source.handle) if f.source.handle else None
        if ctx is not None and f.source.entity_type == "INSERT":
            r = engine.classify_context(ctx)
        else:
            r = engine.classify(
                layer=f.source.layer, block=f.source.block,
                entity_type=f.source.entity_type,
                text=f.attributes.get("text"),
            )
        f.feature_class = r.feature_class
        f.confidence = r.confidence
        f.attributes.update(r.attributes)
        if getattr(r, "evidence", None):
            f.attributes["_map_evidence"] = r.evidence
        out.add(f)
    return out


def run(
    path: str,
    benchmark: Optional[str] = None,
    scope_layers: Optional[set[str]] = None,
    auto_scope: bool = True,
    scope_threshold: int = 20000,
    georeference: bool = True,
    warehouse: Optional[str] = None,
    refine: bool = True,
    on_stage=None,
) -> tuple[FeatureCollection, RunReport]:
    def _stage(name, **detail):
        if on_stage:
            try:
                on_stage(name, detail or None)
            except Exception:  # noqa: BLE001 - a progress callback must never break the run
                pass

    _stage("parse", status="running")
    coll, pstats = parse_dxf(path)
    rep = RunReport(source=coll.source_file or path)
    _stage("parse", status="done", entities=pstats.entities_seen, features=pstats.features_out)
    rep.parse = {
        "entities_seen": pstats.entities_seen,
        "features_out": pstats.features_out,
        "blocks_exploded": pstats.blocks_exploded,
        "curves_flattened": pstats.curves_flattened,
        "by_type": pstats.by_type,
    }

    engine = MappingEngine.from_yaml()

    # Build the INSERT hit vector (nearest-text + block fingerprint) once, keyed by handle. This
    # is what lets classify_context resolve the opaque gc* duct symbols and rescue them across
    # layers. Extraction re-reads the DXF; skip it if there are no reviewed block codes loaded.
    ctx_by_handle: dict = {}
    if engine.block_codes:
        try:
            for c in extract_insert_contexts(path):
                if c.handle:
                    ctx_by_handle[c.handle] = c
        except Exception:  # noqa: BLE001 - never let context extraction break the core pipeline
            ctx_by_handle = {}

    # Scope to the comms target layers on large, multi-discipline drawings. Explicit
    # scope_layers wins; otherwise auto-detect comms layers from the profile when the
    # drawing is big enough that off-discipline noise + O(n^2) topology would dominate.
    target = set(scope_layers) if scope_layers else None
    if target is None and auto_scope and pstats.features_out >= scope_threshold:
        prof = profile_dxf(path)
        target = set(prof.comms_layers)
    if target:
        before = len(coll)
        coll = _scope_to_layers(coll, target, engine=engine, ctx_by_handle=ctx_by_handle)
        rep.scope = {"target_layers": sorted(target), "kept": len(coll), "dropped": before - len(coll)}

    coll = _reconcile_geometry(coll, engine, ctx_by_handle=ctx_by_handle)
    rep.counts_raw = coll.counts_by_class()
    rep.unmapped = rep.counts_raw.get("__unmapped__", 0)

    # Topology: close near-closed room rings, then per-class clean.
    _stage("topology", status="running")
    coll = close_unclosed_lines_to_polygons(coll, close_gap_max=2.0)
    coll, trep = clean_collection(coll, point_tol=0.5, dangle_max_len=10.0, dup_tol=0.6)
    rep.topology = trep.to_dict()

    # Topology-aware refinement (G11): drop noise line-fragments misclassified as routes, snap
    # real routes to manholes. On real drawings this removes ~half the false "cables" (raising the
    # count dimension) and closes the network (raising connectivity).
    if refine:
        coll, rrep = refine_topology(coll, min_route_len=2.0, snap_tol=5.0)
        rep.refine = rrep.to_dict()
        # Graph label propagation (G-coverage): upgrade gated-out duct symbols to duct when the
        # comms-network topology independently confirms them (near a route or manhole).
        from .refine import propagate_network_labels

        coll, prop = propagate_network_labels(coll, assoc_tol=8.0)
        rep.refine["propagated"] = prop

    # Structured attribute extraction (G11c): parse duct specs (3孔PVC110 -> holes/material/dia),
    # point IDs, etc. from captured labels into typed fields — raises the attribute dimension.
    from .attributes import enrich_collection

    rep.attributes_added = enrich_collection(coll)
    rep.counts_final = coll.counts_by_class()

    # Network connectivity — synthesize junction nodes at route splice points (routes connect to
    # each other, not only to manholes), which is what makes connectivity reflect real topology.
    _stage("network", status="running")
    net = build_network(coll, snap_tol=3.0, synth_junctions=True)
    nqc = net.qc()
    rep.network = nqc.to_dict()
    _stage("network", status="done", connectivity=rep.network["connectivity_ratio"])

    # CRS classification from extent.
    xs = [pt for f in coll.features for pt in _coords_x(f.geometry)]
    ys = [pt for f in coll.features for pt in _coords_y(f.geometry)]
    if xs and ys:
        guess = classify_extent(min(xs), min(ys), max(xs), max(ys))
        coll.crs = guess.label if guess.epsg is None else f"EPSG:{guess.epsg}"
        rep.crs = guess.to_dict()

    # Georeference (G9): recover a real-world transform from in-drawing X=/Y= node labels, refined
    # by consensus re-pairing to the actual node symbols (removes label-placement offset). We do
    # NOT declare a named EPSG — the coords are a local survey grid; the honest declaration is a
    # local-engineering grid plus the fitted transform record (Codex G9 #4).
    fit = None
    gcps_refined = None
    if georeference:
        _stage("georeference", status="running")
        try:
            from .gcp import extract_gcps_from_labels, fit_transform, refine_gcps_to_nodes

            gcps = extract_gcps_from_labels(path)
            node_pos = [(f.geometry.x, f.geometry.y) for f in coll.features
                        if f.attributes.get("is_node_block") and f.geometry.geom_type == "Point"]
            if len(gcps) >= 3:
                gcps_refined = refine_gcps_to_nodes(gcps, node_pos)
                fit = fit_transform(
                    gcps_refined,
                    dst_crs="local-engineering-grid (EPSG unknown; X=northing,Y=easting; fitted transform)",
                )
                rep.georef = fit.to_dict()
        except Exception as ex:  # noqa: BLE001 - georef is optional, never break the core run
            rep.georef = {"error": str(ex)}

    # Per-feature semantic verification (G-sem): REAL per-feature correctness using signals
    # independent of the classifier's rule path (manhole↔surveyed-label cross-source, cable↔topology,
    # duct↔fingerprint, annotation↔text). Closes the audit's tautological-correctness defect.
    try:
        from .feature_context import block_fingerprint
        from .verify import verify_per_feature
        import ezdxf as _ezdxf

        _doc = _ezdxf.readfile(path)
        fingerprints = {}
        for f in coll.features:
            if f.source.block and f.source.block not in fingerprints:
                fingerprints[f.source.block] = block_fingerprint(_doc, f.source.block)
        pfv = verify_per_feature(coll, source_path=path, transform=fit,
                                 gcps_refined=gcps_refined, fingerprints=fingerprints)
        rep.per_feature = pfv.to_dict()
    except Exception as ex:  # noqa: BLE001 - verification is best-effort, never break the core run
        rep.per_feature = {"error": str(ex)}

    # Accuracy vs labeled benchmark (optional).
    if benchmark:
        _stage("accuracy", status="running")
        from .verify import BenchmarkSpec, score
        from .warehouse import PUBLISHED_SCHEMA

        bench = BenchmarkSpec.from_json(benchmark)
        report = score(coll, bench, schemas=PUBLISHED_SCHEMA,
                       network_qc=nqc.to_dict(), georef=rep.georef or None)
        rep.accuracy = report.to_dict()
        _stage("accuracy", status="done", overall=rep.accuracy.get("overall"))

    # Warehouse (G10): write the standardized GeoPackage (one layer per class + published schema +
    # provenance + metadata tables + embedded styles). Geometry is georeferenced by the G9 transform.
    if warehouse:
        _stage("warehouse", status="running")
        try:
            from .warehouse import write_geopackage

            qc = dict(rep.network)
            qc["unmapped"] = rep.unmapped
            if rep.georef:
                qc["georef_rmse"] = rep.georef.get("rmse")
            wrep = write_geopackage(
                coll, warehouse, transform=fit,
                manifest={"pipeline": "cad2gis", "counts": rep.counts_final},
                qc=qc, source_path=path,
            )
            rep.warehouse = wrep.to_dict()
        except Exception as ex:  # noqa: BLE001 - warehousing is optional, never break the core run
            rep.warehouse = {"error": str(ex)}

    return coll, rep


def _coords_x(geom):
    try:
        return [p[0] for p in geom.exterior.coords] if geom.geom_type == "Polygon" else [p[0] for p in geom.coords]
    except Exception:  # noqa: BLE001
        return [geom.x] if geom.geom_type == "Point" else []


def _coords_y(geom):
    try:
        return [p[1] for p in geom.exterior.coords] if geom.geom_type == "Polygon" else [p[1] for p in geom.coords]
    except Exception:  # noqa: BLE001
        return [geom.y] if geom.geom_type == "Point" else []
