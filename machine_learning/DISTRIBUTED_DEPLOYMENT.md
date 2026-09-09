# Distributed Federation Deployment

This document is the operational runbook for deploying the Flower 1.33.0 / PyTorch federated learning application across two or more physical machines on the same network.

It covers the distributed Docker deployment, host preparation, TLS and SuperNode identity installation, SuperNode registration, service startup, federated training, verification, and Step 7 acceptance testing.

Security architecture and security policy are documented separately in `SECURITY.md`. Project overview and local development guidance belong in `README.md`.

## 1. Target architecture

The distributed deployment uses Flower's SuperLink / SuperNode / SuperExec topology.

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
              +--------------+--------------+
              |              |              |
              | TLS +        | TLS +        | TLS +
              | auth         | auth         | auth
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

The server host runs the federation control plane and ServerApp. Each physical client host runs exactly one assigned SuperNode and one ClientApp.

A physical client does not run a copy of the SuperLink and does not use the Docker-only hostname `superlink` to reach the server. Client SuperNodes connect to the server using the configured LAN-reachable `SUPERLINK_ADDRESS`.

## 2. Deployment roles and profiles

The deployment has two explicit profiles:

- `development` — intended for local Docker/Compose development and integration testing. Flower's insecure transport may be used for the local federation.
- `production` — intended for distributed deployment. TLS is required for the SuperLink Fleet and Control APIs, SuperNode authentication is enabled, and SuperLink registration state is persisted.

A physical distributed deployment should use:

```dotenv
DEPLOYMENT_PROFILE=production
```

There are also host roles:

- `server` — SuperLink, ServerApp, trainer, and production registration service.
- `client` — one SuperNode and one ClientApp for one assigned client identity.
- `all` — local development/integration role that can generate the complete local stack.

For a physical multi-host deployment, use `server` on the federation server and `client` on each client machine.

## 3. Host prerequisites

The host needs only the tools required to prepare and run the Docker deployment. The ML runtime dependencies remain inside Docker images.

`setup.sh` verifies:

### Server host

- Python 3
- Docker
- Docker Compose v2 (`docker compose`)
- OpenSSL
- PyYAML for the setup scripts

### Client host

- Python 3
- Docker
- Docker Compose v2 (`docker compose`)
- `ssh-keygen`
- PyYAML for the setup scripts

The client host does not need a local Flower or PyTorch installation.

## 4. Shared repository revision

Every physical host must use the same repository revision for the federated application and deployment scripts.

On each host:

```bash
git clone <repository>
cd machine_learning
git checkout <deployment-revision>
```

If the repository has already been cloned, update it to the same revision before generating Compose files.

The server and clients should not run different versions of `server_app.py`, `client_app.py`, the DataContract, or the deployment configuration.

## 5. Server host preparation

### 5.1 Configure the production environment

Create `.env` from `.env.production.example` and configure the server role.

At minimum:

```dotenv
DEPLOYMENT_PROFILE=production
DEPLOYMENT_ROLE=server
SUPERLINK_ADDRESS=<server-dns-or-lan-ip>:9092
SUPERLINK_CONTROL_ADDRESS=<server-dns-or-lan-ip>:9093
```

The remaining production paths should normally be left at the values supplied by `.env.production.example` unless the deployment requires a different host layout.

The important production paths are:

```dotenv
TLS_ROOT_CERTIFICATES=/etc/flower/tls/ca.crt
SUPERLINK_CERTIFICATE=/etc/flower/tls/superlink.crt
SUPERLINK_PRIVATE_KEY=/etc/flower/tls/superlink.key
TLS_CERTIFICATE_HOST_DIR=./certificates/prod/tls
SUPERNODE_AUTH_PRIVATE_KEY_DIR=/etc/flower/auth
SUPERNODE_AUTH_HOST_DIR=./certificates/prod/auth
SUPERLINK_STATE_HOST_DIR=./state/superlink
SUPERLINK_STATE_DIR=/var/lib/flower
```

