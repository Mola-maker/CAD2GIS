"""Provider-neutral port for proposal-only CAD evidence review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """A sanitised provider/configuration failure safe for CLI reporting."""


@dataclass(frozen=True)
class ReviewRequest:
    """Sanitised application request; never contains a DWG path or geometry."""

    system_prompt: str
    context: Mapping[str, Any]
    json_schema: Mapping[str, Any]
    schema_name: str = "cad2gis_curation_proposal"

    def __post_init__(self) -> None:
        if not self.system_prompt.strip():
            raise ValueError("ReviewRequest.system_prompt must be non-empty")
        if not self.schema_name.strip():
            raise ValueError("ReviewRequest.schema_name must be non-empty")
        if not isinstance(self.context, Mapping):
            raise TypeError("ReviewRequest.context must be a mapping")
        if not isinstance(self.json_schema, Mapping):
            raise TypeError("ReviewRequest.json_schema must be a mapping")


@dataclass(frozen=True)
class ReviewResponse:
    """Normalised provider response plus secret-free provenance."""

    content: str
    provider: str
    protocol: str
    model: str
    capability: str
    base_url_profile_sha256: str
    request_sha256: str
    response_sha256: str
    response_id: str


@runtime_checkable
class ReviewProvider(Protocol):
    """Hexagonal application port implemented by model adapters."""

    def review(self, request: ReviewRequest) -> ReviewResponse:
        """Review one task and return one structured proposal document."""

