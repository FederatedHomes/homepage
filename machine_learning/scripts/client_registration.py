#!/usr/bin/env python3
"""Batch-register configured Flower SuperNode public keys.

The registration image intentionally contains only Python, Flower, and this
stdlib-only orchestrator. The script supports the small, controlled clients.yml
schema used by this deployment and keeps registration status for every client.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import time


CLIENTS_FILE = Path(os.environ.get("CLIENTS_FILE", "/app/clients.yml"))
PUBLIC_KEY_DIR = Path(os.environ.get("PUBLIC_KEY_DIR", "/app/certificates/prod/auth"))
PROFILE = os.environ.get("FLOWER_PROFILE", "production-deployment")
MAX_ATTEMPTS = max(1, int(os.environ.get("REGISTRATION_MAX_ATTEMPTS", "3")))
RETRY_DELAY = max(0, int(os.environ.get("REGISTRATION_RETRY_DELAY_SECONDS", "5")))
MIN_CLIENTS = 2


class ConfigError(RuntimeError):
    pass


def parse_clients(path: Path) -> list[dict[str, str]]:
    """Parse the intentionally small clients.yml structure without PyYAML."""
    if not path.is_file():
        raise ConfigError(f"clients.yml not found: {path}")

    clients: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_clients = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "clients:":
            in_clients = True
            continue
        if not in_clients:
            continue
        if line.startswith("- "):
            if current is not None:
                clients.append(current)
            current = {}
            key_value = line[2:].strip()
            if ":" in key_value:
                key, value = key_value.split(":", 1)
                current[key.strip()] = unquote(value.strip())
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.strip()] = unquote(value.strip())

    if current is not None:
        clients.append(current)

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for client in clients:
        client_id = client.get("id", "").strip()
        public_key = client.get("public_key", "").strip()
        if not client_id:
            raise ConfigError("Every configured client must define an id.")
        if client_id in seen:
            raise ConfigError(f"Duplicate client ID in clients.yml: {client_id}")
        if not public_key:
            raise ConfigError(f"Client '{client_id}' must define public_key.")
        seen.add(client_id)
        normalized.append({"id": client_id, "public_key": public_key})

    if len(normalized) < MIN_CLIENTS:
        raise ConfigError(f"At least {MIN_CLIENTS} clients are required; found {len(normalized)}.")
    return normalized


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def canonical_public_key(client: dict[str, str]) -> Path:
    """Resolve the configured host path to the read-only key mounted by Compose."""
    client_id = client["id"]
    configured = Path(client["public_key"])
    if configured.name != f"{client_id}.pub":
        raise ConfigError(
            f"Client '{client_id}' public_key must end with '{client_id}.pub' "
            f"for the secure registration mount. Got: {configured}"
        )
    return PUBLIC_KEY_DIR / f"{client_id}.pub"


def is_already_registered(output: str) -> bool:
    text = output.lower()
    return any(
        marker in text
        for marker in (
            "already registered",
            "already exists",
            "already been registered",
            "registration already exists",
        )
    )


def register_one(client: dict[str, str]) -> tuple[str, str]:
    client_id = client["id"]
    public_key = canonical_public_key(client)
    if not public_key.is_file():
        return "FAILED", f"public key not mounted: {public_key}"

    command = [
        "flwr",
        "supernode",
        "register",
        str(public_key),
        PROFILE,
        "--format",
        "json",
    ]

    last_output = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        last_output = output
        if completed.returncode == 0:
            return "REGISTERED", output or "registration completed"
        if is_already_registered(output):
            return "ALREADY REGISTERED", output
        if attempt < MAX_ATTEMPTS:
            print(f"  {client_id}: attempt {attempt}/{MAX_ATTEMPTS} failed; retrying in {RETRY_DELAY}s...", flush=True)
            time.sleep(RETRY_DELAY)

    detail = last_output.splitlines()[-1] if last_output else "Flower registration command failed"
    return "FAILED", detail


def main() -> int:
    try:
        clients = parse_clients(CLIENTS_FILE)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Registering {len(clients)} configured SuperNodes with Flower profile '{PROFILE}'.", flush=True)
    results: list[tuple[str, str, str]] = []
    for client in clients:
        client_id = client["id"]
        print(f"\nRegistering {client_id}...", flush=True)
        status, detail = register_one(client)
        results.append((client_id, status, detail))
        print(f"  {client_id}: {status}", flush=True)
        if status == "FAILED":
            print(f"  detail: {detail}", flush=True)

    print("\nRegistration summary")
    print("===================")
    for client_id, status, _ in results:
        print(f"{client_id}: {status}")

    failures = [client_id for client_id, status, _ in results if status == "FAILED"]
    if failures:
        print(f"\nERROR: Registration failed for: {', '.join(failures)}", file=sys.stderr)
        return 1

    print("\nAll configured clients are registered or already registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
