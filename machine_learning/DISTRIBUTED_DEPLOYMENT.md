# Distributed Federation Deployment

This guide is for Step 7: running Flower 1.33.0 with PyTorch across separate Docker hosts on the same network.

## Target topology

```text
Server host (for example 192.168.1.100)
  ├── SuperLink :9092/:9093/:9091
  ├── ServerApp
  └── Trainer

Client host A
  └── SuperNode + ClientApp (client-1)

Client host B
  └── SuperNode + ClientApp (client-2)
```

Each client host runs only its own client ID. Client SuperNodes connect to the server using `SUPERLINK_ADDRESS`; they do not use the Docker-only hostname `superlink`.

## 1. Server host

Clone the same repository revision on the server host and enter `machine_learning/`.

Create `.env` from `.env.production.example` and set:

```dotenv
DEPLOYMENT_PROFILE=production
DEPLOYMENT_ROLE=server
SUPERLINK_ADDRESS=192.168.1.100:9092
SUPERLINK_CONTROL_ADDRESS=192.168.1.100:9093
```

Configure the production TLS and authentication paths as described in `.env.production.example`.

Prepare the server host with `setup.sh`. For the production profile, the setup creates starter TLS credentials on the **server only**:

- `ca.crt`
- `superlink.crt`
- `superlink.key`

These are valid starter credentials for distributed integration testing and are not the final federation PKI credentials.

**Before preparing client hosts, the server administrator must share the server's `ca.crt` with every client.** All clients must use this same CA certificate to trust the SuperLink certificate. Clients must not generate their own CA certificate.

Generate the SuperLink certificate with a SAN matching the server DNS name or LAN IP used by clients. For development certificates, `scripts/generate_dev_certs.py --superlink-host <server-ip-or-dns>` can be used; do not use development certificates for a production deployment.

Start the server federation services before registering clients. The production server Compose exposes the SuperLink Fleet API on `9092` and Control API on `9093`.

## 2. Client host

On each physical client host, clone the same repository revision and enter `machine_learning/`.

Copy `.env.production.example` to `.env` and set a unique role and client ID. For example, client 1 uses:

```dotenv
DEPLOYMENT_PROFILE=production
DEPLOYMENT_ROLE=client
CLIENT_ID=client-1
SUPERLINK_ADDRESS=192.168.1.100:9092
SUPERLINK_CONTROL_ADDRESS=192.168.1.100:9093
```

Before client preparation, copy the **server's `ca.crt`** into the client's configured `TLS_CERTIFICATE_HOST_DIR`. Do not generate a new CA on the client. The client uses the server-provided CA to validate the SuperLink TLS certificate.

Then run:

```bash
./setup.sh
```

Choose the client host role and the assigned client ID when prompted. In the production profile, setup now performs the complete client identity bootstrap automatically:

1. Creates the client-specific EC P-384 SuperNode key pair if it does not already exist.
2. Validates the server CA and client key pair.
3. Connects to the SuperLink **Control API** at `SUPERLINK_CONTROL_ADDRESS` using TLS.
4. Starts a temporary Python 3.11 Docker container.
5. Installs exactly `flwr==1.33.0` inside that temporary container.
6. Runs `flwr supernode register` with the client's public key.
7. Treats an already-registered identity as success, so rerunning setup is safe.
8. Leaves the client's private key exclusively on the client host.

The registration helper is `scripts/register_supernode.py`. It mounts only the public key, CA certificate, and an ephemeral Flower CLI configuration into the temporary container. No private key is mounted into the registration container.

The client operator should **not** need to install Flower or manually run `flwr supernode register`.

The client host needs:

- the `ca.crt` copied from the server host
- its own SuperNode authentication private key
- its own local dataset
- its own checkpoint directory

It does **not** need the SuperLink certificate/private key or the SuperLink state directory.

Generate a client-specific Compose file:

```bash
python3 scripts/generate_compose.py \
  --config clients.yml \
  --output docker-compose.generated.yml \
  --profile production \
  --role client \
  --client-id client-1
```

For the second client host, use `--client-id client-2`.

Build and start the client. The custom SuperExec image is built automatically by Compose:

```bash
docker compose -f docker-compose.generated.yml up --build
```

The client Compose contains exactly two services:

- `supernode-<client-id>`
- `superexec-clientapp-<client-id>`

## 3. Network requirements

The server host must allow inbound TCP traffic to Flower SuperLink port `9092` from the client hosts and TCP `9093` for the Control API used during registration.

The registration container uses the client host's normal Docker bridge network. It does **not** depend on the server's Docker network existing on the client machine. The configured `SUPERLINK_CONTROL_ADDRESS` must therefore be a LAN-reachable DNS name or IP address.

Also verify that the hostname/IP in `SUPERLINK_ADDRESS` and `SUPERLINK_CONTROL_ADDRESS` matches the SuperLink certificate SAN where applicable.

Do not replace the production endpoint with `superlink:9092` or `superlink:9093` on a physical client host; those hostnames exist only inside the server's Docker network.

## 4. Step 7 acceptance test

Use at least two physical client hosts with different IP addresses:

1. Prepare the server host and create the starter server TLS credentials.
2. Start the server SuperLink with production TLS and SuperNode authentication enabled.
3. Share the server's `ca.crt` with client-1 and client-2.
4. Prepare client-1 with `CLIENT_ID=client-1`; setup creates its key pair and registers its public key automatically.
5. Prepare client-2 with `CLIENT_ID=client-2`; setup creates its key pair and registers its public key automatically.
6. Start client-1 on host A.
7. Start client-2 on host B.
8. Confirm both SuperNodes authenticate and connect to the SuperLink.
9. Start the trainer on the server host.
10. Confirm both clients participate in each configured round.
11. Confirm FedAvg aggregation completes.
12. Confirm global evaluation runs.
13. Capture the SuperLink, SuperNode, ClientApp, and trainer logs as evidence.
14. Repeat with a third client if available.

The existing resilience behavior remains active: failed client replies are excluded from aggregation, while fewer than two successful clients abort the round rather than producing an invalid aggregate.
