"""Explicit terminal accounting for source entities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from .model import SourceEntity


class TerminalState(str, Enum):
    ACCEPTED = "accepted"
    UNSUPPORTED = "unsupported"
    ABSTAINED = "abstained"
    ERRORED = "errored"


@dataclass(frozen=True)
class EntityAccounting:
    entity_key: str
    state: TerminalState
    reasons: tuple[str, ...]


def _reasons(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set)):
        return ()
    cleaned = {str(value).strip() for value in values}
    return tuple(sorted(value for value in cleaned if value))


def _account_entity(entity: SourceEntity) -> EntityAccounting:
    entity_key = entity.entity_key
    if not entity_key.strip():
        raise ValueError("blank source entity key")

    status = entity.reader_backend_status.strip()
    status_folded = status.casefold()
    unsupported = _reasons(entity.raw_properties.get("unsupported_reasons"))

    if status_folded.startswith(("error", "fail")):
        state = TerminalState.ERRORED
        reasons = (status,) if status else ()
    elif status_folded.startswith("abstain"):
        state = TerminalState.ABSTAINED
        reasons = (status,) if status else ()
    elif status_folded.startswith("unsupported"):
        state = TerminalState.UNSUPPORTED
        reasons = tuple(sorted({status, *unsupported} - {""}))
    elif unsupported:
        state = TerminalState.UNSUPPORTED
        reasons = unsupported
    else:
        state = TerminalState.ACCEPTED
        reasons = ()
    return EntityAccounting(entity_key=entity_key, state=state, reasons=reasons)


def account_entities(entities: Iterable[SourceEntity]) -> list[EntityAccounting]:
    """Assign one deterministic terminal state to every source entity."""
    records: list[EntityAccounting] = []
    seen: set[str] = set()
    for entity in entities:
        record = _account_entity(entity)
        if record.entity_key in seen:
            raise ValueError(f"duplicate source entity key: {record.entity_key}")
        seen.add(record.entity_key)
        records.append(record)
    return sorted(records, key=lambda record: record.entity_key)


def summarize_accounting(records: Iterable[EntityAccounting]) -> dict[str, int]:
    """Count terminal states while enforcing unique, nonblank entity keys."""
    counts = {
        "accepted": 0,
        "unsupported": 0,
        "abstained": 0,
        "errored": 0,
    }
    seen: set[str] = set()
    total = 0
    for record in records:
        if not isinstance(record, EntityAccounting):
            raise ValueError("accounting records must be EntityAccounting instances")
        if not record.entity_key.strip():
            raise ValueError("blank source entity key")
        if record.entity_key in seen:
            raise ValueError(f"duplicate source entity key: {record.entity_key}")
        if not isinstance(record.state, TerminalState):
            raise ValueError(f"unexpected terminal state: {record.state!r}")
        seen.add(record.entity_key)
        counts[record.state.value] += 1
        total += 1

    if sum(counts.values()) != total:
        raise ValueError("terminal state counts do not sum to total")
    return {**counts, "total": total}
