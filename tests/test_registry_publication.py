import hashlib
import json
from pathlib import Path
import pytest
from tools.registry_status import publication_record

ROOT = Path(__file__).resolve().parents[1]


def test_publication_requires_matching_public_readback():
    with pytest.raises(ValueError):
        publication_record(
            "Official MCP Registry",
            "published",
            url="https://example.com",
            version="0.8.0",
        )
    with pytest.raises(ValueError):
        publication_record(
            "Official MCP Registry",
            "published",
            url="https://example.com",
            version="0.8.0",
            public_record={
                "name": "io.github.Mola-maker/cad2gis-agent",
                "version": "0.7.8",
            },
        )
    record = publication_record(
        "Official MCP Registry",
        "published",
        url="https://example.com",
        version="0.8.0",
        public_record={
            "name": "io.github.Mola-maker/cad2gis-agent",
            "version": "0.8.0",
        },
    )
    assert record["evidence"]["public_record"]["version"] == "0.8.0"
    assert (
        publication_record(
            "Smithery",
            "requires_user_action",
            version="0.8.0",
            required_user_action="Sign in",
        )["required_user_action"]
        == "Sign in"
    )


def test_registry_package_matches_actual_bundle():
    from jsonschema import validate

    server = json.loads((ROOT / "server.json").read_text())
    validate(
        server,
        json.loads((ROOT / "tests/fixtures/registry/server.schema.json").read_text()),
    )
    package = server["packages"][0]
    assert package["registryType"] == "mcpb"
    bundle = ROOT.parent / "deliverables/cad2gis-agent-0.8.0.mcpb"
    assert len(package["fileSha256"]) == 64
    if bundle.exists():
        assert package["fileSha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    validate(
        json.loads((ROOT / "glama.json").read_text()),
        json.loads((ROOT / "tests/fixtures/registry/glama.schema.json").read_text()),
    )
