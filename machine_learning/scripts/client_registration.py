#!/usr/bin/env python3
"""Register configured Flower SuperNode public keys and report their status."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


CLIENTS_FILE = Path(os.environ.get("CLIENTS_FILE", "/app/clients.yml"))
PUBLIC_KEY_DIR = Path(os.environ.get("PUBLIC_KEY_DIR", "/app/certificates/prod/auth"))
PROFILE = os.environ.get("FLOWER_PROFILE", "production-deployment")
MIN_CLIENTS = 2


class ConfigError(RuntimeError):
    pass


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_clients(path: Path) -> list[dict[str, str]]:
    """Parse the small, controlled clients.yml schema used by this deployment."""
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
        if Path(public_key).name != f"{client_id}.pub":
            raise ConfigError(
                f"Client '{client_id}' public_key must end with '{client_id}.pub'."
            )
        seen.add(client_id)
        normalized.append({"id": client_id, "public_key": public_key})

    if len(normalized) < MIN_CLIENTS:
        raise ConfigError(
            f"At least {MIN_CLIENTS} clients are required; found {len(normalized)}."
        )
    return normalized


def registration_key(client: dict[str, str]) -> Path:
    """Return the public key at the path mounted into the registration container."""
    return PUBLIC_KEY_DIR / f"{client['id']}.pub"


def run_flwr(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a Flower CLI command and capture its output."""
    return subprocess.run(
        ["flwr", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        part for part in (result.stdout, result.stderr) if part
    ).strip()


def register_client(client: dict[str, str]) -> tuple[str, str]:
    client_id = client["id"]
    public_key = registration_key(client)

    if not public_key.is_file():
        return "FAILED", f"public key not mounted: {public_key}"

    result = run_flwr(
        "supernode",
        "register",
        str(public_key),
        PROFILE,
        "--format",
        "json",
    )
    output = combined_output(result)

    if result.returncode == 0:
        return "REGISTERED", output or "registration completed"

    return "FAILED", output or "Flower registration command failed"


def list_registered_clients() -> tuple[int, str]:
    result = run_flwr(
        "supernode",
        "list",
        PROFILE,
        "--format",
        "json",
        "--verbose",
    )
    return result.returncode, combined_output(result)


def main() -> int:
    try:
        clients = parse_clients(CLIENTS_FILE)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"Registering {len(clients)} configured SuperNodes with Flower profile '{PROFILE}'.",
        flush=True,
    )

    results: list[tuple[str, str]] = []
    for client in clients:
        client_id = client["id"]
        print(f"\nRegistering {client_id}...", flush=True)
        status, detail = register_client(client)
        results.append((client_id, status))
        print(f"  {client_id}: {status}", flush=True)
        if detail:
            print(detail, flush=True)

    print("\nRegistration status", flush=True)
    print("==================", flush=True)
    for client_id, status in results:
        print(f"{client_id}: {status}", flush=True)

    failures = [client_id for client_id, status in results if status == "FAILED"]
    if failures:
        print(
            f"\nERROR: Registration failed for: {', '.join(failures)}",
            file=sys.stderr,
        )
        return 1

    print("\nRegistered SuperNodes reported by Flower:", flush=True)
    list_returncode, list_output = list_registered_clients()
    if list_output:
        print(list_output, flush=True)

    if list_returncode != 0:
        print("ERROR: Failed to list registered SuperNodes.", file=sys.stderr)
        return 1

    print("\nAll configured SuperNodes processed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
