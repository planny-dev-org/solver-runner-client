"""Envelope codec tests, including the fixture shared with solver-runner-server.

``tests/fixtures/envelopes.jsonl`` is committed identically in both repos. The server
encodes envelopes with its own ~20-line emitter rather than importing this package, so
the fixture is what keeps the two implementations agreeing without coupling the repos:
each side asserts against the same expected wire lines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

from solver_runner_client.events import (
    FORMAT_VERSION,
    MAX_PAYLOAD_BYTES,
    SENTINEL,
    Event,
    EventFormatError,
    decode,
    encode,
    has_sentinel,
    parse_event,
)

FIXTURE = Path(__file__).parent / "fixtures" / "envelopes.jsonl"


def _fixture_rows() -> List[Dict[str, Any]]:
    with FIXTURE.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _ids() -> Iterator[str]:
    for row in _fixture_rows():
        yield f"{row['kind']}-{row['level']}{'-truncated' if row['truncated'] else ''}"


@pytest.mark.parametrize("row", _fixture_rows(), ids=list(_ids()))
def test_fixture_lines_decode_as_expected(row: Dict[str, Any]) -> None:
    """The shared contract: these exact wire lines mean these exact events."""
    event = decode(row["line"])

    assert event.kind == row["kind"]
    assert event.level == row["level"]
    assert event.data == row["data"]
    assert event.truncated is row["truncated"]


@pytest.mark.parametrize("row", _fixture_rows(), ids=list(_ids()))
def test_fixture_lines_round_trip(row: Dict[str, Any]) -> None:
    """encode() reproduces the fixture line, so the emitter has a reference to match."""
    if row["truncated"]:
        pytest.skip("truncation is produced by an oversized payload, not by encode(data)")
    assert encode(row["kind"], row["data"], row["level"]) == row["line"]


def test_round_trip_preserves_nested_data() -> None:
    data: Dict[str, Any] = {
        "total": 7,
        "counts": {"coverage": 3, "in_a_row": 4},
        "skipped": 2,
        "eval_ms": 11,
    }

    event = decode(encode("violations", data))

    assert event == Event(kind="violations", level="INFO", data=data, truncated=False)


def test_plain_solver_output_is_not_an_event() -> None:
    """parse_event returns None for text so callers can pass it straight through."""
    for line in (
        "Gurobi Optimizer version 12.0.3 build v12.0.3rc0 (linux64)",
        "",
        "Found heuristic solution: objective 1234.0000000",
        # the sentinel is matched line-anchored: mid-line it is just text
        f"solver said {SENTINEL} which is not an envelope",
    ):
        assert parse_event(line) is None
        assert has_sentinel(line) is False


def test_sentinel_must_be_line_anchored() -> None:
    assert has_sentinel(f"{SENTINEL} {{}}") is True
    assert has_sentinel(f" {SENTINEL} {{}}") is False


def test_malformed_envelope_raises_rather_than_being_dropped() -> None:
    """A broken envelope is a bug to surface, not text to pass through."""
    cases = [
        f"{SENTINEL} not json at all",
        f"{SENTINEL} [1, 2, 3]",
        f'{SENTINEL} {{"v":1,"kind":"","level":"INFO","data":{{}}}}',
        f'{SENTINEL} {{"v":1,"level":"INFO","data":{{}}}}',
        f'{SENTINEL} {{"v":1,"kind":"x","level":"NOPE","data":{{}}}}',
        f'{SENTINEL} {{"v":1,"kind":"x","level":"INFO","data":[1]}}',
    ]
    for line in cases:
        with pytest.raises(EventFormatError):
            parse_event(line)


def test_unsupported_version_is_rejected_not_guessed() -> None:
    line = f'{SENTINEL} {{"v":{FORMAT_VERSION + 1},"kind":"x","level":"INFO","data":{{}}}}'

    with pytest.raises(EventFormatError, match="unsupported envelope version"):
        parse_event(line)


def test_missing_version_is_rejected() -> None:
    with pytest.raises(EventFormatError, match="unsupported envelope version"):
        parse_event(f'{SENTINEL} {{"kind":"x","level":"INFO","data":{{}}}}')


def test_null_data_decodes_as_empty() -> None:
    event = decode(f'{SENTINEL} {{"v":1,"kind":"x","level":"INFO","data":null}}')

    assert event.data == {}


def test_level_defaults_to_info_when_absent() -> None:
    event = decode(f'{SENTINEL} {{"v":1,"kind":"x","data":{{}}}}')

    assert event.level == "INFO"


def test_oversized_payload_is_truncated_not_dropped() -> None:
    line = encode("violations", {"blob": "x" * (MAX_PAYLOAD_BYTES * 2)})

    assert len(line.encode("utf-8")) < MAX_PAYLOAD_BYTES + len(SENTINEL) + 2
    event = decode(line)
    assert event.truncated is True
    assert event.kind == "violations"
    assert event.data == {}


def test_encoded_events_are_always_one_line() -> None:
    """Framing splits on newlines, so an envelope containing one would be shredded."""
    line = encode("violations", {"message": "first\nsecond\r\nthird", "tab": "a\tb"})

    assert "\n" not in line
    assert "\r" not in line
    assert decode(line).data["message"] == "first\nsecond\r\nthird"


def test_non_serialisable_data_raises() -> None:
    with pytest.raises(EventFormatError, match="not JSON-serialisable"):
        encode("violations", {"bad": object()})


def test_decode_rejects_a_line_without_the_sentinel() -> None:
    with pytest.raises(EventFormatError, match="does not start with"):
        decode('{"v":1,"kind":"x","level":"INFO","data":{}}')
