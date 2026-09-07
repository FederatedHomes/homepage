# Security and deployment profiles

This project uses explicit deployment profiles so development convenience does not silently weaken the production federation.

## Deployment profiles

The federated learning application has two explicit deployment profiles:

- `development` — the local Docker/Compose workflow. This profile may use Flower's `--insecure` transport for local integration testing.
- `production` — the secure deployment profile. Production requires an explicit SuperLink address, TLS certificate/key paths, SuperNode authentication credentials, and a persistent SuperLink state directory. Production Fleet commands reject `--insecure`.

The default profile is `development` so existing local workflows remain unchanged.

## Flower network channels

Flower exposes separate APIs on the SuperLink. The current Docker deployment intentionally treats them differently:

| Connection | Port | Flower API | Current security | Purpose |
|---|---:|---|---|---|
| SuperExec → SuperLink | 9091 | Runtime | Internal/plaintext | ServerApp execution and Runtime communication |
| SuperNode → SuperLink | 9092 | Fleet | **TLS + SuperNode authentication in production** | Federated communication |
| Flower CLI → SuperLink | 9093 | Control | **TLS in production** | Deployment/control operations |
| SuperExec → SuperNode | 9094 | Runtime | Internal/plaintext | ClientApp execution and Runtime communication |

The `superlink:9092` address therefore refers to the Fleet API used by SuperNodes. The `superlink:9093` address in `.flwr/config.toml` refers to the Control API used by the Flower CLI. These are different APIs on the same SuperLink and are not interchangeable.

The current `--insecure` flags on `superexec-serverapp` and `superexec-clientapp` apply to the separate Runtime/AppIO connections. They do **not** disable TLS or SuperNode authentication on the production Fleet connection. Runtime/AppIO TLS is a separate future hardening step and is intentionally outside the current authentication-layer scope.

## TLS infrastructure

Production federation TLS uses a CA certificate to verify the SuperLink and a SuperLink certificate/private key to identify the SuperLink:

```text
/etc/flower/tls/
├── ca.crt
├── superlink.crt
└── superlink.key
```

The SuperLink receives all three files. SuperNodes receive only `ca.crt` and connect with Flower's `--root-certificates` option. The SuperLink private key must never be distributed to SuperNodes.

For controlled development testing, `scripts/generate_dev_certs.py` can create a local CA and SuperLink certificate. Generated material is stored under `certificates/`, which is ignored by Git. Production certificates must come from the organization's chosen PKI/certificate authority.

## Docker deployment

`machine_learning/scripts/generate_compose.py` is the source for the generated Compose deployment. It creates one SuperLink, one SuperNode per configured client, the corresponding SuperExec services, a trainer, and a Docker bridge network.

Production Compose generation mounts TLS material read-only and enables SuperNode authentication on the Fleet connection. Each production SuperNode receives only its own authentication private key. The SuperLink also receives a persistent state directory and starts Flower with a database path inside that directory so registered SuperNode identities survive container recreation.

The production generator also validates that the generated SuperLink and SuperNode Fleet commands do not contain `--insecure`.

The Flower trainer selects the named `production-deployment` profile from `.flwr/config.toml`. That name identifies the Flower CLI deployment configuration; it is not a port, client ID, or SuperNode identity.

## Production configuration

Start from `.env.production.example` and provide deployment-specific values on the target host. Never commit the populated `.env` file, TLS private keys, SuperNode private authentication keys, or runtime state databases.

The production validator requires:

- `DEPLOYMENT_PROFILE=production`
- `SUPERLINK_ADDRESS`
- `TLS_ROOT_CERTIFICATES`
- `SUPERLINK_CERTIFICATE`
- `SUPERLINK_PRIVATE_KEY`
- `TLS_CERTIFICATE_HOST_DIR`
- `SUPERNODE_AUTH_PRIVATE_KEY_DIR`
- `SUPERNODE_AUTH_HOST_DIR`
- `SUPERLINK_STATE_HOST_DIR`
- `SUPERLINK_STATE_DIR`

`SUPERLINK_STATE_HOST_DIR` must point to a persistent host directory that survives SuperLink container recreation. `SUPERLINK_STATE_DIR` is the writable directory inside the SuperLink container; the generator uses `<SUPERLINK_STATE_DIR>/superlink.db` as Flower's database path.

## Secrets handling

Private keys and other runtime credentials belong outside Git. The repository ignores `.env`, certificate/key files, the local `certificates/` tree, persistent `state/`, and Flower runtime state under `machine_learning/.flwr/`.

Each production service should receive only the credentials it needs.

### SuperNode authentication

Production SuperNode authentication is enabled with Flower's `--enable-supernode-auth` option on the SuperLink. Each authorized SuperNode has a unique ECDSA P-384 key pair in SSH/OpenSSH format. The public key is registered with the SuperLink; the corresponding private key remains with that SuperNode.

