"""Line framing tests for ``send(frame_lines=...)``.

Two things are being pinned here:

* ``frame_lines=True`` produces one log record per line, which is what makes
  sentinel-matched envelopes recoverable from the stream.
* ``frame_lines=False`` keeps the historical chunk behaviour, which is the
  compatibility guarantee for every existing caller. That guarantee is a test, not a
  claim -- see ``test_default_path_keeps_chunk_segmentation``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import tarfile
import threading
from typing import List, Optional, Sequence

import pytest

from solver_runner_client.client import _emit_lines, send
from solver_runner_client.events import encode, parse_event

END_SEQUENCE = b"__ENDSEQUENCE__"


# --------------------------------------------------------------------------- #
# _emit_lines: the framing primitive, tested without a socket
# --------------------------------------------------------------------------- #


class Recorder(logging.Handler):
    """Collects emitted messages in order."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def logger(recorder: Recorder, request: pytest.FixtureRequest) -> logging.Logger:
    log = logging.getLogger(f"framing-test.{request.node.name}")
    log.handlers = [recorder]
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def test_emit_lines_returns_the_unterminated_remainder(
    logger: logging.Logger, recorder: Recorder
) -> None:
    remainder = _emit_lines(logger, b"complete\npartia", flush=False)

    assert recorder.messages == ["complete"]
    assert remainder == b"partia"


def test_emit_lines_flush_emits_the_remainder(
    logger: logging.Logger, recorder: Recorder
) -> None:
    remainder = _emit_lines(logger, b"trailing without newline", flush=True)

    assert recorder.messages == ["trailing without newline"]
    assert remainder == b""


def test_emit_lines_preserves_blank_lines(
    logger: logging.Logger, recorder: Recorder
) -> None:
    """Blanks are what make the framed stream *render* like the chunked one."""
    _emit_lines(logger, b"a\n\nb\n", flush=False)

    assert recorder.messages == ["a", "", "b"]


def test_emit_lines_does_not_emit_an_empty_remainder(
    logger: logging.Logger, recorder: Recorder
) -> None:
    remainder = _emit_lines(logger, b"a\n", flush=True)

    assert recorder.messages == ["a"]
    assert remainder == b""


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 16, 64, 1024])
def test_framing_is_independent_of_chunk_boundaries(
    logger: logging.Logger, recorder: Recorder, chunk_size: int
) -> None:
    """Whatever the chunking, the framed lines reassemble the original text."""
    text = "alpha\nbeta\n\ngamma with spaces\ndelta\n"
    payload = text.encode()

    buffer = b""
    for start in range(0, len(payload), chunk_size):
        buffer = _emit_lines(logger, buffer + payload[start : start + chunk_size], flush=False)
    _emit_lines(logger, buffer, flush=True)

    assert "\n".join(recorder.messages) + "\n" == text


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 11])
def test_multibyte_utf8_split_across_chunks_survives(
    logger: logging.Logger, recorder: Recorder, chunk_size: int
) -> None:
    """The unframed path raises UnicodeDecodeError here; framing decodes whole lines."""
    text = "Zürich – Genève\nmüller\n"
    payload = text.encode()

    buffer = b""
    for start in range(0, len(payload), chunk_size):
        buffer = _emit_lines(logger, buffer + payload[start : start + chunk_size], flush=False)
    _emit_lines(logger, buffer, flush=True)

    assert recorder.messages == ["Zürich – Genève", "müller"]


def test_envelope_split_across_chunks_is_recoverable(
    logger: logging.Logger, recorder: Recorder
) -> None:
    """The whole point of framing: a split envelope must still parse."""
    line = encode("violations", {"total": 7, "counts": {"coverage": 3}})
    payload = (line + "\n").encode()
    midpoint = len(payload) // 2

    buffer = _emit_lines(logger, payload[:midpoint], flush=False)
    _emit_lines(logger, buffer + payload[midpoint:], flush=False)

    assert len(recorder.messages) == 1
    event = parse_event(recorder.messages[0])
    assert event is not None
    assert event.data["total"] == 7


# --------------------------------------------------------------------------- #
# send(): framing over a real socket
# --------------------------------------------------------------------------- #


def _output_tarball() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in (("output.json", json.dumps({"ok": True}).encode()), ("output.mst", b"")):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class ScriptedRunner:
    """Replies with a fixed sequence of byte writes, then the output tarball.

    Writing in scripted pieces is what lets a test place a line boundary, or the
    ``output:`` marker, in the middle of a chunk.
    """

    def __init__(self, writes: Sequence[bytes]) -> None:
        self._writes = list(writes)
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port: int = self._sock.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            message = b""
            while END_SEQUENCE not in message:
                chunk = conn.recv(16384)
                if not chunk:
                    return
                message += chunk
            for write in self._writes:
                conn.sendall(write)
            conn.sendall(_output_tarball())

    def close(self) -> None:
        self._closed = True
        self._sock.close()