`SUPERLINK_ADDRESS` is the Fleet API endpoint used by SuperNodes. `SUPERLINK_CONTROL_ADDRESS` is the Control API endpoint used by the registration workflow.

If the Control API address is omitted, the deployment configuration derives port `9093` from the configured Fleet address on port `9092`.

### 5.2 Configure the client inventory

`clients.yml` is the server-side inventory of authorized federation clients. It is the source used to generate the distributed Compose configuration and the production registration workflow.

Each client entry defines:

- unique client ID
- local data directory
- local checkpoint directory
- public SuperNode authentication key

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

At least two clients are required, and client IDs must be unique.

The public key entry is an authorization record. The corresponding private key belongs only on the physical client host assigned to that client.

### 5.3 Prepare the server

Run:

```bash
./setup.sh
```

Select the server-host role when prompted, or set `DEPLOYMENT_ROLE=server` in `.env`.

For production, the setup prepares the configured SuperLink state directory and can create starter TLS material for controlled distributed integration testing.

Starter TLS material consists of:

```text
certificates/prod/tls/
├── ca.crt
├── superlink.crt
└── superlink.key
```

The starter CA private key is removed after the starter certificate is generated. These credentials are suitable for controlled integration testing; production federation certificates should be issued and managed by the organization's approved PKI process.

### 5.4 Certificate identity

The SuperLink certificate must contain a Subject Alternative Name (SAN) matching the hostname or IP address used by the clients to reach the SuperLink.

For example, if clients connect to:

```text
192.168.0.172:9092
```

then `192.168.0.172` must be represented in the SuperLink certificate SAN.

Do not assume that the Docker service name `superlink` is a valid certificate identity. Docker-internal service names and physical-host network identities are different deployment contexts.

For controlled development certificate generation, the repository provides:

```bash
python3 scripts/generate_dev_certs.py --superlink-host <server-ip-or-dns>
```

Do not use development certificates as the organization's production PKI.

## 6. Distribute the server CA to clients

The server's `ca.crt` is the trust anchor used by every production SuperNode and by the production registration workflow.

Copy **only** the CA certificate to each client host:

```text
server host
  certificates/prod/tls/ca.crt
          |
          +----> client-1 host
          +----> client-2 host
          +----> client-3 host
```

On each client, place it in the configured `TLS_CERTIFICATE_HOST_DIR`, normally:

```text
certificates/prod/tls/ca.crt
```

Clients must not generate their own replacement CA for the federation.

The SuperLink certificate and SuperLink private key remain on the server. They are not copied to clients.

## 7. Client host preparation

Perform these steps independently on each physical client host.

### 7.1 Configure the client role

Create `.env` from `.env.production.example` and set the assigned identity:

```dotenv
DEPLOYMENT_PROFILE=production
DEPLOYMENT_ROLE=client
CLIENT_ID=client-1
SUPERLINK_ADDRESS=<server-dns-or-lan-ip>:9092
SUPERLINK_CONTROL_ADDRESS=<server-dns-or-lan-ip>:9093
```

Each physical client must use a unique `CLIENT_ID` from the server's `clients.yml` inventory.

### 7.2 Install the server CA

Copy the server's `ca.crt` to:

```text
certificates/prod/tls/ca.crt
```

The client setup validates that this file exists before continuing.

### 7.3 Create the client identity

Run:

```bash
./setup.sh
```

Select the client-host role and the assigned client ID if they are not already defined in `.env`.

For production, setup creates a client-specific ECDSA P-384 SuperNode authentication key pair when one does not already exist:

```text
certificates/prod/auth/
├── client-1
└── client-1.pub
```

The private key remains on that physical client host. The public key is the material that must be transferred to the server for authorization.

A physical client creates and uses **only its own authentication identity**. It should never receive another client's private key.

### 7.4 Prepare local data and checkpoints

The selected client entry in `clients.yml` supplies the local data and checkpoint paths. `setup.sh` creates those directories when required.

Each client supplies its own local dataset. The dataset must satisfy the application's shared DataContract before it can participate in training.

## 8. Transfer client public keys to the server

For each physical client, transfer only its public key to the server host.

For example:

