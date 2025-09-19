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

# using a model with 3 multi objectives
env0 = model.getMultiobjEnv(0)
env1 = model.getMultiobjEnv(1)
env2 = model.getMultiobjEnv(2)
env0.setParam("TimeLimit", 1) 
env1.setParam("TimeLimit", 1)
env2.setParam("TimeLimit", 1)

# usual way to run gurobi
model.optimize()
model.write("output.json")
output = json.load("output.json")

# using client
model.write("/tmp/model.mps")
model.write("/tmp/model.main.prm")  # main.prm suffix is mandatory, it informs runner this is the main params
env0.writeParams("/tmp/model.env_0.prm")  # env_0.prm suffix is mandatory, it informs solver this is params for env 0
env1.writeParams("/tmp/model.env_1.prm")  # env_1.prm suffix is mandatory, it informs solver this is params for env 1
env2.writeParams("/tmp/model.env_2.prm")  # env_2.prm suffix is mandatory, it informs solver this is params for env 2
output = client.send(
    [
        "/tmp/model.mps",
        "/tmp/model.prm",  # optional
        "/tmp/model.env_0.prm"  # optional
        "/tmp/model.env_1.prm"  # optional
        "/tmp/model.env_2.prm"  # optional
        "/tmp/model.mst",  # optional
        "/tmp/model.relaxation.json"  # optional, see format below
    ]
)
```

## relaxation file

In order to use the feasibility relaxation feature, a JSON relaxation file is expected with this format:
```json
{
  "constraint_regexp": 10
}
```

Where `constraint_regexp` is the regular expression that will be used to match constraints names and value `10` is the relaxation weight of these constraints.

```shell
python client.py </path/to/file.mps> --parameters_file_path <path/to/file.prm>
```
