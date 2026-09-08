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
```

Configure the production TLS and authentication paths as described in `.env.production.example`.

The server needs:

- CA certificate
- SuperLink certificate
- SuperLink private key
- persistent SuperLink state directory
- the public/registration material required by the configured SuperNodes

Generate the SuperLink certificate with a SAN matching the server DNS name or LAN IP used by clients. For development certificates, `scripts/generate_dev_certs.py --superlink-host <server-ip-or-dns>` can be used; do not use development certificates for a production deployment.

Generate the production SuperNode authentication identities for the configured clients and place the corresponding authorized material on the server according to the existing Step 6 authentication workflow.

Generate the server Compose file:

```bash
python3 scripts/generate_compose.py \
  --config clients.yml \
  --output docker-compose.generated.yml \
  --profile production \
  --role server
```

The server Compose contains only `superlink`, `superexec-serverapp`, and `trainer`.

Start the server federation services:

```bash
docker build -f Dockerfile.superexec -t flwr_superexec:local .
docker compose -f docker-compose.generated.yml up -d superlink superexec-serverapp
```

Start the trainer when the client hosts are ready:

```bash
docker compose -f docker-compose.generated.yml up trainer
```

## 2. Client host

On each physical client host, clone the same repository revision and enter `machine_learning/`.

Copy `.env.production.example` to `.env` and set a unique role and client ID. For example, client 1 uses:

```dotenv
DEPLOYMENT_PROFILE=production
DEPLOYMENT_ROLE=client
CLIENT_ID=client-1
SUPERLINK_ADDRESS=192.168.1.100:9092
```

The client host needs:

- the shared CA certificate
- only its own SuperNode authentication private key
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

Build the shared application image and start the client:

```bash
docker build -f Dockerfile.superexec -t flwr_superexec:local .
docker compose -f docker-compose.generated.yml up
```

The client Compose contains exactly two services:

- `supernode-<client-id>`
- `superexec-clientapp-<client-id>`

## 3. Network requirements

The server host must allow inbound TCP traffic to Flower SuperLink port `9092` from the client hosts. The server-side application/control-plane ports remain on the server host.

Verify from each client host that the server endpoint is reachable before starting the SuperNode. Also verify that the hostname/IP in `SUPERLINK_ADDRESS` is present in the SuperLink certificate SAN.

Do not replace the production endpoint with `superlink:9092` on a physical client host; that hostname exists only inside the server's Docker network.

## 4. Step 7 acceptance test

Use at least two physical client hosts with different IP addresses:

1. Start the server SuperLink.
2. Start client-1 on host A.
3. Start client-2 on host B.
4. Start the trainer on the server host.
5. Confirm both clients participate in each configured round.
6. Confirm FedAvg aggregation completes.
7. Confirm global evaluation runs.
8. Capture the SuperLink, SuperNode, ClientApp, and trainer logs as evidence.
9. Repeat with a third client if available.

The existing resilience behavior remains active: failed client replies are excluded from aggregation, while fewer than two successful clients abort the round rather than producing an invalid aggregate.