The repository's development helper can generate local authentication identities:

```bash
python scripts/generate_supernode_auth.py \
    --output-dir certificates/dev/auth \
    client-1 client-2 client-3
```

This helper is for development and integration testing only. Production identities must be generated and managed through the organization's approved secret-management/PKI process.

For production, the authentication paths are separated into host and container paths:

```text
Host:
<auth-host-dir>/
├── client-1
├── client-2
└── client-3

Container:
/etc/flower/auth/
├── client-1
├── client-2
└── client-3
```

`SUPERNODE_AUTH_HOST_DIR` identifies the host directory containing the private keys. `SUPERNODE_AUTH_PRIVATE_KEY_DIR` identifies the container directory used by Flower. The Compose generator mounts each SuperNode's key individually and read-only; a SuperNode must never receive the private keys belonging to other clients.

Production configuration requires:

```text
SUPERNODE_AUTH_PRIVATE_KEY_DIR=/etc/flower/auth
SUPERNODE_AUTH_HOST_DIR=<host authentication directory>
```

`setup.sh` validates that the configured authentication directory exists and that every client in `clients.yml` has its own authentication private key before production Compose generation.

### Persistent SuperLink authentication state

SuperNode registration is SuperLink state. In production, that state must survive SuperLink container recreation. The generated production command includes:

```text
--database /var/lib/flower/superlink.db
```

and mounts the configured host state directory to `/var/lib/flower`.

The setup workflow creates the configured `SUPERLINK_STATE_HOST_DIR` before generating Compose. The directory and resulting database are runtime state and are ignored by Git.

The required persistence validation is:

1. Register the authorized SuperNode public keys.
2. Start the production SuperLink and all authorized SuperNodes.
3. Confirm all authorized SuperNodes authenticate.
4. Recreate/restart the SuperLink container without deleting the state directory.
5. Confirm all authorized SuperNodes authenticate again without re-registration.

Do not use `docker compose down -v` for this validation because persistent state must not be removed.

### Register authorized SuperNodes

Generating a key pair does not authorize a SuperNode. Register each public key with the configured SuperLink:

```bash
flwr supernode register \
    certificates/prod/auth/client-1.pub \
    production-deployment
```

Verify the registered identities with:

```bash
flwr supernode list production-deployment
```

Only public keys belonging to authorized SuperNodes should be registered.

Flower registration expects an OpenSSH ECDSA public key, such as:

```text
ecdsa-sha2-nistp384 AAAA...
```

Do not use a PEM public-key file beginning with `-----BEGIN PUBLIC KEY-----`.

### Validate authentication keys

Private-key material must never be printed or committed. To validate a private key without displaying it:

```bash
ssh-keygen -y -f certificates/prod/auth/client-1 > /dev/null
```

Repeat for every configured SuperNode.

To compare public-key fingerprints:

```bash
for client in client-1 client-2 client-3; do
    echo "$client:"
    ssh-keygen -lf "certificates/prod/auth/$client.pub"
done
```

Each configured client must have a unique fingerprint.

### Production authentication flow

```text
SuperNode private key
        |
        v
SuperNode establishes TLS to SuperLink
        |
        v
SuperNode authentication
        |
   +----+----+
   |         |
accepted   rejected
   |         |
   v         v
federated   no access
training
```

TLS protects the Fleet transport and verifies the SuperLink using the configured CA. SuperNode authentication separately determines whether the connecting SuperNode identity is authorized. Persistent SuperLink state preserves the authorization registry across container recreation.

An authorized SuperNode must connect successfully, while an unregistered SuperNode using a different key must be rejected. An authorized SuperNode must also reconnect successfully after the SuperLink container is recreated without deleting the persistent state directory.

### Key rotation and revocation

If a SuperNode authentication private key is compromised:

1. Stop the affected SuperNode.
2. Unregister/revoke its old public-key identity from the SuperLink.
3. Generate a new unique key pair through the approved credential-management process.
4. Distribute only the new private key to the affected SuperNode.
5. Register the new public key.
6. Verify the new identity before resuming training.

Do not replace a registered key without considering the impact on the existing Node ID and authorization state.

## Scope boundary

Step 6.2 establishes TLS for the SuperLink ↔ SuperNode Fleet transport and the Flower CLI connection used by the deployment runtime. Step 6.3 adds SuperNode authentication to that TLS-protected Fleet connection and persists the SuperLink authorization state. Internal Runtime/AppIO TLS (`--appio-ssl-*`) requires separate per-service certificate/SAN handling and is intentionally kept as a distinct future hardening item rather than sharing the SuperLink private key across services.
