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
output_json_filepath, output_mst_filepath = client.send(
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
output = json.load(open(output_json_filepath))
```

## Concurrent submissions

By default `send()` builds its tarball by `chdir()`-ing into the input directory and
adding files by basename, and it leaves the process working directory inside its own
temp dir when it returns. The working directory is **process-global**, so two
concurrent `send()` calls in one process interleave their `chdir()` calls and each
ends up tarring the other's directory.

Pass `isolate_cwd=True` on every concurrent call site:

```python
output_json_filepath, output_mst_filepath = client.send(
    ["/tmp/model.mps", "/tmp/model.main.prm"],
    isolate_cwd=True,
)
```

The resulting tarball is equivalent -- entries are flat in both modes -- and input
files are no longer required to share a single directory. The default stays `False`
so existing callers, including any that rely on the working-directory side effect,
are unaffected.

## Per-call logger

`send()` streams the server's log lines through the module logger
`solver_runner_client.client`. Pass `logger=` to send one submission's lines
somewhere else -- useful to tell two concurrent submissions apart:

```python
probe_logger = logging.getLogger("solver_runner_client.client.probe")
client.send(paths, logger=probe_logger, isolate_cwd=True)
```

Handlers attached to `solver_runner_client.client` keep receiving records from the
child logger through normal logger propagation.

## relaxation file

In order to use the feasibility relaxation feature, a JSON relaxation file is expected with this format:
```json
{
  "strategy": 2,
  "rules": [
    {
      "pattern": "min-in-row",
      "weight": 10
    },
    {
      "pattern": "max-in-row",
      "weight": 10
    }
  ]
}
```

Where `pattern` is the regular expression that will be used to match constraints names and value `10` is the relaxation weight of these constraints.

`strategy` is the relax directive: 
- 0: no relaxation
- 1: run IIS computation only
- 2: run relaxation using minrelax=True on feasRelax call
- 3: run relaxation using minrelax=False on feasRelax call
- 4: run relaxation using minrelax=False and re-optimize using original objective

See solver-runner-server for exact implementation.


```shell
python client.py </path/to/file.mps> --parameters_file_path <path/to/file.prm> --relaxation_file_path <path/to/file.json>
```


## Custom logging

To get client logs inside calling app, use logging capabilities. 
This is useful for example to propagate solver-runner-client logs to redis stream

Here is the example code that: 
- create a customer logging handler (`MyHandler`)
- remove all previous root logger handlers
- add customer logging handler to solver runner client logger
- send a `model.mps` to client and display execution logs received on custom handler `handle` method

```python
import os
import logging

from solver_runner_client import client

logging.basicConfig(level=logging.INFO)

class MyHandler(logging.StreamHandler):

    def handle(self, record: logging.LogRecord) -> bool:
        # add the redis client send here
        print(f"handle record -> {record}")  # just to show it works
        return True


# remove previous handlers
while logging.root.handlers:
    logging.root.removeHandler(logging.root.handlers[0])

# add custom logger
client_logger = logging.getLogger("solver_runner_client.client")
client_logger.addHandler(MyHandler())

# test MyHandler receive client logs and handle it
client.send([os.path.join(os.getcwd(), "model.mps")])
```


# Structured events on the log stream

The runner emits two kinds of output on one channel: the solver's own text, and
explicit machine-readable events. An event is a sentinel-prefixed, single-line JSON
envelope:

```
@@PLANNY_LOG@@ {"v":1,"kind":"violations","level":"INFO","data":{"total":7}}
```

`solver_runner_client.events.parse_event` demultiplexes: `None` for ordinary solver
text, an `Event` for an envelope, and `EventFormatError` when the sentinel is present
but the payload is malformed. That last distinction is deliberate — a broken envelope
is a bug to surface, not text to pass through.

```python
from solver_runner_client.client import send
from solver_runner_client.events import parse_event

output_json, output_mst = send(paths, frame_lines=True)
```

`frame_lines=True` is required to consume events: the sentinel is matched
line-anchored, and the default chunked path can split an envelope across two log
records, which is not recoverable. It also repairs two pre-existing fidelity losses in
the default path — trailing newlines stripped at chunk boundaries (which glues adjacent
lines together) and `UnicodeDecodeError` on a multi-byte character split across chunks.

`tests/fixtures/envelopes.jsonl` is committed identically in solver-runner-server, so
the encoder there and the decoder here stay in agreement without either repo depending
on the other.
