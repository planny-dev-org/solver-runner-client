import io
import os
import argparse
import socket
import tempfile
import ssl
import tarfile
import datetime
import logging
from typing import List, Optional, Sequence, Tuple

LOG = logging.getLogger(__name__)

END_SEQUENCE = b"__ENDSEQUENCE__"
OUTPUT_MARKER = b"output:"
RECV_SIZE = 1024


def _decode(chunk: bytes) -> str:
    """Decode one complete log line.

    ``errors="replace"`` matters only in the case that currently raises: the unframed
    path decodes arbitrary byte boundaries, so a multi-byte UTF-8 character split
    across two recv() chunks throws UnicodeDecodeError. Framing makes lines whole, and
    replacement covers anything genuinely malformed on the wire.
    """
    return chunk.decode("utf-8", errors="replace")


def _emit_lines(log: logging.Logger, buffer: bytes, *, flush: bool) -> bytes:
    """Emit every complete line in ``buffer``; return the unterminated remainder.

    Empty lines are emitted as empty records rather than skipped. The unframed path
    logs whole chunks whose text already contains interior newlines, so a terminal
    renders "a\n\nb" as three lines either way -- preserving the blanks is what keeps
    the *rendered* stream identical.

    With ``flush=True`` the trailing remainder is emitted too, for end of stream.
    """
    *complete, remainder = buffer.split(b"\n")
    for line in complete:
        log.info(_decode(line))
    if flush and remainder:
        log.info(_decode(remainder))
        return b""
    return remainder


parser = argparse.ArgumentParser(
    prog="client.py",
    description="Send an execution request to runner server.\n"
    "Run as a command or using client.send method.\n"
    "Following env vars must be set:\n"
    "RUNNER_SERVER_HOSTNAME => default to socket.gethostname()\n"
    "RUNNER_SERVER_PORT => default to 5050\n"
    "RUNNER_SERVER_CERT_PATH => path to server certificate file. If not set, an unsecured socket will be used\n"
    "RUNNER_CLIENT_CERT_PATH => path to client certificate file"
    "RUNNER_CLIENT_KEY_PATH => path to client certificate key file\n",
)
parser.add_argument("mps_file_path", help="file to run (.mps)")
parser.add_argument(
    "--parameters_file_paths",
    nargs="+",
    help="optional parameter files (*main.prm or *env_N.prm where N is an integer from 0 to 2)",
)
parser.add_argument("--relaxation_file_path", help="optional relaxation file (.json)")
parser.add_argument("--mst_file_path", help="optional mip start file (.mst)")


