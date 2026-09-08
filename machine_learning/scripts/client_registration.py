#!/usr/bin/env python3
"""Register configured SuperNode public keys with the Flower SuperLink."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLIENTS_FILE = ROOT / "clients.yml"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_clients() -> list[dict]:
    with CLIENTS_FILE.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    clients = data.get("clients", [])
    if not isinstance(clients, list):
        raise RuntimeError("clients.yml must contain a 'clients' list.")
    return clients


def register(client_id: str, public_key: Path, ca_file: Path, control_address: str) -> bool:
    command = [
        "flower-supernode",
        "--client-app", "src.client_app:app",
        "--superlink", control_address,
        "--root-certificates", str(ca_file),
        "--auth-supernode-public-key", str(public_key),
        "--client-id", client_id,
    ]
    # The registration operation is delegated to the existing Flower CLI.
    # Capture its output so JSON success=false cannot be mistaken for process success.
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    print(output, flush=True)
    try:
        payload = json.loads(completed.stdout.strip())
        return completed.returncode == 0 and payload.get("success") is True
    except json.JSONDecodeError:
        return completed.returncode == 0 and "success": true in output.lower()


def main() -> int:
    control_address = env("SUPERLINK_CONTROL_ADDRESS")
    ca_file = Path(env("TLS_CERTIFICATE_HOST_DIR", "./certificates/prod/tls")) / "ca.crt"
    auth_dir = Path(env("SUPERNODE_AUTH_HOST_DIR", "./certificates/prod/auth"))
    max_attempts = int(env("REGISTRATION_MAX_ATTEMPTS", "5"))
    retry_seconds = int(env("REGISTRATION_RETRY_SECONDS", "10"))

    if not control_address:
        print("ERROR: SUPERLINK_CONTROL_ADDRESS is required.", file=sys.stderr)
        return 1
    if not ca_file.is_file():
        print(f"ERROR: Federation CA certificate not found: {ca_file}", file=sys.stderr)
        return 1

    clients = load_clients()
    if not clients:
        print("ERROR: No clients are configured in clients.yml.", file=sys.stderr)
        return 1

    failures = []
    for client in clients:
        client_id = str(client.get("id", "")).strip()
        public_key_value = str(client.get("public_key", "")).strip()
        if not client_id:
            print("ERROR: A client entry is missing its id.", file=sys.stderr)
            failures.append("<missing-id>")
            continue
        public_key = Path(public_key_value) if public_key_value else auth_dir / f"{client_id}.pub"
        if not public_key.is_absolute():
            public_key = ROOT / public_key
        if not public_key.is_file():
            print(f"ERROR: Public key for {client_id} not found: {public_key}", file=sys.stderr)
            failures.append(client_id)
            continue

        registered = False
        for attempt in range(1, max_attempts + 1):
            print(f"Registering SuperNode {client_id} (attempt {attempt}/{max_attempts})...", flush=True)
            if register(client_id, public_key, ca_file, control_address):
                print(f"SuperNode {client_id} registered successfully.", flush=True)
                registered = True
                break
            if attempt < max_attempts:
                print(f"Registration failed for {client_id}; retrying in {retry_seconds}s...", flush=True)
                time.sleep(retry_seconds)
        if not registered:
            failures.append(client_id)

    if failures:
        print(f"ERROR: Registration failed for: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("All configured SuperNodes registered successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
