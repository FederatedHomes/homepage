---
tags: [federated, machine learning, vision, fds]
framework: [torch, torchvision]
---

# Federated Learning with PyTorch and Flower

This project provides a Docker-based federated learning framework built with **Flower 1.33.0**, **PyTorch**, and Python.

The application uses Flower's SuperLink / SuperNode / SuperExec architecture. It supports local development as well as distributed federation across two or more physical client machines on the same network.

Each client keeps its own dataset and checkpoint storage locally. During federated training, clients perform local computation and participate in model aggregation through the Flower federation.

## Documentation

The project documentation is intentionally separated by responsibility:

| Document | Purpose |
|---|---|
| `README.md` | Project overview, repository structure, development workflow, testing, and general usage |
| [`DISTRIBUTED_DEPLOYMENT.md`](DISTRIBUTED_DEPLOYMENT.md) | Operational runbook for deploying and running the federation across two or more physical Docker hosts |
| [`SECURITY.md`](SECURITY.md) | Security architecture, trust boundaries, TLS, SuperNode authentication, credential handling, key management, and security requirements |

### Where to start

- **New to the project?** Start here for architecture, source layout, development, and local testing.
- **Deploying across physical machines?** Follow `DISTRIBUTED_DEPLOYMENT.md`. It contains the concrete host preparation, configuration, Compose, registration, startup, verification, acceptance-test, and troubleshooting procedures.
- **Making a security decision or changing credentials/TLS/authentication?** Treat `SECURITY.md` as the authoritative security specification.

The documentation follows a simple separation-of-concerns rule:

> **README explains. Deployment instructs. Security specifies and constrains.**

The deployment guide and security specification may reference one another, but neither should duplicate the other's authoritative content.

## Architecture at a glance

The distributed architecture is based on Flower SuperLink / SuperNode / SuperExec:

```text
                         Server host

                 +-----------------------+
                 |       SuperLink       |
                 |                       |
                 | Fleet API   :9092     |
                 | Control API :9093      |
                 | Runtime     :9091     |
                 +-----------+-----------+
                             |
                    TLS + authentication
                             |
              +--------------+--------------+
              |              |              |
              v              v              v
        Client host A  Client host B  Client host C

        +-----------+   +-----------+   +-----------+
        | SuperNode |   | SuperNode |   | SuperNode |
        | client-1  |   | client-2  |   | client-3  |
        +-----+-----+   +-----+-----+   +-----+-----+
              |               |               |
        ClientApp       ClientApp       ClientApp
              |               |               |
         local data       local data       local data
```

A physical distributed deployment uses:

- one server host running the SuperLink and ServerApp infrastructure;
- one SuperNode and one ClientApp on each physical client host;
- a unique client identity per physical client;
- TLS-protected SuperNode ↔ SuperLink Fleet communication in production;
- SuperNode authentication in production;
- local client datasets that are not transferred to the server as raw training data.

The concrete distributed deployment procedure is maintained in `DISTRIBUTED_DEPLOYMENT.md`. Security controls and their boundaries are maintained in `SECURITY.md`.

## What this repo contains

- `Dockerfile.superexec` — runtime image for the Flower `serverapp` and `clientapp`, including the application and testing dependencies
- `Dockerfile.client-registration` — runtime image for the production SuperNode registration helper
- `clients.yml` — source of truth for configured federated clients and their server-side public authentication keys
- `scripts/generate_compose.py` — generates Docker Compose configuration from `clients.yml`
- `scripts/client_registration.py` — registers configured SuperNode public identities with the production SuperLink Control API
- `scripts/generate_supernode_auth.py` — development helper for generating SuperNode authentication identities
- `scripts/generate_dev_certs.py` — development certificate-generation helper
- `setup.sh` — interactive host preparation, configuration, testing, and deployment helper
- `.flwr/config.toml` — Flower CLI deployment profiles used by the trainer
- `.env.example` — development environment defaults
- `.env.production.example` — production deployment environment template
- `pyproject.toml` — Python project metadata and local-development configuration
- `requirements.txt` — additional runtime dependencies installed into the custom Docker application image
- `src/` — Flower application source
  - `src/server_app.py` — Flower `ServerApp`
  - `src/client_app.py` — Flower `ClientApp`
  - `src/task.py` — model, training, preprocessing, and local CSV dataset loading
  - `src/data_contract.py` — shared DataContract defining the model-facing data schema
  - `src/deployment_config.py` — deployment profile and production security configuration validation
- `tests/` — application and DataContract validation tests

## Dependency model

`requirements.txt` contains the additional runtime dependencies installed into the custom `flwr_superexec:local` Docker image.

The base image `flwr/superexec:1.33.0` already provides the Flower runtime, so the project's runtime dependency file deliberately does not need to duplicate the Flower installation.

The Docker image also contains `pytest`, allowing the application test suite to run inside Docker without requiring the host machine to install the ML dependencies.

