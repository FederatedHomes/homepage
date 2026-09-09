# Local Development and Deployment

This document contains the setup and execution procedures for running the federated learning application on a single development host. It is intentionally separate from the project overview in `README.md` and the physical multi-host deployment procedure in `DISTRIBUTED_DEPLOYMENT.md`.

## Scope

Use this document for:

- local Docker-based development;
- application testing;
- local all-in-one federation;
- optional direct host execution without Docker.

For two or more physical client machines, use `DISTRIBUTED_DEPLOYMENT.md` instead.

## Prerequisites

The host should provide:

- Python 3
- Docker
- Docker Compose v2
- PyYAML for the setup/configuration helper

The ML runtime dependencies are provided by Docker and do not need to be installed on the host for the recommended workflow.

## Setup helper

From `machine_learning/`:

```bash
chmod +x setup.sh
./setup.sh
```

The interactive menu provides the local/development operations alongside the deployment operations:

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

For local development, the relevant path is normally host preparation, testing, and the local all-in-one development option. The exact generated services depend on the selected configuration.

## Docker-based tests

The preferred test workflow runs the test suite inside the project's custom Docker image so that the host does not need the application's ML dependencies.

The test flow is:

```text
setup.sh
   |
   +-- Build flwr_superexec:local
   |
   +-- Start temporary test-runner
   |
   +-- pytest tests/ -v
   |
   +-- Remove test container
```

Run `./setup.sh` and select **Run tests**.

The test suite covers the DataContract and application validation behavior, including:

- valid client data;
- independent and mixed multi-client validation;
- required and extra features;
- feature ordering;
- feature and label dtype conversion;
- numeric string conversion;
- non-convertible values;
- fractional and unknown labels;
- segmentation configuration;
- generated tensor dimensions and dtype;
- invalid DataContract configurations.

A successful run should report all tests as `PASSED`.

## Local all-in-one federation

The local Docker workflow is intended for development and integration testing on one host.

The generated stack can include:

- `superlink` — Flower SuperLink;
- `supernode-*` — SuperNode services generated from `clients.yml`;
- `superexec-serverapp` — custom Flower ServerApp container;
- `superexec-clientapp-*` — custom Flower ClientApp containers;
- `trainer` — Flower `superexec:1.33.0` runner;
- `test-runner` — temporary pytest container when tests are requested.

The custom application services and test runner use the project's `flwr_superexec:local` image.

The trainer launches the Flower application with the `local-deployment` profile. The generated Compose configuration is derived from `clients.yml`, allowing the local client count to be changed without manually maintaining individual service definitions.

## Local data

Local client data is stored under the configured client data directories, for example:

```text
data/client-1/train.csv
data/client-1/val.csv

data/client-2/train.csv
data/client-2/val.csv
```

Each dataset must conform to the shared DataContract described in `README.md` and implemented by the application.

Client checkpoints are stored locally, for example:

```text
checkpoints/
├── client-1/
├── client-2/
└── client-3/
```

`data/` and `checkpoints/` are runtime artifacts and are not tracked in Git.

## Direct execution without Docker

The recommended development workflow is Docker-based. If direct host execution is needed, install the repository and its dependencies locally and run Flower from `machine_learning/`:

```bash
pip install -e .
flwr run . local-deployment --stream
```

Direct execution requires the appropriate Python and ML dependencies on the host. It is a development workflow only and is not the procedure for physical multi-host federation.

## Development workflow

A typical local development cycle is:

```text
1. Modify application or DataContract code
          |
          v
2. Run Docker-based tests
          |
          v
3. Run a local federated training session
          |
          v
4. Validate model/checkpoint output
          |
          v
5. Commit validated changes
```

## Moving to distributed deployment

Once the application passes local validation, use `DISTRIBUTED_DEPLOYMENT.md` for deployment across two or more physical Docker hosts.

That runbook covers server/client host preparation, TLS, per-client authentication identities, registration, Compose generation, networking, startup, distributed training verification, resilience acceptance tests, state persistence, and troubleshooting.
