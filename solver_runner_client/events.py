"""Structured events carried on the runner's log stream.

The runner emits two kinds of output on one channel: the solver's own text, and
explicit machine-readable events. An event is a sentinel-prefixed, single-line JSON
envelope::

    @@PLANNY_LOG@@ {"v":1,"kind":"violations","level":"INFO","data":{...}}

Consumers demultiplex on the sentinel: :func:`parse_event` returns ``None`` for
ordinary solver text and an :class:`Event` for an envelope, so a caller can forward
events somewhere structured while leaving the text stream untouched.

The sentinel is matched **line-anchored**, so solver output cannot produce an event by
containing the marker mid-line. That is why reliable line framing (``frame_lines`` in
:func:`solver_runner_client.client.send`) is a prerequisite for using this module
against a live stream: a chunked stream can split an envelope across two records, and
half an envelope is not recoverable.

Malformed envelopes raise :class:`EventFormatError` rather than returning ``None``.
The distinction matters: ``None`` means "not an event, pass it through", while an
exception means "this was meant to be an event and the format is wrong", which is a
bug worth surfacing instead of silently dropping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Final, Literal, Optional, cast, get_args

SENTINEL: Final[str] = "@@PLANNY_LOG@@"
"""Line prefix marking a structured event. Chosen so the solver never emits it."""

FORMAT_VERSION: Final[int] = 1
"""Envelope version. Present from the first release so parsers can reject futures."""

MAX_PAYLOAD_BYTES: Final[int] = 8192
"""Cap on the JSON payload. Larger events are emitted truncated rather than dropped."""

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
"""Log levels, matching worker-client's ``LEVEL_LITERAL`` so they pass straight through."""

_LEVELS: Final[frozenset[str]] = frozenset(get_args(Level))

JsonDict = Dict[str, object]


def _empty_data() -> JsonDict:
    """Typed default for :attr:`Event.data` (``default_factory=dict`` is unparameterised)."""
    return {}


class EventFormatError(ValueError):
    """Raised when a line carries the sentinel but is not a well-formed envelope."""


@dataclass(frozen=True)
class Event:
    """One decoded event from the runner's log stream."""

    kind: str
    level: Level = "INFO"
    data: JsonDict = field(default_factory=_empty_data)
    truncated: bool = False


def has_sentinel(line: str) -> bool:
    """Whether ``line`` is an event envelope rather than ordinary solver text."""
    return line.startswith(SENTINEL)


def encode(kind: str, data: JsonDict, level: Level = "INFO") -> str:
    """Serialise one event to its single-line wire form.

    Payloads over :data:`MAX_PAYLOAD_BYTES` are replaced by a ``truncated`` marker
    envelope: an oversized line is worse than a clipped one, and dropping the event
    silently is worse than both.

    :raises EventFormatError: if ``data`` is not JSON-serialisable.
    """
    payload = _dumps({"v": FORMAT_VERSION, "kind": kind, "level": level, "data": data})
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        payload = _dumps(
            {
                "v": FORMAT_VERSION,
                "kind": kind,
                "level": level,
                "data": {},
                "truncated": True,
            }
        )
    return f"{SENTINEL} {payload}"


def parse_event(line: str) -> Optional[Event]:
    """Decode ``line`` if it is an event, else return ``None``.

    :raises EventFormatError: if the sentinel is present but the envelope is invalid.
    """
    if not has_sentinel(line):
        return None
    return decode(line)


def decode(line: str) -> Event:
    """Decode a line already known to carry the sentinel.

    :raises EventFormatError: on any malformed envelope.
    """
    if not has_sentinel(line):
        raise EventFormatError(f"line does not start with {SENTINEL!r}")

    try:
        raw: object = json.loads(line[len(SENTINEL) :].strip())
    except json.JSONDecodeError as exc:
        raise EventFormatError(f"payload is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise EventFormatError(f"payload must be a JSON object, got {type(raw).__name__}")
    envelope = cast(JsonDict, raw)

    version = envelope.get("v")
    if version != FORMAT_VERSION:
        raise EventFormatError(
            f"unsupported envelope version {version!r} (expected {FORMAT_VERSION})"
        )

    kind = envelope.get("kind")
    if not isinstance(kind, str) or not kind:
        raise EventFormatError(f"'kind' must be a non-empty string, got {kind!r}")

    level = envelope.get("level", "INFO")
    if not isinstance(level, str) or level not in _LEVELS:
        raise EventFormatError(f"'level' must be one of {sorted(_LEVELS)}, got {level!r}")

    data = envelope.get("data")
    if data is None:
        payload_data: JsonDict = {}
    elif isinstance(data, dict):
        payload_data = cast(JsonDict, data)
    else:
        raise EventFormatError(f"'data' must be an object or null, got {type(data).__name__}")

    return Event(
        kind=kind,
        level=cast(Level, level),
        data=payload_data,
        truncated=envelope.get("truncated") is True,
    )


def _dumps(envelope: JsonDict) -> str:
    """Compact, single-line JSON. Newlines in the result would break the framing."""
    try:
        payload = json.dumps(envelope, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise EventFormatError(f"event data is not JSON-serialisable: {exc}") from exc
    if "\n" in payload or "\r" in payload:
        # json.dumps escapes these inside strings, so reaching here means a key or
        # value smuggled a raw newline through a custom encoder.
        raise EventFormatError("event payload must not contain newlines")
    return payload


__all__ = [
    "SENTINEL",
    "FORMAT_VERSION",
    "MAX_PAYLOAD_BYTES",
    "Event",
    "EventFormatError",
    "Level",
    "JsonDict",
    "decode",
    "encode",
    "has_sentinel",
    "parse_event",
]