`pyproject.toml` remains the Python package metadata and local-development configuration file.

The project does **not** maintain a separate host-only requirements file. Host preparation is handled by `setup.sh`; ML runtime dependencies remain in Docker.

## Data structure

The project expects a per-client dataset layout under `machine_learning/data/`, as configured by `clients.yml`.

For example:

```text
data/client-1/train.csv
data/client-1/val.csv

data/client-2/train.csv
data/client-2/val.csv
```

Each CSV must conform to the shared `DataContract` defined by the federated learning application.

The DataContract defines:

- required feature columns;
- label column;
- feature and label data types;
- segmentation length and overlap;
- missing-value handling;
- Continuous Wavelet Transform (CWT) configuration;
- model input tensor shape;
- model input tensor dtype;
- tensor layout.

Client data is validated against this contract before being used by the model.

### Data validation behavior

| Validation | Behavior |
|---|---|
| Missing feature | Reject |
| Extra feature | Warn and ignore |
| Reordered features | Accept |
| Convertible feature dtype | Convert and log |
| Convertible label dtype | Convert and log |
| Non-convertible feature values | Reject |
| Non-convertible label values | Reject |
| Fractional labels | Reject |
| Unknown labels | Reject |
| Incorrect segmentation length | Reject |
| Invalid segmentation configuration | Reject |
| Invalid generated tensor shape | Reject |
| Invalid generated tensor dtype | Reject |

This allows clients to provide compatible data with minor dtype differences while ensuring that incompatible data cannot silently enter the federated training process.

> `machine_learning/data/` and `machine_learning/checkpoints/` are not tracked in Git. They are intended for local client datasets and checkpoint storage.

## Client configuration

`clients.yml` is the source of truth for the configured federation clients.

Each client defines:

- Client ID
- Data directory
- Checkpoint directory
- Server-side public SuperNode authentication key

Example:

```yaml
clients:
  - id: client-1
    data_dir: ./data/client-1
    checkpoint_dir: ./checkpoints/client-1
    public_key: ./certificates/prod/auth/client-1.pub

  - id: client-2
    data_dir: ./data/client-2
    checkpoint_dir: ./checkpoints/client-2
    public_key: ./certificates/prod/auth/client-2.pub

  - id: client-3
    data_dir: ./data/client-3
    checkpoint_dir: ./checkpoints/client-3
    public_key: ./certificates/prod/auth/client-3.pub
```

At least two clients are required for a federated deployment, and client IDs must be unique.

For a physical deployment, the server inventory may contain all configured clients, while each physical client host is assigned exactly one `CLIENT_ID`. A client host does not receive the private authentication keys belonging to other clients.

The exact process for preparing client hosts and registering public identities is documented in `DISTRIBUTED_DEPLOYMENT.md`. The security requirements governing those identities are defined in `SECURITY.md`.

## Setup and launch

From the `machine_learning/` directory:

```bash
chmod +x setup.sh
./setup.sh
```

The current setup menu is:

```text
1) Prepare host
2) Generate server Compose
3) Generate client Compose
4) Start server infrastructure
5) Run tests
6) Show config
7) Start local all-in-one development
8) Exit
```

The exact options can vary with the selected deployment role/profile, but the workflow is intentionally divided between host preparation, Compose generation, server infrastructure, testing, configuration inspection, and local all-in-one development.

### Host preparation

`setup.sh` verifies the host tools required for the selected role.

Server hosts require:

- Python 3
- Docker
- Docker Compose v2
- OpenSSL
- PyYAML

Client hosts require:

- Python 3
- Docker
- Docker Compose v2
- `ssh-keygen`
- PyYAML

The ML runtime dependencies are not required on the host because they are provided by Docker.

For production client preparation, `setup.sh` creates only the selected client's SuperNode authentication identity. For production server preparation, it prepares the server-side TLS and persistent-state resources required by the deployment configuration.

See `DISTRIBUTED_DEPLOYMENT.md` for the complete distributed setup procedure and `SECURITY.md` for credential-handling requirements.

## Testing

The preferred application test workflow runs pytest inside Docker.

The Docker test runner uses the same custom application image that contains the project's Python and ML dependencies. This means the host machine does not need local installations of PyTorch, pandas, NumPy, PyWavelets, scikit-image, pytest, or the other application dependencies.

The process is:

```text
setup.sh
   |
   +-- Build flwr_superexec:local
   |
   +-- Start test-runner container
   |
   +-- pytest tests/ -v
   |
   +-- Remove test container
```

The test runner is isolated from the federated runtime. It does not start the SuperLink, SuperNodes, or federated training services.

Run:

```bash
./setup.sh
```

and select the test option.

The test suite currently validates:

- valid client data;
- independent validation of multiple clients;
- missing required features;
- extra features;
- reordered feature columns;
- feature dtype conversion;
- numeric string feature conversion;
- non-convertible feature values;
- label dtype conversion;
- numeric string label conversion;
- non-convertible labels;
- fractional labels;
- unknown labels;
- segmentation length;
- invalid DataContract configuration;
- generated tensor dimensions;
- generated tensor dtype;
- mixed multi-client validation.

