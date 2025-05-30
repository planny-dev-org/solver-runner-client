import sys
import io
import os
import argparse
import socket
import tempfile
import ssl
import tarfile
import datetime

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


def send(input_file_abs_paths):
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
    archive = tarfile.open(os.path.join(tmp_dir, "inputs.tar.gz"), "w:gz")
    # jump into each input files directory to avoid including paths in the tarball
    os.chdir(os.path.dirname(os.path.abspath(input_file_abs_paths[0])))
    for input_file_abs_path in input_file_abs_paths:
        archive.add(os.path.basename(input_file_abs_path))

    archive.close()
    os.chdir(tmp_dir)
    print(f"sending input archive to server ...")
    ret = client_socket.sendfile(open("inputs.tar.gz", "rb"))
    print(f"sent {ret} bytes of archive file data")
    client_socket.sendall(END_SEQUENCE)
    data = True
    output_sequence = False
    all_message = b""

    # receive and print data received from remote server
    while data:
        data = client_socket.recv(1024)
        if not output_sequence:
            if b"output:" in data:
                output_sequence = True
                sys.stdout.write(data.split(b"output:")[0].decode())
            else:
                sys.stdout.write(data.decode())
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
    print(f"output file path written to {output_file_path}")
    print(f"mst file path written to {mst_file_path}")