def _run(
    writes: Sequence[bytes],
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
    recorder: Recorder,
    log: logging.Logger,
    *,
    frame_lines: bool,
) -> None:
    server = ScriptedRunner(writes)
    monkeypatch.setenv("RUNNER_SERVER_HOSTNAME", "127.0.0.1")
    monkeypatch.setenv("RUNNER_SERVER_PORT", str(server.port))
    monkeypatch.delenv("RUNNER_SERVER_CERT_PATH", raising=False)
    mps = tmp_path / "model.mps"  # type: ignore[operator]
    mps.write_text("model")
    try:
        send([str(mps)], logger=log, isolate_cwd=True, frame_lines=frame_lines)
    finally:
        server.close()


def _streamed(recorder: Recorder) -> List[str]:
    """Only the server's streamed lines, not send()'s own two status messages."""
    return [
        message
        for message in recorder.messages
        if not message.startswith(("sending input archive", "sent "))
    ]


def test_framed_send_emits_one_record_per_line(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, recorder: Recorder, logger: logging.Logger
) -> None:
    _run(
        [b"first line\nsecond li", b"ne\nthird line\n", b"output:"],
        tmp_path,
        monkeypatch,
        recorder,
        logger,
        frame_lines=True,
    )

    assert _streamed(recorder) == ["first line", "second line", "third line"]


def test_framed_send_recovers_an_envelope_split_across_writes(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, recorder: Recorder, logger: logging.Logger
) -> None:
    line = encode("violations", {"total": 3, "counts": {"rest": 3}})
    payload = (line + "\n").encode()
    cut = len(payload) // 2

    _run(
        [b"Gurobi log line\n", payload[:cut], payload[cut:], b"output:"],
        tmp_path,
        monkeypatch,
        recorder,
        logger,
        frame_lines=True,
    )

    streamed = _streamed(recorder)
    events = [event for event in (parse_event(m) for m in streamed) if event is not None]
    assert len(events) == 1
    assert events[0].kind == "violations"
    assert events[0].data["total"] == 3
    assert "Gurobi log line" in streamed


def test_framed_send_flushes_a_line_with_no_trailing_newline(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, recorder: Recorder, logger: logging.Logger
) -> None:
    _run([b"no trailing newline", b"output:"], tmp_path, monkeypatch, recorder, logger, frame_lines=True)

    assert _streamed(recorder) == ["no trailing newline"]


def test_output_marker_split_across_writes_is_still_detected(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, recorder: Recorder, logger: logging.Logger
) -> None:
    """A marker straddling a boundary must not leak the binary tarball into the log."""
    _run([b"a log line\nout", b"put:"], tmp_path, monkeypatch, recorder, logger, frame_lines=True)

    streamed = _streamed(recorder)
    assert streamed == ["a log line"]
    assert not any("output:" in message for message in streamed)


def test_default_path_keeps_chunk_segmentation(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, recorder: Recorder, logger: logging.Logger
) -> None:
    """The compatibility guarantee: unframed still logs whole chunks, newlines and all.

    Asserted as an invariant rather than an exact segmentation, because TCP is free to
    coalesce the server's writes into one recv() -- what must hold is that records are
    chunk-shaped (interior newlines retained inside a record) rather than one per line.
    """
    _run(
        [b"first line\nsecond li", b"ne\nthird line\n", b"output:"],
        tmp_path,
        monkeypatch,
        recorder,
        logger,
        frame_lines=False,
    )

    streamed = _streamed(recorder)
    assert any("\n" in message for message in streamed), (
        "unframed records must retain interior newlines"
    )


def test_framed_records_never_contain_newlines(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, recorder: Recorder, logger: logging.Logger
) -> None:
    """The invariant the envelope matcher depends on: one record is exactly one line."""
    _run(
        [b"alpha\nbe", b"ta\n\ngamma\n", b"output:"],
        tmp_path,
        monkeypatch,
        recorder,
        logger,
        frame_lines=True,
    )

    streamed = _streamed(recorder)
    assert all("\n" not in message for message in streamed)
    assert streamed == ["alpha", "beta", "", "gamma"]


def test_unframed_path_loses_newlines_at_chunk_boundaries(
    logger: logging.Logger, recorder: Recorder
) -> None:
    """Pre-existing defect that framing repairs, pinned so the difference is explicit.

    The unframed path logs ``chunk.decode().rstrip("\n")``, which strips *trailing*
    newlines. When a chunk boundary coincides with a line boundary the newline is gone
    from the record, so concatenating records glues two lines into one. Framing keeps
    them separate.
    """
    chunks = [b"a\n", b"b\n"]

    unframed = "".join(chunk.decode().rstrip("\n") for chunk in chunks)
    assert unframed == "ab", "the defect: the line break vanished"

    buffer = b""
    for chunk in chunks:
        buffer = _emit_lines(logger, buffer + chunk, flush=False)
    _emit_lines(logger, buffer, flush=True)

    assert recorder.messages == ["a", "b"], "framing keeps the lines apart"


def test_framed_lines_reassemble_the_original_text(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, recorder: Recorder, logger: logging.Logger
) -> None:
    """Framing changes segmentation, never content."""
    text = "alpha\nbeta\n\ngamma with spaces\n"

    _run([text.encode(), b"output:"], tmp_path, monkeypatch, recorder, logger, frame_lines=True)

    assert "\n".join(_streamed(recorder)) + "\n" == text
