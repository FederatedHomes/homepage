# Security Architecture and Policy

This document defines the security model, trust boundaries, credential policy, authentication controls, and production security requirements for the federated learning framework.

It is intentionally **not** a deployment runbook. Concrete server/client setup, certificate copying, Compose commands, startup order, network setup, verification, and troubleshooting belong in `DISTRIBUTED_DEPLOYMENT.md`.

Project overview and local development guidance belong in `README.md`.

## 1. Security objectives

The distributed federation must provide the following security properties:

1. **Authenticated server endpoint** — production SuperNodes must verify that they are connecting to the intended SuperLink.
2. **Encrypted federated transport** — production SuperNode ↔ SuperLink Fleet communication must use TLS.
3. **Authenticated SuperNodes** — only registered SuperNode identities may participate in the production federation.
4. **Credential isolation** — each physical client receives only the credentials required for its own identity and operation.
5. **Server private-key protection** — the SuperLink private key must remain on the server and must never be distributed to clients.
6. **Persistent authorization state** — registered SuperNode identities must survive SuperLink container recreation.
7. **No accidental production downgrade** — production Fleet configuration must reject Flower's `--insecure` transport setting.
8. **Separation of trust domains** — Docker-internal Runtime/AppIO communication must not be confused with the externally reachable Fleet and Control APIs.

These objectives describe the security controls implemented by the current deployment. They do not claim that the entire application is production-hardened against every possible threat.

## 2. Deployment security profiles

The application has two explicit deployment profiles:

| Profile | Intended use | Fleet transport | SuperNode authentication | Persistent SuperLink state |
|---|---|---|---|---|
| `development` | Local development/integration | May use `--insecure` | Development configuration | Optional |
| `production` | Distributed federation | TLS required | Required | Required |

The default profile is `development` so local development remains convenient and explicit.

Production configuration must be selected deliberately with:

```dotenv
DEPLOYMENT_PROFILE=production
```

The production deployment configuration rejects `--insecure` for the production Fleet commands. A deployment must not work around this validation to make an otherwise incorrect TLS configuration operate.

## 3. Trust boundaries

The current architecture contains several distinct communication boundaries:

```text
                         SERVER TRUST DOMAIN

                    +-----------------------+
                    |       SuperLink       |
                    |                       |
                    |  9091 Runtime        |
                    |  9092 Fleet          |
                    |  9093 Control        |
                    +----------+------------+
                               |
                     TLS + SuperNode auth
                               |
              +----------------+----------------+
              |                                 |
       CLIENT TRUST DOMAIN               CLIENT TRUST DOMAIN
              |                                 |
        +-----------+                     +-----------+
        | SuperNode |                     | SuperNode |
        | client-1  |                     | client-2  |
        +-----+-----+                     +-----+-----+
              |                                 |
        local ClientApp                    local ClientApp
              |                                 |
           9094                              9094
```

The important boundary is between a physical client and the server's SuperLink Fleet API. That boundary is protected by TLS and SuperNode authentication in production.

The ClientApp ↔ SuperNode and ServerApp ↔ SuperLink Runtime/AppIO paths are separate internal communication paths. They are not interchangeable with the Fleet API.

## 4. Flower network channels

The current Docker architecture uses four relevant Flower API ports:

| Connection | Port | Flower API | Production security | Purpose |
|---|---:|---|---|---|
| ServerApp/SuperExec → SuperLink | 9091 | Runtime | Internal/plaintext | ServerApp execution and Runtime communication |
| SuperNode → SuperLink | 9092 | Fleet | **TLS + SuperNode authentication** | Federated communication |
| Flower CLI/trainer → SuperLink | 9093 | Control | **TLS** | Deployment and control operations |
| ClientApp/SuperExec → SuperNode | 9094 | Runtime | Internal/plaintext | ClientApp execution and Runtime/AppIO communication |

These APIs are distinct.

In particular:

- `superlink:9092` identifies the Docker-network Fleet endpoint.
- `superlink:9093` identifies the Docker-network Control endpoint.
- A physical client must use the server's LAN/DNS endpoint rather than the Docker-only hostname `superlink`.
- The certificate identity used for a production TLS connection must match the DNS name or IP address used by that connection.

