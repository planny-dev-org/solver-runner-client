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
    "RUNNER_SERVER_CERT_PATH => if not set, an unsecured socket will be used\n",
)
parser.add_argument("mps_file_path", help="file to run (.mps)")
parser.add_argument("--parameters_file_path", help="optional parameters file (.prm)")


def send(mps_file_path, prm_file_path):
    host = os.environ.get("RUNNER_SERVER_HOSTNAME", socket.gethostname())
    port = int(os.environ.get("RUNNER_SERVER_PORT", 5050))
    cert_path = os.environ.get("RUNNER_SERVER_CERT_PATH")
    sock = socket.socket()  # instantiate
    sock.connect((host, port))  # connect to the server
    client_socket = sock
    if cert_path:
        context = ssl.create_default_context()
        context.load_verify_locations(cert_path)
        client_socket = context.wrap_socket(sock, server_hostname=host)

    # create a temp dir
    tmp_dir = tempfile.mkdtemp(
        prefix=datetime.datetime.now().strftime("%y_%m_%dT%H_%M_%S")
    )
    # create tarball
    archive = tarfile.open(os.path.join(tmp_dir, "inputs.tar.gz"), "w:gz")
    # jump into each input files directory to avoid including paths in the tarball
    os.chdir(os.path.dirname(os.path.abspath(mps_file_path)))
    archive.add(os.path.basename(mps_file_path))
    if prm_file_path:
        os.chdir(os.path.dirname(os.path.abspath(prm_file_path)))
        archive.add(os.path.basename(prm_file_path))
        archive.add(prm_file_path)
    archive.close()
    os.chdir(tmp_dir)
    ret = client_socket.sendfile(open("inputs.tar.gz", "rb"))
    print(f"sent {ret} bytes of archive file data")
    client_socket.sendall(END_SEQUENCE)
    data = b"data"
    all_message = b""
    while data and END_SEQUENCE not in data:
        data = client_socket.recv(1024)  # receive response
        printable_data = data.split(b"output:")[0]
        try:
            sys.stdout.write(printable_data.decode())
        except UnicodeDecodeError:
            # avoid printing binary data
            pass
        all_message += data

    output_file_content = all_message.split(b"output:")[-1]
    buffer = io.BytesIO(output_file_content)
    buffer.seek(0)
    archive = tarfile.open(fileobj=buffer, mode="r:gz")
    archive.extractall(tmp_dir)
    client_socket.close()
    return os.path.join(tmp_dir, archive.getnames()[0])


if __name__ == "__main__":
    arguments = parser.parse_args()
    output_file_path = send(arguments.mps_file_path, arguments.parameters_file_path)
    print(f"output file path written to {output_file_path}")
