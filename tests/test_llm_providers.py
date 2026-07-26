from __future__ import annotations

import json
import urllib.error

import pytest

from cad2gis.cad2gis_v3.curation_providers import (
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderError,
    ReviewRequest,
)


def _request() -> ReviewRequest:
    return ReviewRequest(
        system_prompt="Return one JSON object and no prose.",
        context={"task_id": "task:1", "candidate_ids": ["candidate:1"]},
        json_schema={
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
            "additionalProperties": False,
        },
    )


def test_deepseek_profile_uses_current_v4_json_mode_contract() -> None:
    config = ProviderConfig.from_env({
        "DEEPSEEK_API_KEY": "secret-value",
    })

    assert config.provider == "deepseek"
    assert config.base_url == "https://api.deepseek.com"
    assert config.endpoint == "https://api.deepseek.com/chat/completions"
    assert config.model == "deepseek-v4-flash"
    assert config.capability == "json_object"
    assert config.token_field == "max_tokens"
    assert config.disable_thinking is True
    assert "secret-value" not in repr(config)

    payload = OpenAICompatibleProvider(config)._payload(_request())
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 4096
    assert payload["temperature"] == 0


def test_new_api_profile_supports_root_and_v1_gateway_urls() -> None:
    common = {
        "NEW_API_API_KEY": "gateway-secret",
        "NEW_API_MODEL": "gateway-model",
        "NEW_API_CAPABILITY": "json_schema",
    }
    root = ProviderConfig.from_env(
        {**common, "NEW_API_BASE_URL": "https://gateway.example.test"},
        provider="new_api",
    )
    versioned = ProviderConfig.from_env(
        {**common, "NEW_API_BASE_URL": "https://gateway.example.test/v1"},
        provider="new_api",
    )

    assert root.endpoint == "https://gateway.example.test/v1/chat/completions"
    assert versioned.endpoint == "https://gateway.example.test/v1/chat/completions"
    payload = OpenAICompatibleProvider(root)._payload(_request())
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["max_completion_tokens"] == 4096
    assert "thinking" not in payload


def test_provider_transport_records_hashes_without_exposing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return json.dumps({
                "id": "response-1",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps({"candidate_id": "candidate:1"}),
                    },
                }],
            }).encode("utf-8")

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = ProviderConfig.from_env({
        "DEEPSEEK_API_KEY": "secret-value",
    })

    response = OpenAICompatibleProvider(config).review(_request())

    assert response.provider == "deepseek"
    assert response.model == "deepseek-v4-flash"
    assert response.content == '{"candidate_id": "candidate:1"}'
    assert len(response.request_sha256) == 64
    assert len(response.response_sha256) == 64
    assert captured["request"].get_header("Authorization") == "Bearer secret-value"
    assert captured["timeout"] == 60
    assert "secret-value" not in repr(response)


def test_provider_transport_returns_sanitised_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_urlopen(_request, *, timeout):
        raise urllib.error.URLError(f"network failed after {timeout}")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    provider = OpenAICompatibleProvider(ProviderConfig.from_env({
        "DEEPSEEK_API_KEY": "secret-value",
    }))

    with pytest.raises(ProviderError, match="URLError") as caught:
        provider.review(_request())

    assert "secret-value" not in str(caught.value)