```text
client-1 host
  certificates/prod/auth/client-1.pub
                |
                v
server host
  certificates/prod/auth/client-1.pub
```

Repeat for each configured client.

Never transfer:

- client private authentication keys
- the SuperLink private key
- unrelated host credentials

The server's `clients.yml` must reference the corresponding public-key paths.

## 9. Generate the server Compose deployment

On the server host:

```bash
python3 scripts/generate_compose.py \
  --config clients.yml \
  --output docker-compose.server.yml \
  --profile production \
  --role server
```

The generated server deployment contains the SuperLink, ServerApp, trainer, and production client-registration service.

The production SuperLink exposes:

| Port | API | Distributed purpose |
|---:|---|---|
| 9091 | Runtime | Internal ServerApp ↔ SuperLink communication |
| 9092 | Fleet | SuperNode ↔ SuperLink federated communication |
| 9093 | Control | Flower CLI/registration control operations |

Port `9091` is used internally by the Docker deployment and does not need to be exposed to physical clients.

## 10. Register authorized SuperNodes

Registration is performed from the server-side production deployment using the public keys supplied in `clients.yml` and the configured authentication directory.

The generated server Compose includes:

```text
client-registration
```

The registration service mounts:

- `clients.yml` read-only
- the federation CA certificate read-only
- the server-side public authentication keys read-only
- Flower CLI configuration read-only

It does not require client private keys.

Start the server infrastructure first, then run the registration service through the generated Compose deployment:

```bash
docker compose -f docker-compose.server.yml up -d superlink superexec-serverapp
```

Then:

```bash
docker compose -f docker-compose.server.yml run --rm client-registration
```

The registration workflow connects to the SuperLink Control API using the configured production TLS trust chain and registers the configured public identities.

Registration is idempotent for identities that are already registered; rerunning the workflow should not require generating a new key merely because the identity is already known to the SuperLink.

## 11. Generate each client Compose deployment

On each physical client host, generate a Compose file containing only the assigned client:

```bash
python3 scripts/generate_compose.py \
  --config clients.yml \
  --output docker-compose.client.yml \
  --profile production \
  --role client \
  --client-id client-1
```

For another physical host, replace `client-1` with its assigned ID.

The generated client deployment contains exactly:

- one SuperNode for the selected client ID
- one ClientApp for the selected client ID

The client SuperNode receives:

- the federation CA certificate
- its own authentication private key

It does not receive the SuperLink certificate/private key or another client's private authentication key.

The SuperNode is configured with the production `SUPERLINK_ADDRESS` and Flower's `--root-certificates` option. Production SuperNode authentication uses the selected client's private key.

## 12. Start the distributed federation

The recommended startup order is:

```text
1. Server SuperLink
       |
       v
2. ServerApp infrastructure
       |
       v
3. Register authorized SuperNode public keys
       |
       v
4. Start physical client SuperNodes + ClientApps
       |
       v
5. Start the server trainer
       |
       v
6. Federated rounds execute
```

### 12.1 Start the server

On the server:

```bash
docker compose -f docker-compose.server.yml up -d superlink superexec-serverapp
```

If registration has not yet been completed, run the registration service as described above.

### 12.2 Start each physical client

On each client host:

```bash
docker compose -f docker-compose.client.yml up --build
```

The SuperNode establishes the TLS-protected Fleet connection to the server and authenticates using its client-specific identity.

The ClientApp connects to its local SuperNode through the Docker network on the client host.

### 12.3 Start the trainer

After the required client SuperNodes are online, start the trainer on the server:

```bash
docker compose -f docker-compose.server.yml up trainer
```

The production trainer uses the `production-deployment` Flower profile defined in `.flwr/config.toml`.

The production trainer configuration must use a SuperLink Control API address whose certificate identity matches the SuperLink certificate SAN. For a certificate issued to a LAN IP, the trainer must use that IP rather than the Docker-only `superlink` hostname.

## 13. Network requirements

The physical network must permit the following connections.

