from __future__ import annotations

import pytest

from cad2gis.cad2gis_v3.accounting import (
    EntityAccounting,
    TerminalState,
    account_entities,
    summarize_accounting,
)
from cad2gis.cad2gis_v3.model import SourceEntity


def _entity(
    key: str,
    *,
    status: str = "",
    unsupported_reasons: object = (),
) -> SourceEntity:
    return SourceEntity.from_record(
        {
            "entity_key": key,
            "source_sha256": "a" * 64,
            "source_file": "fixture.dwg",
            "handle": key,
            "layout": "Model",
            "layout_role": "model",
            "cad_role": "model",
            "layer": "0",
            "object_name": "AcDbLine",
            "dwg_type_name": "LINE",
            "points": [(0.0, 0.0), (1.0, 0.0)],
            "centroid": (0.0, 0.0),
            "raw_properties": {
                "reader_backend_status": status,
                "unsupported_reasons": unsupported_reasons,
            },
        }
    )


def test_accounts_each_terminal_state_and_sorts_keys() -> None:
    records = account_entities(
        [
            _entity("z-accepted"),
            _entity("x-unsupported", unsupported_reasons=["curve unavailable", "curve unavailable", " "]),
            _entity("y-abstained", status="ABSTAINED_BY_REVIEW"),
            _entity("w-errored", status="Failure while reading"),
        ]
    )

    assert [record.entity_key for record in records] == [
        "w-errored",
        "x-unsupported",
        "y-abstained",
        "z-accepted",
    ]
    assert {record.entity_key: record.state for record in records} == {
        "w-errored": TerminalState.ERRORED,
        "x-unsupported": TerminalState.UNSUPPORTED,
        "y-abstained": TerminalState.ABSTAINED,
        "z-accepted": TerminalState.ACCEPTED,
    }
    assert next(record for record in records if record.entity_key == "w-errored").reasons == (
        "Failure while reading",
    )
    assert next(record for record in records if record.entity_key == "x-unsupported").reasons == (
        "curve unavailable",
    )
    assert next(record for record in records if record.entity_key == "z-accepted").reasons == ()


def test_terminal_precedence_is_error_abstain_unsupported_accepted() -> None:
    records = account_entities(
        [
            _entity("accepted", unsupported_reasons=["not selected"]),
            _entity("unsupported", unsupported_reasons=["missing geometry"]),
            _entity("abstained", status="abstain pending", unsupported_reasons=["ignored"]),
            _entity("errored", status="ERROR then failed", unsupported_reasons=["ignored"]),
        ]
    )
    states = {record.entity_key: record.state for record in records}
    assert states == {
        "accepted": TerminalState.UNSUPPORTED,
        "unsupported": TerminalState.UNSUPPORTED,
        "abstained": TerminalState.ABSTAINED,
        "errored": TerminalState.ERRORED,
    }
    assert next(record for record in records if record.entity_key == "abstained").reasons == (
        "abstain pending",
    )
    assert next(record for record in records if record.entity_key == "errored").reasons == (
        "ERROR then failed",
    )


def test_blank_unsupported_reasons_do_not_create_unsupported_state() -> None:
    record = account_entities([_entity("blank-reasons", unsupported_reasons=[" ", ""])])[0]

    assert record.state is TerminalState.ACCEPTED
    assert record.reasons == ()


def test_explicit_unsupported_status_is_terminal_with_status_reason() -> None:
    record = account_entities([_entity("explicit", status="UNSUPPORTED")])[0]

    assert record.state is TerminalState.UNSUPPORTED
    assert record.reasons == ("UNSUPPORTED",)


@pytest.mark.parametrize("status", ["authoritative", "com_direct", "supported"])
def test_success_status_does_not_enter_unsupported_reasons(status: str) -> None:
    record = account_entities(
        [_entity("backend-with-reason", status=status, unsupported_reasons=["missing geometry"])]
    )[0]

    assert record.state is TerminalState.UNSUPPORTED
    assert record.reasons == ("missing geometry",)


def test_success_status_without_reasons_is_accepted() -> None:
    records = account_entities([_entity("authoritative", status="authoritative")])

    assert records[0].state is TerminalState.ACCEPTED
    assert records[0].reasons == ()


def test_rejects_blank_and_duplicate_entity_keys() -> None:
    with pytest.raises(ValueError, match="blank source entity key"):
        account_entities([_entity(" ")])
    with pytest.raises(ValueError, match="duplicate source entity key: dup"):
        account_entities([_entity("dup"), _entity("dup")])


def test_summary_accepts_generators_and_rejects_invalid_records() -> None:
    entities = (_entity(key) for key in ("b", "a"))
    records = account_entities(entities)
    assert summarize_accounting(record for record in records) == {
        "accepted": 2,
        "unsupported": 0,
        "abstained": 0,
        "errored": 0,
        "total": 2,
    }

    with pytest.raises(ValueError, match="duplicate source entity key: a"):
        summarize_accounting([records[0], records[0]])
    with pytest.raises(ValueError, match="blank source entity key"):
        summarize_accounting([EntityAccounting("", TerminalState.ACCEPTED, ())])
    with pytest.raises(ValueError, match="unexpected terminal state"):
        summarize_accounting([EntityAccounting("invalid", "accepted", ())])  # type: ignore[arg-type]