def send(
    input_file_abs_paths: Sequence[str],
    *,
    logger: Optional[logging.Logger] = None,
    isolate_cwd: bool = False,
    frame_lines: bool = False,
) -> Tuple[str, str]:
    """
    Send an execution request to the runner server and return the output paths.

    :param input_file_abs_paths: input files to tar and send (.mps mandatory)
    :param logger: logger to emit the server's streamed log lines on. Defaults to
        this module's logger, which is the historical behaviour. Pass a distinct
        logger to tell two concurrent submissions apart.
    :param isolate_cwd: when True, build the tarball without changing the process
        working directory. The default False keeps the historical behaviour,
        including the fact that send() leaves the CWD inside its temp dir.

        The working directory is process-global state, so the default path cannot
        be used by two concurrent send() calls in one process: they interleave
        their chdir() calls and each ends up tarring the other's directory. Pass
        isolate_cwd=True on *every* concurrent call site. The resulting tarball is
        equivalent -- entries are flat in both modes -- and as a side benefit
        input files are no longer required to share a single directory.
    :param frame_lines: when True, emit one log record per line of server output
        instead of one per recv() chunk. The default False keeps the historical
        chunk behaviour, with one deliberate exception: undecodable bytes are
        replaced rather than raising UnicodeDecodeError (see :func:`_decode`).

        The concatenated text is identical either way and a terminal renders both
        the same, because chunk records already contain interior newlines. What
        changes is record segmentation, and with it two things that need whole
        lines: machine-readable envelopes (see :mod:`solver_runner_client.events`)
        cannot be matched if a line may arrive in two pieces, and decoding is only
        safe once a line is whole -- the unframed path can split a multi-byte UTF-8
        character across chunks and raise UnicodeDecodeError.
    :return: (output json path, output mst path)
    """
    log = logger if logger is not None else LOG
    host = os.environ.get("RUNNER_SERVER_HOSTNAME", socket.gethostname())
    port = int(os.environ.get("RUNNER_SERVER_PORT", 5050))
    server_cert_path = os.environ.get("RUNNER_SERVER_CERT_PATH")
    client_cert_path = os.environ.get("RUNNER_CLIENT_CERT_PATH")
    key_path = os.environ.get("RUNNER_CLIENT_KEY_PATH")
    sock = socket.socket()  # instantiate
    sock.connect((host, port))  # connect to the server
    client_socket = sock
    if server_cert_path:
        if client_cert_path is None or key_path is None:
            # Previously this reached ssl as None and raised an opaque TypeError.
            raise ValueError(
                "RUNNER_SERVER_CERT_PATH is set, so RUNNER_CLIENT_CERT_PATH and "
                "RUNNER_CLIENT_KEY_PATH must be set too"
            )
        context = ssl.create_default_context()
        context.load_verify_locations(server_cert_path)
        context.load_cert_chain(client_cert_path, key_path)
        client_socket = context.wrap_socket(sock, server_hostname=host)

    # create a temp dir
    tmp_dir = tempfile.mkdtemp(
        prefix=datetime.datetime.now().strftime("%y_%m_%dT%H_%M_%S")
    )
    # create tarball
    archive_path = os.path.join(tmp_dir, "inputs.tar.gz")
    archive = tarfile.open(archive_path, "w:gz")
    if isolate_cwd:
        # arcname reproduces the flat layout the legacy path gets from
        # chdir + basename, without touching the process working directory
        for input_file_abs_path in input_file_abs_paths:
            archive.add(
                os.path.abspath(input_file_abs_path),
                arcname=os.path.basename(input_file_abs_path),
            )
        archive.close()
    else:
        # jump into each input files directory to avoid including paths in the tarball
        os.chdir(os.path.dirname(os.path.abspath(input_file_abs_paths[0])))
        for input_file_abs_path in input_file_abs_paths:
            archive.add(os.path.basename(input_file_abs_path))

        archive.close()
        os.chdir(tmp_dir)
    log.info("sending input archive to server ...")
    with open(archive_path, "rb") as input_file:
        ret = client_socket.sendfile(input_file)
    log.info(f"sent {ret} bytes of archive file data")
    client_socket.sendall(END_SEQUENCE)
    data = True
    output_sequence = False
    all_message = b""
    log_buffer = b""

    # receive and log data received from remote server
    while data:
        data = client_socket.recv(RECV_SIZE)
        if not output_sequence:
            # Look for the marker across the boundary too: on the unframed path a
            # marker split between two chunks is missed and the binary tarball gets
            # logged as text. Extraction still works -- all_message is split below --
            # which is why this has been invisible.
            pending = log_buffer + data
            if OUTPUT_MARKER in pending:
                output_sequence = True
                text = pending.split(OUTPUT_MARKER)[0]
                if frame_lines:
                    _emit_lines(log, text, flush=True)
                    log_buffer = b""
                else:
                    log.info(_decode(text).rstrip("\n"))
            elif frame_lines:
                log_buffer = _emit_lines(log, pending, flush=False)
            else:
                log.info(_decode(data).rstrip("\n"))
        all_message += data

    if frame_lines and log_buffer:
        _emit_lines(log, log_buffer, flush=True)

    output_file_content = all_message.split(OUTPUT_MARKER)[-1]
    buffer = io.BytesIO(output_file_content)
    buffer.seek(0)
    archive = tarfile.open(fileobj=buffer, mode="r:gz")
    archive.extractall(tmp_dir)
    client_socket.close()
    return os.path.join(tmp_dir, archive.getnames()[0]), os.path.join(
        tmp_dir, archive.getnames()[1]
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    arguments = parser.parse_args()

    # flatten file paths
    file_paths: List[str] = [
        arguments.mps_file_path,
    ]
    if arguments.relaxation_file_path:
        file_paths.append(arguments.relaxation_file_path)
    if arguments.mst_file_path:
        file_paths.append(arguments.mst_file_path)
    if arguments.parameters_file_paths is not None:
        for file_path in arguments.parameters_file_paths:
            file_paths.append(file_path)

    output_file_path, mst_file_path = send(file_paths)

    LOG.info(f"output file path written to {output_file_path}")
    LOG.info(f"mst file path written to {mst_file_path}")

    # Also print final paths to stdout for easy use in pipelines.
    print(output_file_path)
    print(mst_file_path)
