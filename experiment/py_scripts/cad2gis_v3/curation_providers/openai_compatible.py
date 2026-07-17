"""Single HTTP adapter shared by DeepSeek and New API profiles."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any

from .base import ProviderError, ReviewRequest, ReviewResponse
from .config import ProviderConfig


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


class OpenAICompatibleProvider:
    """OpenAI Chat Completions transport with explicit output capability."""

    protocol = "openai-chat-completions"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def _payload(self, request: ReviewRequest) -> dict[str, Any]:
        if self.config.capability == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": dict(request.json_schema),
                },
            }
        elif self.config.capability == "json_object":
            response_format = {"type": "json_object"}
        else:  # Defensive even though configuration is fail-closed.
            raise ProviderError("Unsupported structured-output capability")

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        request.context,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "response_format": response_format,
            self.config.token_field: self.config.max_completion_tokens,
            "stream": False,
            "temperature": 0,
        }
        if self.config.disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        return payload

    def review(self, request: ReviewRequest) -> ReviewResponse:
        payload = self._payload(request)
        request_bytes = _canonical_bytes(payload)
        if len(request_bytes) > self.config.max_input_bytes:
            raise ProviderError(
                "LLM review request exceeds CAD2GIS_LLM_MAX_INPUT_BYTES"
            )
        http_request = urllib.request.Request(
            self.config.endpoint,
            data=request_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                http_request, timeout=self.config.timeout_s,
            ) as response:
                raw = response.read(self.config.max_response_bytes + 1)
                status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"LLM provider request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Never interpolate request/config objects: their headers contain the key.
            raise ProviderError(
                f"LLM provider request failed: {type(exc).__name__}"
            ) from None

        if not 200 <= status < 300:
            raise ProviderError(f"LLM provider request failed with HTTP {status}")
        if len(raw) > self.config.max_response_bytes:
            raise ProviderError(
                "LLM provider response exceeds CAD2GIS_LLM_MAX_RESPONSE_BYTES"
            )
        try:
            response_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("LLM provider response is not valid UTF-8 JSON") from exc
        if not isinstance(response_payload, dict):
            raise ProviderError("LLM provider response root must be an object")
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderError("LLM provider response must contain exactly one choice")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("LLM provider choice must be an object")
        if choice.get("finish_reason") != "stop":
            raise ProviderError("LLM provider response did not finish normally")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("LLM provider choice is missing a message")
        if message.get("refusal"):
            raise ProviderError("LLM provider refused the review task")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("LLM provider message content must be a JSON string")
        response_id = response_payload.get("id", "")
        if not isinstance(response_id, str):
            response_id = ""
        return ReviewResponse(
            content=content,
            provider=self.config.provider,
            protocol=self.protocol,
            model=self.config.model,
            capability=self.config.capability,
            base_url_profile_sha256=hashlib.sha256(
                self.config.base_url.encode("utf-8")
            ).hexdigest(),
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            response_sha256=hashlib.sha256(raw).hexdigest(),
            response_id=response_id[:256],
        )

