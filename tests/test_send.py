"""End-to-end tests for ``send()`` against a loopback fake runner server.

``send()`` talks to a plain socket when ``RUNNER_SERVER_CERT_PATH`` is unset, so the
whole function -- tarball construction, framing, log streaming, output extraction --
can be exercised without SSL or a real solver.

The test that matters is ``test_concurrent_isolated_sends_do_not_cross_over``:
``send()``'s legacy path calls ``os.chdir()``, which is process-global, so two
concurrent submissions interleave their chdir() calls and each tars the other's
directory. ``isolate_cwd=True`` is what makes concurrent use safe.
"""

import io
import json
import logging
import os
import socket
import tarfile
import threading
from typing import Dict, List, Optional, Tuple

import pytest

from solver_runner_client.client import send

END_SEQUENCE = b"__ENDSEQUENCE__"


def _output_tarball(echo: str) -> bytes:
    """Build the ``output.json`` + ``output.mst`` tarball the server replies with.

    Order matters: the client returns ``getnames()[0]`` and ``[1]`` positionally.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in (
            ("output.json", json.dumps({"echo": echo}).encode()),
            ("output.mst", b""),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class FakeRunner:
    """Accepts connections, echoes back whatever ``.mps`` it was sent."""

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port: int = self._sock.getsockname()[1]
        # member names of each received archive, keyed by the .mps content
        self.received: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        self._threads: List[threading.Thread] = []
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._closed = False
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            thread = threading.Thread(target=self._handle, args=(conn,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            message = b""
            while END_SEQUENCE not in message:
                chunk = conn.recv(16384)
                if not chunk:
                    break
                message += chunk

            archive_bytes = message.split(END_SEQUENCE)[0]
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
                names = archive.getnames()
                mps_name = next(n for n in names if n.endswith(".mps"))
                extracted = archive.extractfile(mps_name)
                assert extracted is not None
                marker = extracted.read().decode()

            with self._lock:
                self.received[marker] = names

            conn.sendall(b"a log line from the fake runner\n")
            conn.sendall(b"output:")
            conn.sendall(_output_tarball(marker))

    def close(self) -> None:
        self._closed = True
        self._sock.close()


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    server = FakeRunner()
    monkeypatch.setenv("RUNNER_SERVER_HOSTNAME", "127.0.0.1")
    monkeypatch.setenv("RUNNER_SERVER_PORT", str(server.port))
    monkeypatch.delenv("RUNNER_SERVER_CERT_PATH", raising=False)
    yield server
    server.close()


@pytest.fixture(autouse=True)
def restore_cwd():
    """``send()``'s legacy path mutates the process CWD -- put it back."""
    original = os.getcwd()
    yield
    os.chdir(original)


def _write_inputs(directory, marker: str) -> List[str]:
    mps = directory / f"{marker}.mps"
    mps.write_text(marker)
    prm = directory / f"{marker}.main.prm"
    prm.write_text("TimeLimit 1\n")
    return [str(mps), str(prm)]


def _echo_of(output_json_path: str) -> str:
    with open(output_json_path) as handle:
        return json.load(handle)["echo"]


def test_isolated_send_round_trip(runner: FakeRunner, tmp_path) -> None:
    paths = _write_inputs(tmp_path, "alpha")

    output_json, output_mst = send(paths, isolate_cwd=True)

    assert _echo_of(output_json) == "alpha"
    assert output_mst.endswith("output.mst")
    # entries are flat -- no directory components leak into the tarball
    assert sorted(runner.received["alpha"]) == ["alpha.main.prm", "alpha.mps"]


def test_isolated_send_leaves_the_working_directory_alone(
    runner: FakeRunner, tmp_path
) -> None:
    before = os.getcwd()

    send(_write_inputs(tmp_path, "beta"), isolate_cwd=True)

    assert os.getcwd() == before


def test_legacy_send_is_unchanged(runner: FakeRunner, tmp_path) -> None:
    """The default path keeps its behaviour, CWD side effect included."""
    before = os.getcwd()

    output_json, _ = send(_write_inputs(tmp_path, "gamma"))

    assert _echo_of(output_json) == "gamma"
    assert sorted(runner.received["gamma"]) == ["gamma.main.prm", "gamma.mps"]
    # documented side effect: send() leaves the CWD inside its own temp dir
    assert os.getcwd() != before


def test_isolated_send_accepts_inputs_from_several_directories(
    runner: FakeRunner, tmp_path
) -> None:
    """The legacy chdir only ever visits the *first* input's directory."""
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    mps = first / "delta.mps"
    mps.write_text("delta")
    prm = second / "delta.main.prm"
    prm.write_text("TimeLimit 1\n")

    send([str(mps), str(prm)], isolate_cwd=True)

    assert sorted(runner.received["delta"]) == ["delta.main.prm", "delta.mps"]


def test_concurrent_isolated_sends_do_not_cross_over(
    runner: FakeRunner, tmp_path
) -> None:
    """Each concurrent caller must get its own model back, not its neighbour's."""
    markers = [f"job{index}" for index in range(6)]
    inputs = {}
    for marker in markers:
        directory = tmp_path / marker
        directory.mkdir()
        inputs[marker] = _write_inputs(directory, marker)

    results: Dict[str, Optional[str]] = {}
    errors: Dict[str, BaseException] = {}

    def run(marker: str) -> None:
        try:
            output_json, _ = send(inputs[marker], isolate_cwd=True)
            results[marker] = _echo_of(output_json)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors[marker] = exc

    threads = [threading.Thread(target=run, args=(marker,)) for marker in markers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"send() raised: {errors}"
    assert results == {marker: marker for marker in markers}
    for marker in markers:
        assert sorted(runner.received[marker]) == [
            f"{marker}.main.prm",
            f"{marker}.mps",
        ]


def test_per_call_logger_receives_the_streamed_lines(
    runner: FakeRunner, tmp_path
) -> None:
    records: List[Tuple[str, str]] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append((record.name, record.getMessage()))

    logger = logging.getLogger("solver_runner_client.client.probe")
    logger.setLevel(logging.INFO)
    handler = Capture()
    logger.addHandler(handler)
    try:
        send(_write_inputs(tmp_path, "epsilon"), logger=logger, isolate_cwd=True)
    finally:
        logger.removeHandler(handler)

    assert records, "the per-call logger received nothing"
    assert all(name == "solver_runner_client.client.probe" for name, _ in records)
    assert any("fake runner" in message for _, message in records)
