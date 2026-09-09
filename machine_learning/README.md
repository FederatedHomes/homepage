---
tags: [federated, machine learning, vision, fds]
framework: [torch, torchvision]
---

# Federated Learning with PyTorch and Flower

## Executive Summary

This project implements a **secure, Docker-based Federated Machine Learning framework** for training a shared global model across multiple client devices without centralizing their raw training datasets.

The framework is built around:

- **Flower 1.33.0** for federated orchestration;
- **PyTorch** for machine learning;
- **Python** for application development;
- **Docker / Docker Compose** for reproducible deployment;
- a **static HTML interface** that can be served independently through GitHub Pages.

The target operating model is a federation of **two or more physical client devices on the same network**, each maintaining its own local dataset and checkpoint storage. A dedicated server host provides the Flower federation infrastructure, while each client host runs its own authenticated SuperNode and local ClientApp.

### Current status

The core distributed federation is operational. A two-client physical deployment has been successfully trained through the Flower federation, producing a global model from participating client updates.

The current architecture also includes a production-oriented security layer for the Flower control and federation paths:

- TLS protection for the production Fleet path;
- TLS protection for the production Control path;
- per-client SuperNode authentication;
- server-side public-key registration and authorization state;
- isolation of private client authentication keys so that each physical client receives only its own identity.

The current ServerApp also applies a minimum-success policy for aggregation: with three sampled clients, `3/3` and `2/3` successful responses may proceed, while `1/3` or `0/3` causes the round to abort.

Runtime/AppIO TLS, secure aggregation, differential privacy, stronger poisoning defenses, expanded audit logging, and additional production hardening remain planned areas of work.

## Architecture

The system uses Flower's **SuperLink / SuperNode / SuperExec** architecture.

```text
                         SERVER HOST

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
        CLIENT HOST A  CLIENT HOST B  CLIENT HOST C

        +-----------+   +-----------+   +-----------+
        | SuperNode |   | SuperNode |   | SuperNode |
        | client-1  |   | client-2  |   | client-3  |
        +-----+-----+   +-----+-----+   +-----+-----+
              |               |               |
        ClientApp       ClientApp       ClientApp
              |               |               |
         local data       local data       local data
```

At a high level, federated training follows:

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
```

Raw client datasets remain on their respective client hosts. The federation exchanges the application-defined training results needed to produce the global model rather than requiring the raw datasets to be centralized.

## Data and model contract

All clients use a shared **DataContract** that defines the model-facing data requirements, including:

- required feature columns and label definition;
- feature and label types;
- segmentation length and overlap;
- missing-value handling;
- Continuous Wavelet Transform (CWT) configuration;
- model input tensor shape, layout, and dtype.

The application validates client data against this contract before model training. Incompatible data is rejected rather than silently entering the federation.

The project therefore separates three concerns:

1. **Local data ownership** — each client retains its own dataset.
2. **Contract enforcement** — every client must satisfy the same model-facing schema.
3. **Federated learning** — local model updates contribute to the shared global model.

## Security architecture

Production federation is designed around explicit trust boundaries.

- **Fleet communication:** TLS plus SuperNode authentication.
- **Control communication:** TLS.
- **Client identity:** one unique SuperNode identity per physical client.
- **Private-key isolation:** client private authentication keys remain on the corresponding client host.
- **Authorization:** the server maintains the registered public-key inventory and persistent authorization state.
- **Deployment separation:** development and production configurations are explicitly distinguished.

`SECURITY.md` is the authoritative security architecture and policy document. It defines the trust model, certificate requirements, authentication behavior, credential handling, key lifecycle, network exposure, current limitations, and future security hardening.

## Repository structure

The repository is organized around the following responsibilities:

```text
machine_learning/
├── src/                         Application and federation logic
├── tests/                       Automated validation and application tests
├── scripts/                     Deployment and registration helpers
├── clients.yml                  Federation client inventory
├── setup.sh                     Interactive setup/deployment helper
├── Dockerfile.superexec         Shared Flower application runtime image
├── Dockerfile.client-registration
│                                Production registration helper image
├── .flwr/config.toml            Flower deployment profiles
├── pyproject.toml               Python project metadata
├── requirements.txt             Runtime dependencies for the custom image
├── LOCAL_DEPLOYMENT.md          Local development and single-host procedures
├── DISTRIBUTED_DEPLOYMENT.md    Physical multi-host deployment runbook
└── SECURITY.md                  Security architecture and policy
```

### Key application components

- `src/server_app.py` — Flower `ServerApp` and server-side aggregation behavior.
- `src/client_app.py` — Flower `ClientApp` executed for participating clients.
- `src/task.py` — model, preprocessing, training, and local dataset handling.
- `src/data_contract.py` — shared DataContract and validation logic.
- `src/deployment_config.py` — deployment profile and production security configuration validation.
- `scripts/generate_compose.py` — generates Docker Compose services from the configured client inventory.
- `scripts/client_registration.py` — registers production SuperNode public identities with the Flower Control API.
- `clients.yml` — source of truth for configured federation clients and their server-side public keys.

## Deployment model

The framework supports two distinct operating modes:

### Local development

A single host can run the federation components in Docker for application development, testing, and integration validation.

See [`LOCAL_DEPLOYMENT.md`](LOCAL_DEPLOYMENT.md) for all local setup and execution procedures.

### Distributed federation

A production-style federation separates the server infrastructure from physical client hosts. Each client host runs only its assigned client identity and local data environment.

See [`DISTRIBUTED_DEPLOYMENT.md`](DISTRIBUTED_DEPLOYMENT.md) for the complete operational runbook covering host preparation, TLS, authentication identities, registration, Compose generation, networking, startup, verification, resilience acceptance tests, state persistence, and troubleshooting.

## Documentation model

The project documentation deliberately separates **what the system is**, **how it is deployed**, and **how it is secured**:

| Document | Purpose |
|---|---|
| `README.md` | Executive project summary, architecture, current status, major components, and design intent |
| `LOCAL_DEPLOYMENT.md` | Local development, testing, single-host Docker federation, and direct host execution |
| `DISTRIBUTED_DEPLOYMENT.md` | Operational deployment across two or more physical client hosts |
| `SECURITY.md` | Security architecture, trust boundaries, TLS, authentication, credential handling, and security requirements |

> **README explains. Deployment instructs. Security specifies and constrains.**

Setup commands, environment preparation, Compose generation, registration procedures, startup sequences, and troubleshooting belong in the deployment documents rather than in this executive summary.

## Project direction

The framework is being developed incrementally toward a production-capable federated learning platform.

The roadmap includes:

1. secure distributed federation foundation;
2. persistent state, retry/failure policy, and health checks;
3. secure aggregation and privacy mechanisms;
4. CI/CD, image registry, scanning, and release processes;
5. observability and audit capabilities;
6. production orchestration and operational hardening.

The architectural goal is to provide a reproducible federation in which **data remains distributed, model training is coordinated centrally, client identities are controlled explicitly, and deployment/security concerns are documented separately from application logic**.
