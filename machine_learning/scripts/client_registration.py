#!/usr/bin/env python3
"""Register configured Flower SuperNode public keys and verify the registry."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


CLIENTS_FILE = Path(os.environ.get("CLIENTS_FILE", "/app/clients.yml"))
PUBLIC_KEY_DIR = Path(os.environ.get("PUBLIC_KEY_DIR", "/app/certificates/prod/auth"))
PROFILE = os.environ.get("FLOWER_PROFILE", "production-deployment")
FLOWER_HOME = os.environ.get("FLOWER_HOME", "/app")
MIN_CLIENTS = 2


class ConfigError(RuntimeError):
    pass


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_clients(path: Path) -> list[dict[str, str]]:
    """Parse the intentionally small clients.yml structure used by this deployment."""
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
        raise ConfigError(
            f"At least {MIN_CLIENTS} clients are required; found {len(normalized)}."
        )
    return normalized


def canonical_public_key(client: dict[str, str]) -> Path:
    client_id = client["id"]
    configured = Path(client["public_key"])
    if configured.name != f"{client_id}.pub":
        raise ConfigError(
            f"Client '{client_id}' public_key must end with '{client_id}.pub'. "
            f"Got: {configured}"
        )
    return PUBLIC_KEY_DIR / f"{client_id}.pub"


def run_flower(args: list[str]) -> tuple[int, str]:
    env = os.environ.copy()
    # Flower CLI resolves its config under $HOME/.flwr. Compose mounts the
    # production config at /app/.flwr, so make /app the CLI home explicitly.
    env["HOME"] = FLOWER_HOME
    completed = subprocess.run(
        ["flwr", *args],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    output = "\n".join(
        part for part in (completed.stdout, completed.stderr) if part
    ).strip()
    return completed.returncode, output


def json_success(output: str) -> bool | None:
    """Return Flower's JSON success value when output is JSON, otherwise None."""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("success"), bool):
        return payload["success"]
    return None


def register_one(client: dict[str, str]) -> tuple[str, str]:
    client_id = client["id"]
    public_key = canonical_public_key(client)
    if not public_key.is_file():
        return "FAILED", f"public key not mounted: {public_key}"

    returncode, output = run_flower(
        [
            "supernode",
            "register",
            str(public_key),
            PROFILE,
            "--format",
            "json",
        ]
    )

    success = json_success(output)
    if returncode == 0 and success is not False:
        return "REGISTERED", output or "registration completed"

    detail = output or "Flower registration command failed"
    return "FAILED", detail


def list_registered() -> tuple[bool, str]:
    returncode, output = run_flower(
        [
            "supernode",
            "list",
            PROFILE,
            "--format",
            "json",
            "--verbose",
        ]
    )
    success = json_success(output)
    ok = returncode == 0 and success is not False
    return ok, output or "Flower SuperNode list command returned no output"


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
        status, detail = register_one(client)
        results.append((client_id, status))
        print(f"  {client_id}: {status}", flush=True)
        if status == "FAILED":
            print(f"  detail: {detail}", flush=True)

    print("\nRegistration status")
    print("==================")
    for client_id, status in results:
        print(f"{client_id}: {status}")

    failures = [client_id for client_id, status in results if status == "FAILED"]
    if failures:
        print(
            f"\nERROR: Registration failed for: {', '.join(failures)}",
            file=sys.stderr,
        )
        return 1

    print("\nRegistered clients reported by Flower")
    print("======================================")
    list_ok, listing = list_registered()
    print(listing, flush=True)
    if not list_ok:
        print("\nERROR: Flower SuperNode listing failed.", file=sys.stderr)
        return 1

    print("\nAll configured clients are registered and Flower registry listing succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
