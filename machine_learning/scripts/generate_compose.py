#!/usr/bin/env python3
"""Generate an N-client Flower 1.33.0 Docker Compose deployment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import yaml

from src.deployment_config import DeploymentProfile, load_deployment_config, validate_no_insecure_flag

SUPERNODE_PORT = 9094
SUPERNODE_IMAGE = "flwr/supernode:1.33.0"
SUPEREXEC_IMAGE = "flwr_superexec:local"
TLS_CONTAINER_DIR = "/etc/flower/tls"
AUTH_CONTAINER_DIR = "/etc/flower/auth"


def validate_clients(clients: list[dict]) -> None:
    if len(clients) < 2:
        raise ValueError("At least 2 clients are required.")
    ids = [str(client.get("id", "")).strip() for client in clients]
    if any(not client_id for client_id in ids):
        raise ValueError("Every client must define a non-empty 'id'.")
    if len(ids) != len(set(ids)):
        raise ValueError("Client IDs must be unique.")
    for client in clients:
        for key in ("data_dir", "checkpoint_dir"):
            if not str(client.get(key, "")).strip():
                raise ValueError(f"Client '{client['id']}' must define '{key}'.")


def safe_id(client_id: str) -> str:
    return client_id.strip().lower().replace("_", "-").replace(" ", "-")


def node_name(client_id: str) -> str:
    return f"supernode-{safe_id(client_id)}"


def app_name(client_id: str) -> str:
    return f"superexec-clientapp-{safe_id(client_id)}"


def compose_host_path(path: Path) -> str:
    """Render a host path for Compose, preserving relative-path semantics."""
    value = str(path)
    if not path.is_absolute() and not value.startswith("./") and not value.startswith("../"):
        return f"./{value}"
    return value


def build_compose(clients: list[dict], *, profile: DeploymentProfile | str = DeploymentProfile.DEVELOPMENT) -> dict:
    validate_clients(clients)
    profile_value = profile.value if isinstance(profile, DeploymentProfile) else profile
    if profile_value == DeploymentProfile.PRODUCTION.value:
        config = load_deployment_config()
    else:
        config = load_deployment_config({
            "DEPLOYMENT_PROFILE": DeploymentProfile.DEVELOPMENT.value,
            "SUPERLINK_ADDRESS": os.environ.get("SUPERLINK_ADDRESS", "superlink:9092"),
        })

    superlink_command: list[str] = []
    supernode_prefix: list[str] = []
    if config.is_production:
        superlink_command.extend(config.superlink_tls_args())
        superlink_command.extend(config.superlink_auth_args())
        superlink_command.extend(config.superlink_state_args())
        supernode_prefix.extend(config.supernode_tls_args())
    else:
        superlink_command.append("--insecure")
        supernode_prefix.append("--insecure")

    validate_no_insecure_flag(config.profile, superlink_command)
    validate_no_insecure_flag(config.profile, supernode_prefix)

    host_tls_dir = os.environ.get("TLS_CERTIFICATE_HOST_DIR", "./certificates/prod")
    host_auth_dir = os.environ.get("SUPERNODE_AUTH_HOST_DIR", "./certificates/prod/auth")

    superlink_service = {
        "image": "flwr/superlink:1.33.0",
        "container_name": "flwr_superlink",
        "command": [*superlink_command, "--isolation", "process"],
        "ports": ["9091:9091", "9092:9092", "9093:9093"],
        "networks": ["flwr-network"],
    }
    if config.is_production:
        assert config.superlink_state_host_dir is not None
        state_host_dir = compose_host_path(config.superlink_state_host_dir)
        superlink_service["volumes"] = [
            f"{host_tls_dir}/ca.crt:{TLS_CONTAINER_DIR}/ca.crt:ro",
            f"{host_tls_dir}/superlink.crt:{TLS_CONTAINER_DIR}/superlink.crt:ro",
            f"{host_tls_dir}/superlink.key:{TLS_CONTAINER_DIR}/superlink.key:ro",
            f"{state_host_dir}:{config.superlink_state_dir}:rw",
        ]

    services = {"superlink": superlink_service}
    node_services: list[str] = []
    app_services: list[str] = []

    for client in clients:
        client_id = str(client["id"]).strip()
        node = node_name(client_id)
        app = app_name(client_id)
        node_services.append(node)
        app_services.append(app)

        node_command = [
            *supernode_prefix,
            "--superlink", "superlink:9092",
            "--clientappio-api-address", f"0.0.0.0:{SUPERNODE_PORT}",
            "--isolation", "process",
        ]
        if config.is_production:
            node_command.extend(config.supernode_auth_args(client_id))
        validate_no_insecure_flag(config.profile, node_command)

        services[node] = {
            "container_name": f"flwr_{node.replace('-', '_')}",
            "command": node_command,
            "networks": ["flwr-network"],
            "depends_on": ["superlink"],
        }
        if config.is_production:
            services[node]["volumes"] = [
                f"{host_tls_dir}/ca.crt:{TLS_CONTAINER_DIR}/ca.crt:ro",
                f"{host_auth_dir}/{client_id}:{AUTH_CONTAINER_DIR}/{client_id}:ro",
            ]

        services[app] = {
            "container_name": f"flwr_{app.replace('-', '_')}",
            "env_file": [".env"],
            "command": ["--insecure", "--plugin-type", "clientapp", "--appio-api-address", f"{node}:{SUPERNODE_PORT}"],
            "networks": ["flwr-network"],
            "volumes": [f"{client['data_dir']}:${{DATA_DIR}}", f"{client['checkpoint_dir']}:${{CHECKPOINT_DIR}}"],
            "environment": {"CLIENT_ID": client_id},
            "depends_on": [node, "superlink"],
        }

    services["superexec-serverapp"] = {
        "container_name": "flwr_superexec_serverapp",
        "env_file": [".env"],
        "command": ["--insecure", "--plugin-type", "serverapp", "--appio-api-address", "superlink:9091"],
        "networks": ["flwr-network"],
        "volumes": ["./checkpoints/global:${CHECKPOINT_DIR}", "./data/global:${DATA_DIR}"],
        "depends_on": ["superlink"],
    }

    federation_profile = "production-deployment" if config.is_production else "local-deployment"
    services["trainer"] = {
        "image": "flwr/superexec:1.33.0",
        "container_name": "flwr_trainer",
        "entrypoint": ["flwr"],
        "command": ["run", ".", federation_profile, "--stream"],
        "working_dir": "/app",
        "volumes": [".:/app"],
        "networks": ["flwr-network"],
        "depends_on": ["superlink", "superexec-serverapp", *node_services, *app_services],
    }

    services["test-runner"] = {
        "container_name": "flwr_test_runner",
        "entrypoint": ["pytest"],
        "command": ["tests/", "-v"],
        "working_dir": "/app",
        "environment": {"PYTHONPATH": "/app"},
        "volumes": [".:/app"],
        "networks": ["flwr-network"],
    }

    return {"networks": {"flwr-network": {"driver": "bridge"}}, "services": services, "volumes": {"data": {}, "checkpoints": {}}}


def render_compose(compose: dict) -> str:
    lines = [
        "networks:", "  flwr-network:", "    driver: bridge", "",
        "# Shared Flower SuperNode image", "x-flwr-supernode: &flwr_supernode", f"  image: {SUPERNODE_IMAGE}", "",
        "# Shared custom SuperExec image", "x-flwr-superexec: &flwr_superexec", f"  image: {SUPEREXEC_IMAGE}", "", "services:",
    ]
    for name, service in compose["services"].items():
        lines.append(f"  {name}:")
        if name.startswith("supernode-"):
            lines.append("    <<: *flwr_supernode")
        elif name.startswith("superexec-") or name == "test-runner":
            lines.append("    <<: *flwr_superexec")
        body = yaml.safe_dump(service, sort_keys=False, default_flow_style=False).rstrip()
        if body:
            lines.extend(f"    {line}" for line in body.splitlines())
        lines.append("")
    lines.extend(["volumes:", "  data: {}", "  checkpoints: {}", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an N-client Flower 1.33.0 Docker Compose deployment.")
    parser.add_argument("--config", default="clients.yml", help="Path to clients.yml")
    parser.add_argument("--output", default="docker-compose.generated.yml", help="Output Docker Compose file")
    parser.add_argument("--profile", choices=[profile.value for profile in DeploymentProfile], default=os.environ.get("DEPLOYMENT_PROFILE", DeploymentProfile.DEVELOPMENT.value), help="Deployment security profile")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if not config_path.exists():
        raise FileNotFoundError(f"Client configuration not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    clients = config.get("clients", [])
    compose = build_compose(clients, profile=args.profile)
    output_path.write_text(render_compose(compose), encoding="utf-8")
    print(f"Generated {output_path} for {len(clients)} clients ({args.profile} profile).")


if __name__ == "__main__":
    main()