| Source | Destination | Port | Requirement |
|---|---|---:|---|
| Client SuperNode | Server SuperLink | 9092/TCP | Required |
| Server registration service | Server SuperLink | 9093/TCP | Required |
| Server trainer | Server SuperLink | 9093/TCP | Required |
| Server ServerApp | Server SuperLink | 9091/TCP | Docker-internal |
| Client ClientApp | Local client SuperNode | 9094/TCP | Docker-internal |

The client hosts do not need access to server port `9091` or the client AppIO port on other hosts.

The server firewall must allow TCP `9092` from the participating client hosts. Control API access on TCP `9093` must be reachable by the server-side registration/training workflow as configured.

Use a LAN DNS name or IP address that is reachable from the physical clients. Do not replace the production endpoint with:

```text
superlink:9092
superlink:9093
```

on a physical client host. Those names refer to the Docker network used by services on the same Compose stack.

## 14. TLS endpoint requirements

The Fleet and Control endpoints are separate Flower APIs even though they belong to the same SuperLink.

The deployment must maintain these distinctions:

```text
9092  Fleet API
      SuperNode ↔ SuperLink
      TLS + SuperNode authentication in production

9093  Control API
      Flower CLI / registration / trainer deployment control
      TLS in production

9091  Runtime API
      ServerApp ↔ SuperLink
      Docker-internal Runtime/AppIO connection

9094  Client AppIO
      ClientApp ↔ local SuperNode
      Docker-internal Runtime/AppIO connection
```

The current production hardening scope covers TLS and SuperNode authentication for the Fleet connection and TLS for the Control connection. Runtime/AppIO TLS is a separate hardening concern and is not part of this deployment procedure.

## 15. Verify the deployment before training

Before starting a federated run, verify the following on the server:

1. The SuperLink container is running.
2. The SuperLink has its production CA, certificate, and private key mounted.
3. The SuperLink state directory is mounted persistently.
4. The expected client public keys are present.
5. The configured SuperNode identities are registered.
6. The server can reach the configured Control API endpoint.

On each client:

1. The server CA exists at the configured TLS path.
2. The client's private/public authentication key pair exists.
3. The private key belongs only to that client host.
4. The configured `CLIENT_ID` exists in `clients.yml`.
5. The local data and checkpoint directories exist.
6. The configured `SUPERLINK_ADDRESS` is reachable from the client host.

During startup, verify that each SuperNode establishes its authenticated connection to the SuperLink without TLS hostname/SAN errors.

## 16. Federated training verification

A successful distributed run should demonstrate all of the following:

