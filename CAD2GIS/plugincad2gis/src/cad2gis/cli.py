"""Command-line entry point for cad2gis.

The CLI is the *canonical* pipeline runner (the QGIS plugin calls the same code paths).
Subcommands are added story-by-story; unimplemented ones return exit code 2 so scripts
can detect "not yet built" distinctly from failure (1).
"""
from __future__ import annotations

import argparse
import sys

from . import __version__


def _cmd_doctor(args: argparse.Namespace) -> int:
    from . import envcheck

    return envcheck.run()


def _cmd_gen_samples(args: argparse.Namespace) -> int:
    from . import samples

    path = samples.generate(args.out)
    print(f"wrote synthetic sample: {path}")
    return 0


def _ingest_if_dwg(path: str) -> str:
    """Return a DXF path, converting DWG->DXF first if needed."""
    import os

    if os.path.splitext(path)[1].lower() == ".dwg":
        from .ingest import normalize_to_dxf

        res = normalize_to_dxf(path)
        print(f"normalized DWG->DXF via {res.method}: {res.dxf_path}")
        return res.dxf_path
    return path


def _cmd_profile(args: argparse.Namespace) -> int:
    import json

    from .profile import profile_dxf

    dxf = _ingest_if_dwg(args.path)
    prof = profile_dxf(dxf)
    if args.json:
        prof.write_json(args.json)
        print(f"wrote profile: {args.json}")
    print(
        f"{prof.source}: {prof.n_entities} entities, {prof.layers_defined} layers, "
        f"{prof.blocks_defined} blocks, {len(prof.comms_layers)} comms layers, "
        f"{len(prof.control_point_layers)} control-point layers"
    )
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    import json

    from .pipeline import run

    dxf = _ingest_if_dwg(args.path)
    coll, rep = run(dxf, benchmark=args.benchmark)
    d = rep.to_dict()
    out_json = args.report or "cad2gis_report.json"
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    print(f"features: {sum(rep.counts_final.values())}  report: {out_json}")
    if rep.accuracy:
        print(f"accuracy: {rep.accuracy['overall']:.3f} passed={rep.accuracy['passed']}")
    return 0


def _cmd_diagnose(args: argparse.Namespace) -> int:
    from .diagnostics import diagnose_collection, write_diagnostics
    from .pipeline import run

    dxf = _ingest_if_dwg(args.path)
    coll, rep = run(dxf, benchmark=args.benchmark)
    issues = diagnose_collection(coll, per_feature=rep.per_feature, network=rep.network)
    write_diagnostics(args.report, issues, metadata={"source": rep.source, "issue_count": len(issues)})
    print(f"diagnostics: {len(issues)} issues  report: {args.report}")
    return 0


