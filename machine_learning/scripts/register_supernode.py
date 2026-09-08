#!/usr/bin/env python3
"""Register one Flower 1.33.0 SuperNode public key with a SuperLink.

This helper is intended to be called by setup.sh on a client host. It uses a
short-lived Docker container so the client host does not need Flower installed.
The client's private key never leaves the host.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

FLOWER_VERSION = "1.33.0"
FLOWER_IMAGE = "python:3.11-slim"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument("--superlink-address", required=True)
    parser.add_argument("--root-certificates", required=True, type=Path)
    parser.add_argument("--registration-profile", default="production-registration")
    parser.add_argument("--network", default=os.environ.get("FLOWER_REGISTRATION_DOCKER_NETWORK", "bridge"))
    return parser.parse_args()


def require_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise SystemExit(f"ERROR: {description} not found: {path}")


def build_config(address: str, ca_container_path: str, profile: str) -> str:
    return (
        "[superlink]\n"
        f"default = \"{profile}\"\n\n"
        f"[superlink.{profile}]\n"
        f"address = \"{address}\"\n"
        f"root-certificates = \"{ca_container_path}\"\n"
    )


def register(args: argparse.Namespace) -> int:
    require_file(args.public_key, "SuperNode public key")
    require_file(args.root_certificates, "SuperLink CA certificate")

    if not args.client_id.strip():
        raise SystemExit("ERROR: client ID must not be empty.")

    project_root = Path(__file__).resolve().parents[1]
    public_key = args.public_key.resolve()
    ca_file = args.root_certificates.resolve()

    with tempfile.TemporaryDirectory(prefix="flower-register-") as tmp:
        tmp_path = Path(tmp)
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            build_config(args.superlink_address, "/app/ca.crt", args.registration_profile),
            encoding="utf-8",
        )

        command = [
            "docker", "run", "--rm",
            "--network", args.network,
            "-v", f"{public_key}:/app/client.pub:ro",
            "-v", f"{ca_file}:/app/ca.crt:ro",
            "-v", f"{config_file}:/root/.flwr/config.toml:ro",
            FLOWER_IMAGE,
            "sh", "-c",
            (
                "set -eu; "
                "python -m pip install --no-cache-dir 'flwr==1.33.0' >/dev/null; "
                "flwr supernode register /app/client.pub "
                f"{args.registration_profile} --format json"
            ),
        ]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        print(f"Registering SuperNode {args.client_id} with {args.superlink_address}...")
        result = subprocess.run(command, cwd=project_root, text=True, capture_output=True, env=env)

    if result.returncode != 0:
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode

    output = result.stdout.strip()
    if output:
        try:
            parsed = json.loads(output)
            print(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            print(output)

    print(f"SuperNode {args.client_id} registered successfully.")
    return 0


def main() -> int:
    return register(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
