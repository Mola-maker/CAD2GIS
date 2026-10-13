import asyncio
import json
from pathlib import Path
import zipfile
import subprocess
import shutil
import sys
import pytest
from tools.build_agent_delivery import build_agent_delivery

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def built_inputs(tmp_path_factory):
    from scripts import build_pages

    temp = tmp_path_factory.mktemp("agent-build")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(temp / "dist"),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    previous = build_pages.PROJECT_ROOT
    try:
        build_pages.PROJECT_ROOT = temp
        build_pages.build(temp / "site")
    finally:
        build_pages.PROJECT_ROOT = previous
    return (
        next((temp / "dist").glob("*.whl")),
        temp / "dist/cad2gis-0.8.0.tar.gz",
        temp / "site",
    )


@pytest.fixture
def delivery(tmp_path, built_inputs):
    wheel, sdist, site = built_inputs
    result = build_agent_delivery(tmp_path / "out", wheel=wheel, sdist=sdist, site=site)
    return tmp_path / "out", result, wheel


def test_delivery_versions_and_licenses_match(delivery):
    out, result, wheel = delivery
    assert result["version"] == "0.8.0"
    with zipfile.ZipFile(out / "cad2gis-agent-0.8.0.mcpb") as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["version"] == "0.8.0"
        assert manifest["license"] == "Apache-2.0"
        assert b"Apache License" in z.read("LICENSE.md")
        assert manifest["user_config"]["project_root"]["required"] is True
    assert (out / "SHA256SUMS").is_file()
    # Do not overwrite someone else's directory.
    foreign = out.parent / "foreign"
    foreign.mkdir()
    (foreign / "user.txt").write_text("keep")
    with pytest.raises(ValueError):
        build_agent_delivery(
            foreign,
            wheel=wheel,
            sdist=out / "cad2gis-0.8.0.tar.gz",
            site=ROOT / "_site",
        )
    assert (foreign / "user.txt").read_text() == "keep"


def test_mcpb_contains_no_project_data_or_secrets(delivery):
    out, _, _ = delivery
    with zipfile.ZipFile(out / "cad2gis-agent-0.8.0.mcpb") as z:
        names = z.namelist()
        assert not any(
            Path(n).suffix.lower() in {".dwg", ".dxf", ".gpkg", ".qgz", ".whl", ".env"}
            for n in names
        )
        assert not any("demo-data" in n or ".venv" in n or ".git/" in n for n in names)
        assert set(names) == {
            "manifest.json",
            "server/entrypoint.py",
            "LICENSE.md",
            "NOTICE.md",
            "THIRD_PARTY_NOTICES.md",
            "README.md",
        }


def test_mcpb_launches_installed_wheel(delivery, tmp_path):
    uv = shutil.which("uv") or (
        str(ROOT / ".venv/bin/uv") if (ROOT / ".venv/bin/uv").exists() else None
    )
    if not uv:
        pytest.skip(
            "MCPB launch requires installed uv; archive checks run independently"
        )
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from cad2gis.contracts import MCP_TOOL_NAMES

    out, result, wheel = delivery
    unpack = tmp_path / "bundle"
    with zipfile.ZipFile(out / "cad2gis-agent-0.8.0.mcpb") as z:
        z.extractall(unpack)
    manifest = json.loads((unpack / "manifest.json").read_text())
    args = [
        a.replace("${__dirname}", str(unpack))
        for a in manifest["server"]["mcp_config"]["args"]
    ]
    # Before publication use the same built wheel locally; public URL is tested after release.
    args[args.index("--with") + 1] = f"cad2gis[agent] @ {wheel.as_uri()}"

    async def check():
        async with asyncio.timeout(180):
            with (tmp_path / "stderr.log").open("w") as err:
                async with stdio_client(
                    StdioServerParameters(
                        command=uv,
                        args=args,
                        env={
                            "CAD2GIS_PROJECT_ROOT": str(tmp_path),
                            "UV_PYTHON_DOWNLOADS": "never",
                        },
                    ),
                    errlog=err,
                ) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert {t.name for t in tools.tools} == set(MCP_TOOL_NAMES)
                        cap = await session.call_tool("get_capabilities", {})
                        assert not cap.isError
                        assert "0.8.0" in str(cap)
                        status = await session.call_tool("get_runtime_status", {})
                        assert not status.isError

    asyncio.run(check())


def test_mcpb_does_not_resolve_host_project(delivery):
    out, _, _ = delivery
    with zipfile.ZipFile(out / "cad2gis-agent-0.8.0.mcpb") as z:
        manifest = json.loads(z.read("manifest.json"))
    assert "--no-project" in manifest["server"]["mcp_config"]["args"]
