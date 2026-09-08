#!/usr/bin/env python3
"""Register every configured SuperNode public key with the Flower SuperLink."""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import yaml

ROOT = Path("/app")
CLIENTS_FILE = ROOT / "clients.yml"
CA_FILE = ROOT / "tls" / "ca.crt"
CONFIG_FILE = ROOT / "registration-config.toml"

def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()

def load_clients() -> list[dict]:
    with CLIENTS_FILE.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    clients = data.get("clients", [])
    if not isinstance(clients, list) or not clients:
        raise RuntimeError("clients.yml must contain a non-empty 'clients' list.")
    return clients

def register(client_id: str, public_key: Path, profile: str) -> bool:
    command = ["flwr", "supernode", "register", str(public_key), profile, "--format", "json"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    stdout, stderr = result.stdout.strip(), result.stderr.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
            print(json.dumps(payload, indent=2), flush=True)
            if payload.get("success") is not True:
                return False
        except json.JSONDecodeError:
            print(stdout, flush=True)
    if stderr:
        print(stderr, file=sys.stderr, flush=True)
    if result.returncode != 0:
        return False
    print(f"SuperNode {client_id} registered successfully.", flush=True)
    return True

def main() -> int:
    control_address = env("SUPERLINK_CONTROL_ADDRESS")
    profile = env("REGISTRATION_PROFILE", "production-registration")
    max_attempts = int(env("REGISTRATION_MAX_ATTEMPTS", "5"))
    retry_seconds = int(env("REGISTRATION_RETRY_SECONDS", "10"))
    if not control_address:
        print("ERROR: SUPERLINK_CONTROL_ADDRESS is required.", file=sys.stderr)
        return 1
    if not CA_FILE.is_file():
        print(f"ERROR: Federation CA certificate not found: {CA_FILE}", file=sys.stderr)
        return 1
    CONFIG_FILE.write_text(
        "[superlink]\n"
        f'default = "{profile}"\n\n'
        f"[superlink.{profile}]\n"
        f'address = "{control_address}"\n'
        f'root-certificates = "{CA_FILE}"\n', encoding="utf-8"
    )
    failures: list[str] = []
    for client in load_clients():
        client_id = str(client.get("id", "")).strip()
        configured_key = str(client.get("public_key", "")).strip()
        if not client_id:
            print("ERROR: Client entry is missing its id.", file=sys.stderr)
            failures.append("<missing-id>")
            continue
        public_key = Path(configured_key) if configured_key else Path(f"auth/{client_id}.pub")
        if not public_key.is_absolute():
            public_key = ROOT / public_key
        if not public_key.is_file():
            print(f"ERROR: Public key for {client_id} not found: {public_key}", file=sys.stderr)
            failures.append(client_id)
            continue
        success = False
        for attempt in range(1, max_attempts + 1):
            print(f"Registering SuperNode {client_id} with {control_address} (attempt {attempt}/{max_attempts})...", flush=True)
            if register(client_id, public_key, profile):
                success = True
                break
            if attempt < max_attempts:
                print(f"Registration failed for {client_id}; retrying in {retry_seconds}s...", flush=True)
                time.sleep(retry_seconds)
        if not success:
            failures.append(client_id)
    if failures:
        print(f"ERROR: Registration failed for: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("All configured SuperNodes registered successfully.", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