A successful run should report all tests as `PASSED`.

## Local federated development

The local Docker workflow is intended for application development and integration testing.

The generated local Compose stack can include:

- `superlink` — Flower SuperLink
- `supernode-*` — SuperNode services generated from `clients.yml`
- `superexec-serverapp` — custom Flower ServerApp container
- `superexec-clientapp-*` — custom Flower ClientApp containers
- `trainer` — Flower `superexec:1.33.0` runner for the federated session
- `test-runner` — temporary pytest container

The custom application services and test runner use the project's `flwr_superexec:local` image.

The trainer launches the Flower application using the configured deployment profile. In local development, this is normally `local-deployment`.

The generated Compose configuration is derived from `clients.yml`, so the configured number of local clients can be changed without manually maintaining individual service definitions.

## Federated training flow

At a high level, a federated training run follows:

```text
Local client datasets
        |
        v
     ClientApps
        |
        v
    SuperNodes
        |
        v
     SuperLink
        |
        v
     ServerApp
        |
        v
    FedAvg aggregation
        |
        v
     Global model
        |
        v
 Global evaluation
```

The raw client dataset remains in the client's configured data directory. Clients perform local training and return the application-defined federated results through Flower.

The current ServerApp resilience policy requires at least two successful client responses for an aggregation round. For example, with three sampled clients, `3/3` and `2/3` successful responses can proceed, while `1/3` or `0/3` causes the aggregation to abort.

The operational acceptance tests for this behavior are documented in `DISTRIBUTED_DEPLOYMENT.md` rather than duplicated here.

## Checkpoints

Each client saves local checkpoints into its configured checkpoint directory.

For example:

```text
checkpoints/
├── client-1/
├── client-2/
└── client-3/
```

The server also has a global checkpoint location used by the ServerApp deployment.

Checkpoint contents are local runtime artifacts and are not tracked in Git.

## Running locally without Docker

The recommended development and testing workflow is Docker-based.

If you prefer to run the application directly on the host, install the repository and its dependencies, then run Flower from `machine_learning/`:

```bash
pip install -e .
flwr run . local-deployment --stream
```

Direct host execution requires the appropriate Python/ML dependencies to be installed locally. For a consistent dependency environment, use the Docker-based workflow instead.

This local execution mode is a development workflow and is not the procedure for deploying across physical client machines. Use `DISTRIBUTED_DEPLOYMENT.md` for that scenario.

## Development workflow

A recommended application-development workflow is:

```text
1. Modify application or DataContract code
          |
          v
2. Run ./setup.sh
          |
          v
3. Run Docker-based tests
          |
          v
4. Fix validation/test failures
          |
          v
5. Run a local federated training session
          |
          v
6. Validate the resulting model/checkpoints
          |
          v
7. Commit the validated changes
```

For distributed deployment work, follow the separate operational runbook and security specification rather than extending this development workflow with deployment-specific procedures.

## Production and distributed deployment

This README intentionally provides only a high-level description of production deployment.

For the complete physical multi-host workflow, including:

- server preparation;
- client preparation;
- TLS certificate distribution;
- per-client identity creation;
- public-key registration;
- Docker Compose generation;
- network configuration;
- startup sequence;
- distributed training verification;
- resilience acceptance tests;
- state persistence verification;
- troubleshooting;

see [`DISTRIBUTED_DEPLOYMENT.md`](DISTRIBUTED_DEPLOYMENT.md).

For the security model governing:

- TLS and certificate trust;
- certificate SAN requirements;
- SuperNode authentication;
- client identity isolation;
- private-key handling;
- persistent authorization state;
- key rotation and revocation;
- network exposure;
- current security limitations and future hardening;

see [`SECURITY.md`](SECURITY.md).

## Notes

- The project uses Flower SuperLink / SuperNode / SuperExec rather than the older `--server` / `--client` CLI topology.
- Flower version `1.33.0` is the framework target for this project.
- PyTorch is the machine learning framework used by the application.
- `Dockerfile.superexec` provides the shared application runtime environment used by the ServerApp, ClientApp, and Docker-based test runner.
- `clients.yml` is the source of truth for configured federation clients.
- `scripts/generate_compose.py` generates Compose configurations from `clients.yml`.
- `scripts/client_registration.py` performs production SuperNode registration from the server-side public-key inventory.
- `setup.sh` is the primary host-preparation and deployment helper.
- The current two-client distributed federation has been successfully demonstrated.
- The current security layer protects the production Fleet path with TLS and SuperNode authentication and protects the production Control path with TLS.
- Runtime/AppIO TLS remains a separate future hardening item; see `SECURITY.md` for the authoritative security boundary.
- Secure aggregation/privacy mechanisms are planned as later federation-hardening work.