def _cmd_doctor_proposals(args: argparse.Namespace) -> int:
    import json

    from .doctor import write_empty_proposals, write_prompt

    with open(args.diagnostics, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    write_empty_proposals(args.out)
    if args.offline_template:
        write_prompt(args.offline_template, payload)
        print(f"wrote doctor prompt: {args.offline_template}")
    print(f"wrote doctor proposals: {args.out}")
    return 0


def _cmd_apply_corrections(args: argparse.Namespace) -> int:
    import json
    import os

    from .corrections import apply_patches, read_patches, write_feature_collection, write_ledger_entry
    from .pipeline import run

    dxf = _ingest_if_dwg(args.path)
    coll, rep = run(dxf, benchmark=args.benchmark)
    patches = read_patches(args.proposals)
    out, records = apply_patches(coll, patches)
    write_feature_collection(args.out_features, out)
    if os.path.exists(args.ledger):
        os.remove(args.ledger)
    for record in records:
        write_ledger_entry(args.ledger, record)
    accepted = sum(1 for record in records if record.status == "accepted")
    payload = {
        "status": "applied",
        "source": rep.source,
        "patches": len(records),
        "accepted": accepted,
        "rejected": len(records) - accepted,
        "ledger": args.ledger,
        "corrected_features": args.out_features,
    }
    if args.out_report:
        with open(args.out_report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"corrections: {accepted}/{len(records)} accepted  ledger: {args.ledger}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    import json

    payload = {"status": "verified"}
    if args.corrected_features:
        from .corrections import read_feature_collection
        from .verify import BenchmarkSpec, score
        from .warehouse import PUBLISHED_SCHEMA

        coll = read_feature_collection(args.corrected_features)
        payload.update({
            "source": coll.source_file,
            "counts_final": coll.counts_by_class(),
            "corrected_features": args.corrected_features,
        })
        if args.benchmark:
            bench = BenchmarkSpec.from_json(args.benchmark)
            payload["accuracy"] = score(coll, bench, schemas=PUBLISHED_SCHEMA).to_dict()
    elif args.path:
        from .pipeline import run

        dxf = _ingest_if_dwg(args.path)
        _coll, rep = run(dxf, benchmark=args.benchmark)
        payload.update(rep.to_dict())
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"wrote verification report: {args.report}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _not_yet(story: str):
    def _fn(args: argparse.Namespace) -> int:
        print(f"{args.command}: not yet implemented (story {story})")
        return 2

    return _fn


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cad2gis",
        description="CAD (DWG/DXF) -> QGIS/GeoPackage transformation platform",
    )
    p.add_argument("--version", action="version", version=f"cad2gis {__version__}")
    sub = p.add_subparsers(dest="command")

    d = sub.add_parser("doctor", help="check the geospatial toolchain (QGIS/GDAL/ezdxf/GRASS/ODA)")
    d.set_defaults(func=_cmd_doctor)

    g = sub.add_parser("gen-samples", help="generate synthetic comms-infrastructure DXF fixtures")
    g.add_argument("--out", default="samples", help="output directory (default: samples)")
    g.set_defaults(func=_cmd_gen_samples)

    pr = sub.add_parser("profile", help="[G4] profile a DXF/DWG (layers, blocks, entities, CRS class)")
    pr.add_argument("path")
    pr.add_argument("--json", help="write full profile JSON to this path")
    pr.set_defaults(func=_cmd_profile)

    c = sub.add_parser("convert", help="[G3-G9] run the CAD->GIS pipeline (DWG/DXF -> classified features)")
    c.add_argument("path")
    c.add_argument("--benchmark", help="labeled benchmark JSON to score accuracy against")
    c.add_argument("--report", help="write run report JSON here (default: cad2gis_report.json)")
    c.set_defaults(func=_cmd_convert)

    dg = sub.add_parser("diagnose", help="run deterministic Accuracy Doctor diagnostics")
    dg.add_argument("path")
    dg.add_argument("--benchmark", help="labeled benchmark JSON to score accuracy against")
    dg.add_argument("--report", default="build/diagnostics.json", help="write diagnostics JSON here")
    dg.set_defaults(func=_cmd_diagnose)

    dp = sub.add_parser("doctor-proposals", help="write offline doctor prompt and empty proposal bundle")
    dp.add_argument("diagnostics", help="diagnostics JSON from cad2gis diagnose")
    dp.add_argument("--out", default="build/doctor_proposals.json", help="write proposal JSON here")
    dp.add_argument("--offline-template", help="write markdown prompt package here")
    dp.set_defaults(func=_cmd_doctor_proposals)

    ac = sub.add_parser("apply-corrections", help="validate and apply structured correction proposals")
    ac.add_argument("path")
    ac.add_argument("proposals")
    ac.add_argument("--benchmark", help="labeled benchmark JSON to score accuracy against")
    ac.add_argument("--ledger", default="build/corrections/corrections.jsonl", help="write ledger JSONL here")
    ac.add_argument("--out-report", help="write correction application report JSON here")
    ac.add_argument("--out-features", default="build/corrected_features.json", help="write corrected feature artifact JSON here")
    ac.set_defaults(func=_cmd_apply_corrections)

    v = sub.add_parser("verify", help="[G2/G11] score a conversion against the labeled benchmark")
    v.add_argument("path", nargs="?", help="optional DXF/DWG to run before verification")
    v.add_argument("--benchmark", help="labeled benchmark JSON to score accuracy against")
    v.add_argument("--corrected-features", help="verify a corrected feature artifact from apply-corrections")
    v.add_argument("--report", help="write verification report JSON here")
    v.set_defaults(func=_cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
