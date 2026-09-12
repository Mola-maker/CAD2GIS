"""Verify patched native text using independent processes and pinned real inputs.

No CAD2GIS source/baseline changes, suffix trimming, or persistent global state.
Raw DWG reader JSON, DXF and stderr are retained beside the report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def read_dxf(path):
    lines = path.read_bytes().splitlines()
    assert len(lines) % 2 == 0, "Odd DXF tag line count"
    records, current = [], None
    for index in range(0, len(lines), 2):
        code, raw = int(lines[index].strip()), lines[index + 1]
        value = raw.decode("utf-8", "surrogateescape")
        if code == 0:
            if current is not None:
                records.append(current)
            current = [(code, value)]
        elif current is not None:
            current.append((code, value))
    if current:
        records.append(current)
    texts, encoding = {}, []
    facts = []
    for record in records:
        handle = next((v for c, v in record if c == 5), None)
        if handle and record[0][1] not in {"SECTION", "ENDSEC", "EOF"}:
            facts.append(record)
        if record[0][1] != "MTEXT":
            continue
        assert handle and handle not in texts, "Duplicate or missing MTEXT handle"
        raw_text = "".join(v for c, v in record if c in (1, 3))
        if "\udcb0" in raw_text:
            encoding.append({"handle": handle, "byte": "B0", "decoded_codepoint": "U+00B0",
                             "count": raw_text.count("\udcb0")})
        # The native R2000 DXF codepage degree byte is an independently known
        # encoding difference. Record it explicitly; do not remove any suffix.
        texts[handle] = raw_text.replace("\udcb0", "°")
    return texts, facts, encoding


def execute(command, stdout, stderr):
    with stdout.open("wb") as output, stderr.open("wb") as errors:
        completed = subprocess.run(command, stdout=output, stderr=errors, timeout=240)
    assert completed.returncode == 0, f"Native exit {completed.returncode}: {command}; see {stderr}"


def verify(args):
    inputs = json.loads(args.inputs.read_text(encoding="utf-8-sig"))
    args.output.mkdir(parents=True, exist_ok=False)
    suffix = ".exe" if (args.native / "dwg2dxf.exe").is_file() else ""
    binaries = {name: args.native / (name + suffix) for name in ("dwg2dxf", "dwgread")}
    report = {"schema": "cad2gis.native_mtext_acceptance.v1", "status": "running",
              "created_at_utc": datetime.now(timezone.utc).isoformat(), "repetitions": args.repetitions,
              "executables": {str(p): sha(p) for p in binaries.values()}, "cases": {},
              "comparison": "All MTEXT against dwgread JSON; LF/CR DXF escapes and explicit B0 degree decoding only",
              "baseline_modified": False, "source_modified": False}
    report_path = args.output / "native-mtext-report.json"
    def save():
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    try:
        for case in inputs["cases"]:
            path = args.inputs.parent / case["file"]
            assert sha(path) == case["sha256"], f"Input hash mismatch: {path}"
            report["cases"][case["name"]] = {"source_sha256": case["sha256"], "runs": []}
        for iteration in range(args.repetitions):
            cases = inputs["cases"][::1 if iteration % 2 == 0 else -1]
            for case in cases:
                source = (args.inputs.parent / case["file"]).resolve()
                output = args.output / f"run-{iteration:02d}" / case["name"]
                output.mkdir(parents=True)
                native_json, dxf = output / "dwgread.json", output / "converted.dxf"
                commands = [([str(binaries["dwgread"]), "-O", "JSON", str(source)], native_json, output / "dwgread.stderr"),
                            ([str(binaries["dwg2dxf"]), "-y", "-o", str(dxf), str(source)], output / "dwg2dxf.stdout", output / "dwg2dxf.stderr")]
                for command, stdout, stderr in commands[::1 if iteration % 2 == 0 else -1]:
                    execute(command, stdout, stderr)
                objects = json.loads(native_json.read_text(encoding="utf-8"))["OBJECTS"]
                rows = [r for r in objects if r.get("entity") == "MTEXT"]
                expected = {format(r["handle"][2], "X"): r["text"] for r in rows}
                assert len(rows) == len(expected), "Duplicate JSON MTEXT handles"
                assert len(rows) == case["expected_mtext_count"]
                assert digest(expected) == case["expected_json_mtext_sha256"], "JSON text differs from investigation reference"
                expected_escaped = {h: t.replace("\n", "^J").replace("\r", "^M") for h, t in expected.items()}
                actual, facts, encoding = read_dxf(dxf)
                differences = {h: {"expected": expected_escaped.get(h), "actual": actual.get(h)}
                               for h in set(expected_escaped) | set(actual)
                               if expected_escaped.get(h) != actual.get(h)}
                item = {"index": iteration, "json_mtext_sha256": digest(expected),
                        "mtext_count": len(actual), "all_json_objects_sha256": digest(objects),
                        "all_dxf_record_facts_sha256": digest(facts), "dxf_mtext_sha256": digest(actual),
                        "json_object_count": len(objects), "dxf_record_count": len(facts),
                        "entity_type_counts": dict(Counter(r.get("entity", r.get("object", "unknown")) for r in objects)),
                        "dxf_sha256": sha(dxf), "json_sha256": sha(native_json),
                        "explicit_encoding_observations": encoding, "differences": differences}
                runs = report["cases"][case["name"]]["runs"]
                runs.append(item)
                save()
                assert not differences, f"MTEXT mismatch in {case['name']}, run {iteration}: {len(differences)} handles"
                for key in ("json_mtext_sha256", "all_json_objects_sha256", "all_dxf_record_facts_sha256", "dxf_mtext_sha256", "entity_type_counts"):
                    assert item[key] == runs[0][key], f"Unstable {key}: {case['name']}"
                assert sha(source) == case["sha256"], "Source file modified by native process"
                print(json.dumps({"case": case["name"], "run": iteration, "status": "passed", "mtext": len(actual)}), flush=True)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        save()
    print(str(report_path), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    assert args.repetitions >= 5, "Acceptance requires at least five independent runs per drawing"
    args.native = args.native.resolve()
    args.output = args.output.resolve()
    verify(args)
