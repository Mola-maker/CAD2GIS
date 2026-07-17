"""Contract tests for the provider-neutral DeepSeek/New API review lane."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cad2gis_v3.curation import CurationError, build_review_bundle
from cad2gis_v3.curation_provenance import (
    OFFLINE_CURATION_FILES,
    offline_curation_provenance,
)
from cad2gis_v3.curation_providers import (
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderError,
)
from cad2gis_v3.curation_service import review_task
from cad2gis_v3.implementation import (
    PRODUCTION_CONVERSION_FILES,
    production_conversion_provenance,
)
from test_curation_v3 import _feature_task, _make_evidence, _proposal


def _serve(response_value):
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            captured["path"] = self.path
            captured["authorization"] = self.headers.get("Authorization")
            captured["body"] = self.rfile.read(length)
            raw = json.dumps(response_value).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, captured


def test_deepseek_profile_is_current_json_object_and_secret_safe():
    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY"):
        ProviderConfig.from_env({})
    secret = "deepseek-secret-never-log"
    config = ProviderConfig.from_env({"DEEPSEEK_API_KEY": secret})
    assert config.provider == "deepseek"
    assert config.base_url == "https://api.deepseek.com"
    assert config.endpoint == "https://api.deepseek.com/chat/completions"
    assert config.model == "deepseek-v4-flash"
    assert config.capability == "json_object"
    assert config.token_field == "max_tokens"
    assert config.disable_thinking is True
    assert secret not in repr(config)


def test_new_api_profile_requires_deployment_and_explicit_capability():
    base = {
        "CAD2GIS_LLM_PROVIDER": "new_api",
        "NEW_API_BASE_URL": "https://gateway.example/v1",
        "NEW_API_API_KEY": "gateway-secret",
        "NEW_API_MODEL": "routed-model",
    }
    with pytest.raises(ProviderError, match="NEW_API_CAPABILITY"):
        ProviderConfig.from_env(base)
    config = ProviderConfig.from_env({**base, "NEW_API_CAPABILITY": "json_schema"})
    assert config.endpoint == "https://gateway.example/v1/chat/completions"
    assert config.capability == "json_schema"
    assert config.token_field == "max_completion_tokens"
    with pytest.raises(ProviderError, match="json_schema or json_object"):
        ProviderConfig.from_env({**base, "NEW_API_CAPABILITY": "free_text"})


@pytest.mark.parametrize(
    ("provider_id", "capability", "expected_path"),
    (("deepseek", "json_object", "/chat/completions"),
     ("new_api", "json_schema", "/v1/chat/completions")),
)
def test_provider_profiles_share_port_but_keep_capabilities(
    tmp_path, provider_id, capability, expected_path,
):
    dwg, evidence = _make_evidence(tmp_path)
    bundle = build_review_bundle(evidence, dwg)
    task = _feature_task(bundle)
    proposal_value = _proposal(bundle, task)
    response = {
        "id": f"{provider_id}-response",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(proposal_value)},
        }],
    }
    server, thread, captured = _serve(response)
    secret = f"{provider_id}-secret"
    try:
        if provider_id == "deepseek":
            env = {
                "CAD2GIS_LLM_PROVIDER": "deepseek",
                "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "DEEPSEEK_API_KEY": secret,
                "DEEPSEEK_MODEL": "deepseek-contract-model",
            }
        else:
            env = {
                "CAD2GIS_LLM_PROVIDER": "new_api",
                "NEW_API_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "NEW_API_API_KEY": secret,
                "NEW_API_MODEL": "gateway-contract-model",
                "NEW_API_CAPABILITY": capability,
            }
        config = ProviderConfig.from_env(env)
        proposal, audit = review_task(
            bundle, task["task_id"], OpenAICompatibleProvider(config),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    request_value = json.loads(captured["body"])
    assert captured["path"] == expected_path
    assert captured["authorization"] == f"Bearer {secret}"
    assert request_value["response_format"]["type"] == capability
    if capability == "json_schema":
        assert request_value["response_format"]["json_schema"]["strict"] is True
    else:
        assert request_value["thinking"] == {"type": "disabled"}
        assert "max_tokens" in request_value
    request_text = captured["body"].decode("utf-8")
    for forbidden in ("123.4", "456.7", "native_points", "insertion_point"):
        assert forbidden not in request_text
    assert "LONGUEUR" in request_text
    assert "span_metrics" in request_text
    assert proposal.decisions[0].task_id == task["task_id"]
    assert audit["channel"]["provider"] == provider_id
    assert audit["channel"]["capability"] == capability
    assert audit["implementation"]["scope"] == "offline-curation"
    audit_text = json.dumps(audit, ensure_ascii=False)
    assert secret not in audit_text
    assert config.base_url not in audit_text


def test_provider_output_is_untrusted_and_local_domain_gate_rejects_facts(tmp_path):
    dwg, evidence = _make_evidence(tmp_path)
    bundle = build_review_bundle(evidence, dwg)
    task = _feature_task(bundle)
    invalid = _proposal(bundle, task)
    invalid["decisions"][0]["coordinates"] = [1, 2]
    response = {
        "id": "malformed-domain-response",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(invalid)},
        }],
    }
    server, thread, _captured = _serve(response)
    try:
        config = ProviderConfig.from_env({
            "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "DEEPSEEK_API_KEY": "secret",
        })
        with pytest.raises(CurationError, match="Forbidden"):
            review_task(bundle, task["task_id"], OpenAICompatibleProvider(config))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_review_and_production_have_separate_versioned_fingerprints():
    production = production_conversion_provenance()
    review = offline_curation_provenance()
    assert production["scope"] == "production-conversion"
    assert review["scope"] == "offline-curation"
    assert set(PRODUCTION_CONVERSION_FILES).intersection(OFFLINE_CURATION_FILES) == {
        "cad2gis_v3/implementation.py",
    }
    assert not any(
        "curation" in path or "provider" in path
        for path in PRODUCTION_CONVERSION_FILES
    )
    assert production["sha256"] != review["sha256"]


def test_external_plain_http_and_resource_limits_fail_closed():
    with pytest.raises(ProviderError, match="must use HTTPS"):
        ProviderConfig.from_env({
            "DEEPSEEK_API_KEY": "secret",
            "DEEPSEEK_BASE_URL": "http://example.com",
        })
    config = ProviderConfig.from_env({
        "DEEPSEEK_API_KEY": "secret",
        "CAD2GIS_LLM_MAX_RESPONSE_BYTES": "1024",
    })
    assert "secret" not in repr(config)
