#!/usr/bin/env python3
"""Register configured Flower SuperNode public keys and verify the registry."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


CLIENTS_FILE = Path(os.environ.get("CLIENTS_FILE", "/app/clients.yml"))
PUBLIC_KEY_DIR = Path(os.environ.get("PUBLIC_KEY_DIR", "/app/certificates/prod/auth"))
PROFILE = os.environ.get("FLOWER_PROFILE", "production-deployment")
FLOWER_CONFIG_DIR = Path(os.environ.get("FLOWER_CONFIG_DIR", "/app/.flwr"))
FLOWER_HOME = Path(os.environ.get("FLOWER_HOME", "/tmp/flower-cli-home"))
SUPERLINK_CONTROL_ADDRESS = os.environ.get("SUPERLINK_CONTROL_ADDRESS", "").strip()
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


def prepare_flower_home() -> Path:
    """Create a writable CLI home with a production config using the real control address."""
    source = FLOWER_CONFIG_DIR / "config.toml"
    if not source.is_file():
        raise ConfigError(f"Flower configuration not found: {source}")
    if PROFILE == "production-deployment" and not SUPERLINK_CONTROL_ADDRESS:
        raise ConfigError(
            "SUPERLINK_CONTROL_ADDRESS is required for the production registration profile."
        )

    config = source.read_text(encoding="utf-8")
    if PROFILE == "production-deployment":
        lines = config.splitlines()
        in_profile = False
        replaced = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("["):
                in_profile = stripped == "[superlink.production-deployment]"
            elif in_profile and stripped.startswith("address ="):
                lines[index] = f'address = "{SUPERLINK_CONTROL_ADDRESS}"'
                replaced = True
                break
        if not replaced:
            raise ConfigError(
                "Production SuperLink profile does not contain an address entry."
            )
        config = "\n".join(lines) + "\n"

    config_dir = FLOWER_HOME / ".flwr"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(config, encoding="utf-8")
    return FLOWER_HOME


def run_flower(home: Path, args: list[str]) -> tuple[int, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
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
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("success"), bool):
        return payload["success"]
    return None


def register_one(home: Path, client: dict[str, str]) -> tuple[str, str]:
    client_id = client["id"]
    public_key = canonical_public_key(client)
    if not public_key.is_file():
        return "FAILED", f"public key not mounted: {public_key}"

    returncode, output = run_flower(
        home,
        [
            "supernode",
            "register",
            str(public_key),
            PROFILE,
            "--format",
            "json",
        ],
    )

    success = json_success(output)
    if returncode == 0 and success is not False:
        return "REGISTERED", output or "registration completed"

    return "FAILED", output or "Flower registration command failed"


def list_registered(home: Path) -> tuple[bool, str]:
    returncode, output = run_flower(
        home,
        [
            "supernode",
            "list",
            PROFILE,
            "--format",
            "json",
            "--verbose",
        ],
    )
    success = json_success(output)
    return returncode == 0 and success is not False, output or "Flower SuperNode list command returned no output"


def main() -> int:
    try:
        clients = parse_clients(CLIENTS_FILE)
        home = prepare_flower_home()
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
        status, detail = register_one(home, client)
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
    list_ok, listing = list_registered(home)
    print(listing, flush=True)
    if not list_ok:
        print("\nERROR: Flower SuperNode listing failed.", file=sys.stderr)
        return 1

    print("\nAll configured clients are registered and Flower registry listing succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
