Submit a gurobi run request to the [server](https://github.com/planny-dev-org/solver-runner-server) and get the output result.

Use .mps and .json files as input/output

Install on your project using
```shell
poetry add git+https://github.com/planny-dev-org/solver-runner-client.git
```

## Env vars settings

```shell
export RUNNER_SERVER_HOSTNAME="server.host.name"
export RUNNER_SERVER_PORT="1234"  # the server port
export RUNNER_SERVER_CERT_PATH="server/certificate/file/path"
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
