import os
import argparse
import socket
import tempfile
import sys
import ssl
import json

parser = argparse.ArgumentParser(
    prog="client.py",
    description="Send an execution request to runner server.\n"
    "Run as a command or using client.send method.\n"
    "Following env vars must be set:\n"
    "RUNNER_SERVER_HOSTNAME => default to socket.gethostname()\n"
    "RUNNER_SERVER_PORT => default to 5050\n"
    "RUNNER_SERVER_CERT_PATH => if not set, an unsecured socket will be used\n",
)
parser.add_argument("mps_file_path", help=".mps file to run")


def send(mps_file_path):
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

    input_file = open(mps_file_path, "rb")
    ret = client_socket.sendfile(input_file)
    print(f"sent {ret} bytes of data")
    data = True
    all_message = ""
    while data:
        data = client_socket.recv(1024).decode()  # receive response
        sys.stdout.write(data)  # show in terminal
        all_message += data

    output_file_content = all_message.split("output:")[-1]

    output_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    output_file.close()
    try:
        json_content = json.loads(output_file_content)
        json.dump(
            json_content, open(output_file.name, "w"), indent=2, ensure_ascii=False
        )
    except Exception as exc:
        print(f"error while storing json file: {exc}")
    client_socket.close()
    return output_file.name


if __name__ == "__main__":
    arguments = parser.parse_args()
    output_file_path = send(arguments.mps_file_path)
    print(f"output file path written to {output_file_path}")
