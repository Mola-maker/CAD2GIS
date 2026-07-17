"""Fail-closed provider profiles for the optional review lane."""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Mapping

from .base import ProviderError


SUPPORTED_PROVIDERS = ("deepseek", "new_api")
SUPPORTED_CAPABILITIES = ("json_object", "json_schema")


def _bounded_int(
    env: Mapping[str, str], name: str, default: int, low: int, high: int,
) -> int:
    raw = str(env.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProviderError(f"{name} must be an integer") from exc
    if not low <= value <= high:
        raise ProviderError(f"{name} must be between {low} and {high}")
    return value


def _required(env: Mapping[str, str], *names: str) -> dict[str, str]:
    values = {name: str(env.get(name, "")).strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ProviderError(
            "LLM review is disabled; missing environment settings: "
            + ", ".join(missing)
        )
    return values


def _validate_base_url(value: str, variable: str) -> str:
    base_url = value.rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError(
            f"{variable} must not contain credentials, query, or fragment"
        )
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ProviderError(
            f"{variable} must use HTTPS (HTTP is allowed only for loopback tests)"
        )
    if not parsed.netloc:
        raise ProviderError(f"{variable} is invalid")
    return base_url


def _endpoint(base_url: str, provider: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if provider == "deepseek":
        suffix = "/chat/completions"
    elif path.endswith("/v1"):
        suffix = "/chat/completions"
    else:
        suffix = "/v1/chat/completions"
    return f"{base_url}{suffix}"


@dataclass(frozen=True)
class ProviderConfig:
    """One immutable provider profile; the API key is excluded from repr."""

    provider: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    capability: str
    endpoint: str
    token_field: str
    disable_thinking: bool
    timeout_s: int = 60
    max_input_bytes: int = 262_144
    max_response_bytes: int = 262_144
    max_completion_tokens: int = 4096

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, provider: str | None = None,
    ) -> "ProviderConfig":
        source = os.environ if env is None else env
        provider_id = (
            provider or str(source.get("CAD2GIS_LLM_PROVIDER", "deepseek"))
        ).strip().casefold()
        if provider_id not in SUPPORTED_PROVIDERS:
            raise ProviderError(
                "CAD2GIS_LLM_PROVIDER must be one of: "
                + ", ".join(SUPPORTED_PROVIDERS)
            )

        if provider_id == "deepseek":
            values = _required(source, "DEEPSEEK_API_KEY")
            base_url = _validate_base_url(
                str(source.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).strip(),
                "DEEPSEEK_BASE_URL",
            )
            model = str(source.get("DEEPSEEK_MODEL", "deepseek-v4-flash")).strip()
            if not model:
                raise ProviderError("DEEPSEEK_MODEL must be non-empty")
            api_key = values["DEEPSEEK_API_KEY"]
            capability = "json_object"
            token_field = "max_tokens"
            disable_thinking = True
        else:
            values = _required(
                source,
                "NEW_API_BASE_URL",
                "NEW_API_API_KEY",
                "NEW_API_MODEL",
                "NEW_API_CAPABILITY",
            )
            base_url = _validate_base_url(values["NEW_API_BASE_URL"], "NEW_API_BASE_URL")
            model = values["NEW_API_MODEL"]
            api_key = values["NEW_API_API_KEY"]
            capability = values["NEW_API_CAPABILITY"].casefold()
            if capability not in SUPPORTED_CAPABILITIES:
                raise ProviderError(
                    "NEW_API_CAPABILITY must be json_schema or json_object"
                )
            token_field = "max_completion_tokens"
            disable_thinking = False

        if "\r" in api_key or "\n" in api_key:
            raise ProviderError("Provider API key contains an invalid newline")
        common = {
            "timeout_s": _bounded_int(source, "CAD2GIS_LLM_TIMEOUT_S", 60, 1, 300),
            "max_input_bytes": _bounded_int(
                source, "CAD2GIS_LLM_MAX_INPUT_BYTES", 262_144, 1024, 16 * 1024 * 1024,
            ),
            "max_response_bytes": _bounded_int(
                source, "CAD2GIS_LLM_MAX_RESPONSE_BYTES", 262_144, 1024, 4 * 1024 * 1024,
            ),
            "max_completion_tokens": _bounded_int(
                source, "CAD2GIS_LLM_MAX_COMPLETION_TOKENS", 4096, 128, 65_536,
            ),
        }
        return cls(
            provider=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            capability=capability,
            endpoint=_endpoint(base_url, provider_id),
            token_field=token_field,
            disable_thinking=disable_thinking,
            **common,
        )


def load_provider_config(
    env: Mapping[str, str] | None = None, *, provider: str | None = None,
) -> ProviderConfig:
    """Create the selected provider profile from runtime-only settings."""

    return ProviderConfig.from_env(env, provider=provider)