The current `--insecure` options on the ServerApp and ClientApp Runtime/AppIO services do **not** mean that the production Fleet connection is insecure. Runtime/AppIO TLS is a separate hardening item.

## 5. TLS architecture

### 5.1 Trust model

Production TLS uses a federation CA as the trust anchor for the SuperLink server certificate:

```text
Federation CA
     |
     +---- signs ----> SuperLink certificate
                              |
                              v
                        SuperLink server
                              ^
                              |
                       verifies with CA
                              |
                         SuperNode
```

The SuperLink holds:

```text
/etc/flower/tls/
├── ca.crt
├── superlink.crt
└── superlink.key
```

A SuperNode receives only:

```text
ca.crt
```

and uses Flower's `--root-certificates` configuration to verify the SuperLink.

The SuperLink private key is server-only credential material.

### 5.2 Certificate identity

The SuperLink certificate must contain a Subject Alternative Name (SAN) matching the endpoint used by the connecting client or control process.

For example, if a physical client connects to:

```text
192.168.0.172:9092
```

then the SuperLink certificate must contain `IP:192.168.0.172` in its SAN.

A Docker service name such as `superlink` is not automatically a valid certificate identity for a physical-host connection.

The endpoint, DNS/IP address, and certificate SAN therefore form one security configuration. Changing one without considering the others can cause TLS verification failure.

### 5.3 Certificate authority handling

The federation CA is a trust anchor and should be treated as security-sensitive infrastructure even though the public CA certificate is not itself secret.

Only the CA certificate should be distributed to clients. The CA private signing key must remain under the approved certificate-management process and must not be stored in the repository.

The repository's starter/development certificate generation exists for controlled integration testing. Production certificates should be issued, stored, renewed, and revoked through the organization's approved PKI process.

## 6. SuperNode authentication architecture

TLS answers:

> “Am I communicating with the trusted SuperLink?”

SuperNode authentication answers:

> “Is this connecting SuperNode authorized to participate?”

These are separate controls and both are required for the production Fleet connection.

### 6.1 SuperNode identity

Each authorized SuperNode has a unique ECDSA P-384 key pair in SSH/OpenSSH format:

```text
client-1 private key  <---- retained only by client-1
client-1 public key   <---- registered with SuperLink
```

The server uses the public key as the authorization record. The corresponding private key proves possession of that identity when the SuperNode connects.

A registered public key does not authorize other keys belonging to the same client ID.

### 6.2 Identity isolation

The physical deployment follows a strict identity-scope rule:

> **One physical client host receives and uses only its own SuperNode authentication private key.**

For example:

```text
client-1 host
    └── client-1 private key

client-2 host
    └── client-2 private key

client-3 host
    └── client-3 private key
```

A client host must not receive:

- another client's private authentication key;
- the SuperLink private key;
- the CA private signing key;
- credentials belonging to unrelated federation identities.

The server may hold the public authorization records for all configured clients. That does not mean private credentials for all clients should be distributed to every client host.

### 6.3 Authentication enablement

Production SuperLink authentication is enabled with Flower's:

```text
--enable-supernode-auth
```

Each production SuperNode is configured with its own private identity through:

```text
--auth-supernode-private-key /etc/flower/auth/<client-id>
```

The Compose deployment mounts the authentication directory read-only into the SuperNode container.

## 7. Public-key authorization registry

Generating a SuperNode key pair is not sufficient to authorize it.

The authorization lifecycle is:

```text
Generate client key pair
        |
        v
Keep private key on client
        |
        +---- transfer public key only ---->
                                             Server
                                               |
                                               v
                                      Register public key
                                               |
                                               v
                                      Authorized identity
                                               |
                                               v
                                      SuperNode connects
                                               |
                              +----------------+----------------+
                              |                                 |
                           accepted                          rejected
```

Only explicitly authorized public keys should be registered with the SuperLink.

The server-side client inventory associates each configured client ID with its public-key path:

```yaml
clients:
  - id: client-1
    data_dir: ./data/client-1
    checkpoint_dir: ./checkpoints/client-1
    public_key: ./certificates/prod/auth/client-1.pub
```

The public-key registry is authorization state, not a secret store.

## 8. Persistent authorization state

Production SuperLink authorization state must survive container recreation.

The deployment configures Flower with a persistent database path equivalent to:

```text
--database /var/lib/flower/superlink.db
```

The host state directory is mounted into the SuperLink container and must not be deleted during ordinary restart/recreation operations.

The security requirement is:

```text
Container recreation
       |
       v
Persistent host state remains
       |
       v
SuperLink database remains
       |
       v
Registered SuperNode identities remain authorized
```

Deleting the state database can change the authorization state of the federation and must be treated as an administrative/security operation rather than an ordinary restart.

## 9. Credential handling policy

### 9.1 Credentials that must remain outside Git

Never commit:

- populated `.env` files;
- SuperLink private keys;
- SuperNode private authentication keys;
- CA private signing keys;
- runtime state databases;
- other deployment-specific secrets.

The repository is configured to ignore local credential/certificate and runtime-state material.

### 9.2 Read-only mounts

Production TLS and SuperNode authentication material should be mounted read-only into containers whenever the service only needs to consume the credential.

This reduces the ability of an application container to modify the host credential material.

### 9.3 Least privilege

Each service should receive only the credential material required for its function.

Examples:

| Service | Credential scope |
|---|---|
| SuperLink | CA, SuperLink certificate, SuperLink private key, persistent authorization state |
| SuperNode | Federation CA + its own private authentication key |
| Registration service | Federation CA + public authentication keys |
| ClientApp | No SuperNode private authentication key |
| ServerApp | No client private authentication keys |

In particular, the registration service does not need client private keys. It operates using public authorization records.

## 10. Production configuration requirements

Production configuration is validated centrally by `src/deployment_config.py`.

The server-side production configuration requires:

```text
DEPLOYMENT_PROFILE
SUPERLINK_ADDRESS
TLS_ROOT_CERTIFICATES
SUPERLINK_CERTIFICATE
SUPERLINK_PRIVATE_KEY
TLS_CERTIFICATE_HOST_DIR
SUPERNODE_AUTH_PRIVATE_KEY_DIR
SUPERNODE_AUTH_HOST_DIR
SUPERLINK_STATE_HOST_DIR
SUPERLINK_STATE_DIR
```

A production client requires the TLS trust configuration, SuperNode authentication paths, and the configured SuperLink address. The client does not require the server's certificate or private key.

The configuration validator also rejects `--insecure` for production Fleet commands.

The security policy is that missing security material is an error; the deployment must not silently fall back to development transport or credentials.

## 11. Development authentication material

The repository provides a development helper for creating local SuperNode authentication identities:

```bash
python scripts/generate_supernode_auth.py \
    --output-dir certificates/dev/auth \
    client-1 client-2 client-3
```

This is intended for development/integration testing.

Production credentials should be generated and managed using the organization's approved credential-management and PKI processes. Development-generated identities must not be treated as production trust anchors merely because they are technically usable.

## 12. Key validation and fingerprints

Private keys must never be printed as part of validation.

An OpenSSH private key can be checked without exposing its contents:

```bash
ssh-keygen -y -f certificates/prod/auth/client-1 > /dev/null
```

The corresponding public-key fingerprint can be inspected with:

```bash
ssh-keygen -lf certificates/prod/auth/client-1.pub
```

Every configured client identity should have a unique public-key fingerprint.

Flower registration expects an OpenSSH ECDSA public key, for example:

```text
ecdsa-sha2-nistp384 AAAA...
```

A PEM public-key file beginning with:

```text
-----BEGIN PUBLIC KEY-----
```

is not the expected registration format.

## 13. Authentication failure policy

Authentication failures must fail closed.

An unregistered SuperNode, or a SuperNode using a private key that does not correspond to an authorized public key, must not participate in federated communication.

Likewise, a TLS validation failure must not be bypassed by switching a production connection to `--insecure`.

The expected production behavior is:

```text
TLS validation
     |
     +---- fail ----> no federation access
     |
     v
SuperNode authentication
     |
     +---- fail ----> no federation access
     |
     v
Authorized federation participation
```

## 14. Key rotation and revocation

A SuperNode authentication key represents a persistent authorization identity and must be rotated deliberately.

If a private key is suspected or known to be compromised:

1. Stop the affected SuperNode.
2. Revoke/unregister the old public-key identity according to the SuperLink administration process.
3. Generate a new unique key pair through the approved credential-management process.
4. Deliver only the new private key to the affected physical client.
5. Register the new public key.
6. Verify the new identity before resuming federation.
7. Retain appropriate audit evidence of the rotation.

Do not simply overwrite a private key while leaving the old public key authorized. That creates an authorization mismatch rather than completing a secure rotation.

Certificate rotation must likewise preserve the trusted CA chain and valid SAN identity required by the deployed endpoints.

## 15. Network exposure policy

Only APIs that must be reachable across physical hosts should be exposed across the host network.

The intended production boundary is:

```text
Physical client hosts
        |
        | TCP 9092
        v
Server SuperLink Fleet API
```

The Runtime/AppIO ports are intended for the local Docker deployment and should not be unnecessarily exposed to the physical client network.

The Control API on `9093` is a separate administrative interface and should be reachable only by the deployment/control components that require it.

Host firewalls and network controls should therefore restrict access according to the actual deployment topology rather than exposing all Flower ports indiscriminately.

## 16. Runtime/AppIO TLS boundary

The current security layer intentionally does not claim end-to-end TLS for the internal Runtime/AppIO connections.

Current status:

```text
Fleet 9092       TLS + SuperNode authentication       IMPLEMENTED
Control 9093     TLS                                  IMPLEMENTED
Runtime 9091     Internal/plaintext                   FUTURE HARDENING
Runtime 9094     Internal/plaintext                   FUTURE HARDENING
```

Runtime/AppIO TLS requires separate certificate, key, trust, and SAN handling for the relevant services. The SuperLink private key must not be reused as a shared credential for those services.

This boundary is deliberate so that the current security controls remain accurately documented rather than implying broader TLS coverage than the implementation provides.

## 17. Security verification requirements

A production security verification should demonstrate:

1. An authorized SuperNode can establish the TLS-protected Fleet connection.
2. An unregistered SuperNode identity is rejected.
3. A SuperNode using the wrong private key is rejected.
4. A certificate/SAN mismatch causes TLS verification failure rather than silent acceptance.
5. Production Fleet commands reject `--insecure`.
6. Client hosts contain only their own private authentication identity.
7. The SuperLink private key is absent from client hosts.
8. SuperNode authorization survives SuperLink container recreation when the persistent state directory is retained.
9. ClientApp and ServerApp services do not receive unnecessary private authentication credentials.

Operational commands and the concrete test procedure are maintained in `DISTRIBUTED_DEPLOYMENT.md`.

## 18. Security limitations and future hardening

The current implementation establishes TLS and SuperNode authentication for the production Fleet path and TLS for the Control path. It does not by itself provide:

- secure aggregation;
- differential privacy;
- protection against malicious or poisoned model updates;
- end-to-end encryption of Runtime/AppIO traffic;
- comprehensive centralized audit logging;
- production PKI lifecycle automation;
- container/runtime isolation beyond the current Docker configuration;
- full network segmentation or firewall policy automation.

These are separate engineering concerns and should not be inferred from the current TLS/authentication layer.

Secure aggregation and privacy mechanisms are planned as later federation-hardening work.

## 19. Documentation scope boundary

This file is the security specification and policy boundary.

- **Security architecture, trust model, credential handling, authentication, key management, and security requirements:** `SECURITY.md`.
- **Distributed deployment procedure, commands, startup sequence, network setup, verification, acceptance tests, and troubleshooting:** `DISTRIBUTED_DEPLOYMENT.md`.
- **Project overview, source structure, development workflow, and general usage:** `README.md`.

The deployment guide may reference these security requirements, but should not redefine them. The README may summarize the security posture, but should not become the authoritative security specification.