```text
Trainer
   |
   | sample clients
   v
SuperLink
   |
   +----> client-1 SuperNode -> ClientApp -> local training
   |
   +----> client-2 SuperNode -> ClientApp -> local training
   |
   +----> client-3 SuperNode -> ClientApp -> local training
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

For each round, confirm that the trainer reports the expected sampled clients and that the ServerApp completes aggregation.

When model saving is enabled, the server writes the global model and final metrics to the configured global checkpoint directory.

## 17. Failure and resilience behavior

The current ServerApp deliberately distinguishes between successful client responses and failed responses.

### All sampled clients succeed

For example:

```text
3 sampled
3 successful
3/3 aggregated
```

The round proceeds normally.

### One client fails

For example:

```text
3 sampled
2 successful
1 failed
2/3 aggregated
```

The failed response is excluded from aggregation. The round proceeds because the current minimum successful-client threshold is two.

### Two clients fail

For example:

```text
3 sampled
1 successful
2 failed
1/3 successful
```

The round aborts because fewer than two successful client responses are available.

This prevents the system from silently producing an aggregate from a single participating client.

### Authentication failure

A SuperNode using an unregistered or unauthorized identity must not participate in federated communication.

### TLS identity failure

If a client connects using a hostname/IP that is not represented by the SuperLink certificate SAN, TLS negotiation can fail before federated communication begins. Correct the endpoint or certificate identity rather than bypassing TLS validation.

## 18. Step 7 acceptance test

The minimum Step 7 acceptance test uses at least two physical client machines with different IP addresses.

### Test A — two-client federation

1. Prepare the server with production configuration.
2. Prepare the server TLS material.
3. Distribute the server `ca.crt` to both clients.
4. Create a unique client identity on each client.
5. Transfer only each public key to the server.
6. Register both public keys.
7. Start the server SuperLink and ServerApp infrastructure.
8. Start client-1.
9. Start client-2.
10. Start the trainer.
11. Confirm both clients are sampled.
12. Confirm both clients complete local training.
13. Confirm FedAvg aggregation completes.
14. Confirm global evaluation completes.
15. Confirm the global model/checkpoint is written when enabled.
16. Preserve the relevant trainer, SuperLink, SuperNode, and ClientApp logs as test evidence.

**Current status:** this two-client federation has been successfully demonstrated.

### Test B — three-client resilience

When a third physical client is available:

1. Register client-3.
2. Start all three clients.
3. Run a federation with all three available.
4. Confirm `3/3` successful participation and aggregation.

Then:

1. Stop or isolate one client.
2. Run another federation.
3. Confirm the failed client response is excluded.
4. Confirm the round proceeds with `2/3` successful clients.

Finally:

1. Make two of the three clients unavailable.
2. Run another federation.
3. Confirm the round aborts because only one successful response remains.

These tests validate both the distributed topology and the current minimum-successful-client policy.

## 19. SuperLink state persistence check

Production SuperLink authentication state is stored in the configured persistent state directory.

To validate persistence:

1. Register the authorized SuperNode public keys.
2. Start the production SuperLink.
3. Confirm authorized SuperNodes authenticate.
4. Recreate/restart the SuperLink container without deleting the host state directory.
5. Confirm the registered identities remain available.
6. Confirm authorized SuperNodes can authenticate again without unnecessary re-registration.

Do not use `docker compose down -v` during this test because persistent state must survive container recreation.

## 20. Operational notes

### Do not share private keys between clients

Each physical client has one identity. A client should never be configured with another client's private authentication key.

### Do not copy the SuperLink private key to clients

Clients need the CA certificate to trust the SuperLink, not the SuperLink's private key.

### Do not use `--insecure` in production

Production configuration validation rejects insecure Flower transport settings for the production Fleet deployment.

### Do not delete persistent state during normal restarts

The SuperLink database contains authorization state required to preserve registered identities across container recreation.

### Do not manually maintain generated Compose files as the source of truth

Update `clients.yml` and the deployment environment, then regenerate Compose with `scripts/generate_compose.py`.

## 21. Troubleshooting guide

### `No match found for server name: superlink`

The client or trainer is connecting using the Docker service name `superlink`, while the certificate was issued for a different DNS name or IP.

Use the certificate's actual SAN-compatible endpoint for the physical deployment.

### `Connection to the SuperLink is unavailable`

Check, in order:

1. SuperLink is running.
2. The client can reach TCP `9092`.
3. The configured endpoint is correct.
4. The CA certificate is present on the client.
5. The SuperLink certificate SAN matches the endpoint.
6. The SuperNode identity is registered.
7. The SuperNode private key matches its registered public key.

### SuperNode is rejected after registration

Confirm:

- the public key registered on the server is the one corresponding to the client's private key;
- the client is using the expected `CLIENT_ID`;
- the SuperLink persistent state directory has not been replaced or deleted;
- the client is connecting to the intended SuperLink instance.

### A client appears in the configuration but is not participating

Check the client host's SuperNode and ClientApp logs separately. A generated Compose file contains separate services for the SuperNode and ClientApp; the ClientApp cannot perform federation by itself.

### Training aborts because too few clients succeeded

This is expected when fewer than two successful client responses are available. Restore at least one additional participating client rather than lowering the minimum solely to make the round complete.

## 22. Scope boundary

This document is intentionally limited to distributed deployment and operation.

- **Deployment procedure:** this document.
- **Project overview and local development:** `README.md`.
- **Security architecture, trust model, credential policy, key management, and security hardening:** `SECURITY.md`.

Do not duplicate security policy here. Refer to `SECURITY.md` when a deployment decision depends on the security model, and use this document for the concrete deployment procedure.
