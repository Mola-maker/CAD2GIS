"""SVG defaults, opt-out, fonts and result locations at conversion entry points."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from cad2gis import agent_mcp, batch, cli, pipeline


def _result(run, mode):
    candidates = {"mode": mode, "status": "disabled", "font_dirs": []}
    if mode == "candidate":
        candidates.update(status="completed", auto_applied=False,
                          html=str(run / "svg-candidates" / "correspondence.html"))
    return SimpleNamespace(
        run_status="CONDITIONAL", run_manifest_path=run / "run_manifest.json",
        source_path=run / "source.gpkg", evidence_path=run / "evidence.gpkg",
        delivery_path=run / "delivery.gpkg", style_manifest_path=run / "styles.json",
        counts={}, diagnostics={"svg_candidates": candidates},
    )


@pytest.mark.parametrize("command", ["convert", "auto-convert"])
@pytest.mark.parametrize("mode, font_names", [(None, []), ("candidate", ["fonts", "extra-fonts"]), ("off", [])])
def test_cli_svg_default_opt_out_and_fonts(tmp_path, monkeypatch, capsys, command, mode, font_names):
    captured = {}
    source, project, run = (tmp_path / name for name in ("source.dwg", "project", "run"))

    def convert(**kwargs):
        captured.update(kwargs)
        return _result(run, kwargs["svg_mode"])

    monkeypatch.setattr(pipeline, "convert_project", convert)
    monkeypatch.setattr(pipeline, "auto_onboard_project", lambda **kwargs: {"status": "auto_accepted"})
    arguments = [command, str(source), "--project", str(project), "--run-dir", str(run), "--json"]
    if mode:
        arguments.extend(["--svg-mode", mode])
    for name in font_names:
        arguments.extend(["--svg-font-dir", str(tmp_path / name)])
    assert cli.main(arguments) == 0
    assert captured["svg_mode"] == (mode or "candidate")
    assert captured["svg_font_dirs"] == tuple(tmp_path / name for name in font_names)
    payload = json.loads(capsys.readouterr().out)
    if command == "auto-convert":
        payload = payload["conversion"]
    assert payload["svg_candidates"] == _result(run, mode or "candidate").diagnostics["svg_candidates"]


@pytest.mark.parametrize("tool_name", ["run_conversion", "auto_onboard_and_convert"])
@pytest.mark.parametrize("mode, font_names", [(None, []), ("candidate", ["fonts", "extra-fonts"]), ("off", [])])
def test_mcp_svg_default_opt_out_and_fonts(tmp_path, monkeypatch, tool_name, mode, font_names):
    captured = {}
    source, project, run = (tmp_path / name for name in ("source.dwg", "project", "run"))
    source.write_bytes(b"source")
    project.mkdir()
    for name in font_names:
        (tmp_path / name).mkdir()
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))

    def convert(**kwargs):
        captured.update(kwargs)
        return _result(run, kwargs["svg_mode"])

    monkeypatch.setattr(pipeline, "convert_project", convert)
    monkeypatch.setattr(pipeline, "auto_onboard_project", lambda **kwargs: {"status": "auto_accepted"})
    arguments = {"source": str(source), "project_dir": str(project), "run_dir": str(run)}
    if mode:
        arguments["svg_mode"] = mode
    if font_names:
        arguments["svg_font_dirs"] = [str(tmp_path / name) for name in font_names]
    payload = getattr(agent_mcp, tool_name)(**arguments)
    assert captured["svg_mode"] == (mode or "candidate")
    assert captured["svg_font_dirs"] == tuple(tmp_path / name for name in font_names)
    assert payload["svg_candidates"] == _result(run, mode or "candidate").diagnostics["svg_candidates"]


@pytest.mark.parametrize("mode, font_names", [(None, ["fonts"]), ("candidate", ["fonts", "extra-fonts"]), ("off", [])])
def test_batch_svg_modes_fonts_and_published_report(tmp_path, monkeypatch, mode, font_names):
    source = tmp_path / "source.dwg"
    source.write_bytes(b"source")
    (tmp_path / "project").mkdir()
    for name in font_names:
        (tmp_path / name).mkdir()
    item = {"id": "drawing-01", "source": source.name, "project": "project",
            "source_sha256": hashlib.sha256(b"source").hexdigest(), "svg_font_dirs": font_names}
    if mode:
        item["svg_mode"] = mode
    contract = tmp_path / "batch.json"
    contract.write_text(json.dumps({"schema_version": batch.SCHEMA, "drawings": [item]}), encoding="utf-8")
    conversions = []

    def execute(command, **kwargs):
        if command[2] == "cad2gis":
            args = cli._parser().parse_args(command[3:])
            if args.command == "convert":
                conversions.append(args)
                args.run_dir.mkdir()
                (args.run_dir / "run_manifest.json").write_text("{}", encoding="utf-8")
                if args.svg_mode == "candidate":
                    candidates = args.run_dir / "svg-candidates"
                    candidates.mkdir()
                    (candidates / "correspondence.html").write_text("SVG candidates", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(batch.subprocess, "run", execute)
    monkeypatch.setattr("cad2gis.delivery.package_delivery", lambda *args, **kwargs: {"run_status": "CONDITIONAL"})
    result = batch.run_batch(contract, tmp_path / "batch-output")
    assert result["status"] == "COMPLETED"
    assert len(conversions) == 1
    assert conversions[0].svg_mode == (mode or "candidate")
    assert conversions[0].svg_font_dir == [tmp_path / name for name in font_names]
    links = result["drawings"][0]["links"]
    if mode == "off":
        assert "SVG 图例对应候选" not in links
    else:
        assert links["SVG 图例对应候选"] == "drawing-01/run/svg-candidates/correspondence.html"


def test_batch_explicit_off_rejects_font_directories(tmp_path):
    contract = tmp_path / "batch.json"
    contract.write_text(json.dumps({"schema_version": batch.SCHEMA, "drawings": [{
        "id": "drawing-01", "source": "source.dwg", "project": "project",
        "source_sha256": "a" * 64, "svg_mode": "off", "svg_font_dirs": ["fonts"],
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="requires candidate mode"):
        batch.load_contract(contract)
