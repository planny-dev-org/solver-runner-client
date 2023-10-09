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

## Add a parameters file

.mps file does not contain solver parameters, for example time limit.
These parameters can be transmitted using a JSON file using following structure: 

```json
{
  "Params": {
    "TimeLimit": 15
  }
}
```

This will inform the remote execution to set `model.Params.TimeLimit = 15`

Then add JSON file to command line:

```shell
python client.py </path/to/file.mps> --parameters_file_path <path/to/file.json>
```
