Submit a gurobi run request to the [server](https://github.com/planny-dev-org/solver-runner-server) and get the output result.

Use .mps and .json files as input/output

Install on your project using poetry:

```shell
poetry add git+https://github.com/planny-dev-org/solver-runner-client.git
```

## Client certificates

In order to connect, a signed client certificated is needed.
Generate a certificate and a key using openssl: 

```shell
openssl req -noenc -new -keyout runner_client.key -out runner_client.csr
```

Use these values when asked on the prompt:
```shell
Country Name (2 letter code): CH
State or Province Name (full name): Geneva
Locality Name (eg, city): Geneva
Organization Name (eg, company): Planny SA
Organizational Unit Name (eg, section): Web services

=> Let the other values empty
```


Then send the `runner_client.csr` to the server admin. 
You'll get back a `runner_client.cer` file and a `runner.planny.ch.cert.pem` file.


## Env vars settings

```shell
export RUNNER_SERVER_HOSTNAME="server.host.name"
export RUNNER_SERVER_PORT="1234"  # the server port
export RUNNER_SERVER_CERT_PATH="path/to/runner.planny.ch.cert.pem"
export RUNNER_CLIENT_CERT_PATH="path/to/runner_client.cer"
export RUNNER_CLIENT_KEY_PATH="path/to/runner_client.key"
```

## Run

Using command line: 

```shell
python client.py </path/to/file.mps>
```

Or inside a python shell or program:

```python
from solver_runner_client.client import send

mps_file_path = "/tmp/john.mps"
send(mps_file_path)
```

## Add a parameters file

.mps file does not contain solver parameters, for example time limit.
These parameters can be transmitted using a .prm:

```shell
python client.py </path/to/file.mps> --parameters_file_path <path/to/file.prm>
```
