import sys
import io
import os
import argparse
import socket
import tempfile
import ssl
import tarfile
import datetime
import logging

LOG = logging.getLogger(__name__)

END_SEQUENCE = b"__ENDSEQUENCE__"


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


def send(input_file_abs_paths, *, logger=None, isolate_cwd=False):
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

    # receive and log data received from remote server
    while data:
        data = client_socket.recv(1024)
        if not output_sequence:
            if b"output:" in data:
                output_sequence = True
                log.info(data.split(b"output:")[0].decode().rstrip("\n"))
            else:
                log.info(data.decode().rstrip("\n"))
        all_message += data

    output_file_content = all_message.split(b"output:")[-1]
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
    file_paths = [
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
