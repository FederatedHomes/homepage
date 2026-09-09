#!/usr/bin/env python3
"""Generate Flower 1.33.0 Docker Compose deployments for server or client hosts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.deployment_config import DeploymentProfile, load_deployment_config, validate_no_insecure_flag

SUPERNODE_PORT = 9094
SUPERNODE_IMAGE = "flwr/supernode:1.33.0"
REGISTRATION_IMAGE = "flwr_client_registration:local"
REGISTRATION_BUILD = {"context": ".", "dockerfile": "Dockerfile.client-registration"}
SUPEREXEC_IMAGE = "flwr_superexec:local"
SUPEREXEC_BUILD = {"context": ".", "dockerfile": "Dockerfile.superexec"}
TLS_CONTAINER_DIR = "/etc/flower/tls"
AUTH_CONTAINER_DIR = "/etc/flower/auth"
DEPLOYMENT_ROLE_ENV = "DEPLOYMENT_ROLE"
CLIENT_ID_ENV = "CLIENT_ID"


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
        if not str(client.get("public_key", "")).strip():
            raise ValueError(f"Client '{client['id']}' must define 'public_key'.")


def safe_id(client_id: str) -> str:
    return client_id.strip().lower().replace("_", "-").replace(" ", "-")


def node_name(client_id: str) -> str:
    return f"supernode-{safe_id(client_id)}"


def app_name(client_id: str) -> str:
    return f"superexec-clientapp-{safe_id(client_id)}"


def compose_host_path(path: Path) -> str:
    value = str(path)
    if not path.is_absolute() and not value.startswith("./") and not value.startswith("../"):
        return f"./{value}"
    return value


def build_compose(
    clients: list[dict],
    *,
    profile: DeploymentProfile | str = DeploymentProfile.DEVELOPMENT,
    role: str = "all",
    client_id: str | None = None,
) -> dict:
    validate_clients(clients)
    if role not in {"all", "server", "client"}:
        raise ValueError("Deployment role must be one of: all, server, client.")

    profile_value = profile.value if isinstance(profile, DeploymentProfile) else profile
    if profile_value == DeploymentProfile.PRODUCTION.value:
        config = load_deployment_config(role=role)
    else:
        config = load_deployment_config({
            "DEPLOYMENT_PROFILE": DeploymentProfile.DEVELOPMENT.value,
            "SUPERLINK_ADDRESS": os.environ.get("SUPERLINK_ADDRESS", "superlink:9092"),
        }, role=role)

    selected_clients = clients
    if role == "client":
        resolved_client_id = (client_id or os.environ.get(CLIENT_ID_ENV, "")).strip()
        if not resolved_client_id:
            raise ValueError("Client deployment requires --client-id or CLIENT_ID.")
        selected_clients = [client for client in clients if str(client["id"]).strip() == resolved_client_id]
        if not selected_clients:
            raise ValueError(f"Client ID '{resolved_client_id}' is not defined in clients.yml.")

    superlink_command: list[str] = []
    supernode_prefix: list[str] = []
    if config.is_production:
        if role in {"all", "server"}:
            superlink_command.extend(config.superlink_tls_args())
            superlink_command.extend(config.superlink_auth_args())
            superlink_command.extend(config.superlink_state_args())
        if role in {"all", "client"}:
            supernode_prefix.extend(config.supernode_tls_args())
    else:
        if role in {"all", "server"}:
            superlink_command.append("--insecure")
        if role in {"all", "client"}:
            supernode_prefix.append("--insecure")

    validate_no_insecure_flag(config.profile, superlink_command)
    validate_no_insecure_flag(config.profile, supernode_prefix)

    host_tls_dir = os.environ.get("TLS_CERTIFICATE_HOST_DIR", "./certificates/prod")
    host_auth_dir = os.environ.get("SUPERNODE_AUTH_HOST_DIR", "./certificates/prod/auth")

    services: dict[str, dict] = {}

    if role in {"all", "server"}:
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
        services["superlink"] = superlink_service

        if config.is_production:
            registration_volumes = [
                "./.flwr:/app/.flwr:ro",
                "./clients.yml:/app/clients.yml:ro",
                f"{host_tls_dir}/ca.crt:/app/certificates/prod/tls/ca.crt:ro",
                f"{host_auth_dir}:/app/certificates/prod/auth:ro",
            ]
            registration_environment = {
                "SUPERLINK_CONTROL_ADDRESS": config.superlink_control_address,
            }
            services["client-registration"] = {
                "image": REGISTRATION_IMAGE,
                "build": dict(REGISTRATION_BUILD),
                "container_name": "flwr_client_registration",
                "working_dir": "/app",
                "networks": ["flwr-network"],
                "volumes": registration_volumes,
                "environment": registration_environment,
                "depends_on": ["superlink"],
            }

    if role in {"all", "client"}:
        for client in selected_clients:
            current_client_id = str(client["id"]).strip()
            node = node_name(current_client_id)
            app = app_name(current_client_id)
            superlink_address = config.superlink_address
            node_command = [
                *supernode_prefix,
                "--superlink", superlink_address,
                "--clientappio-api-address", f"0.0.0.0:{SUPERNODE_PORT}",
                "--isolation", "process",
            ]
            if config.is_production:
                node_command.extend(config.supernode_auth_args(current_client_id))
            validate_no_insecure_flag(config.profile, node_command)
            services[node] = {
                "image": SUPERNODE_IMAGE,
                "container_name": f"flwr_{node.replace('-', '_')}",
                "init": True,
                "command": node_command,
                "networks": ["flwr-network"],
                "depends_on": [] if role == "client" else ["superlink"],
            }
            if config.is_production:
                services[node]["volumes"] = [
                    f"{host_tls_dir}/ca.crt:{TLS_CONTAINER_DIR}/ca.crt:ro",
                    f"{host_auth_dir}:{AUTH_CONTAINER_DIR}:ro",
                ]
            services[app] = {
                "image": SUPEREXEC_IMAGE,
                "build": dict(SUPEREXEC_BUILD),
                "container_name": f"flwr_{app.replace('-', '_')}",
                "env_file": [".env"],
                "command": ["--insecure", "--plugin-type", "clientapp", "--appio-api-address", f"{node}:{SUPERNODE_PORT}"],
                "networks": ["flwr-network"],
                "volumes": [f"{client['data_dir']}:/app/data:ro", f"{client['checkpoint_dir']}:/app/checkpoints:rw"],
                "environment": {CLIENT_ID_ENV: current_client_id},
                "depends_on": [node],
            }

    if role in {"all", "server"}:
        services["superexec-serverapp"] = {
            "image": SUPEREXEC_IMAGE,
            "build": dict(SUPEREXEC_BUILD),
            "container_name": "flwr_superexec_serverapp",
            "env_file": [".env"],
            "command": ["--insecure", "--plugin-type", "serverapp", "--appio-api-address", "superlink:9091"],
            "networks": ["flwr-network"],
            "volumes": ["./checkpoints/global:/app/checkpoints:rw", "./data/global:/app/data:rw"],
            "depends_on": ["superlink"],
        }

    if role in {"all", "server"}:
        federation_profile = "production-deployment" if config.is_production else "local-deployment"
        services["trainer"] = {
            "image": "flwr/superexec:1.33.0",
            "container_name": "flwr_trainer",
            "entrypoint": ["flwr"],
            "command": ["run", ".", federation_profile, "--stream"],
            "working_dir": "/app",
            "volumes": [".:/app"],
            "networks": ["flwr-network"],
            "depends_on": ["superlink", "superexec-serverapp"],
        }
        if role == "all":
            services["test-runner"] = {
                "image": SUPEREXEC_IMAGE,
                "build": dict(SUPEREXEC_BUILD),
                "container_name": "flwr_test_runner",
                "entrypoint": ["pytest"],
                "command": ["tests/", "-v"],
                "working_dir": "/app",
                "environment": {"PYTHONPATH": "/app"},
                "volumes": [".:/app"],
                "networks": ["flwr-network"],
            }

    return {"networks": {"flwr-network": {"driver": "bridge"}}, "services": services}


def render_compose(compose: dict) -> str:
    return yaml.safe_dump(compose, sort_keys=False, default_flow_style=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Flower 1.33.0 Docker Compose deployment.")
    parser.add_argument("--config", default="clients.yml", help="Path to clients.yml")
    parser.add_argument("--output", default="docker-compose.generated.yml", help="Output Docker Compose file")
    parser.add_argument("--profile", choices=[profile.value for profile in DeploymentProfile], default=os.environ.get("DEPLOYMENT_PROFILE", DeploymentProfile.DEVELOPMENT.value), help="Deployment security profile")
    parser.add_argument("--role", choices=["all", "server", "client"], default=os.environ.get(DEPLOYMENT_ROLE_ENV, "all"), help="Deployment host role")
    parser.add_argument("--client-id", default=os.environ.get(CLIENT_ID_ENV), help="Client ID for a client deployment")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if not config_path.exists():
        raise FileNotFoundError(f"Client configuration not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    clients = config.get("clients", [])
    compose = build_compose(clients, profile=args.profile, role=args.role, client_id=args.client_id)
    output_path.write_text(render_compose(compose), encoding="utf-8")
    print(f"Generated {output_path} for {len(compose['services'])} services ({args.profile}, {args.role} role).")


if __name__ == "__main__":
    main()
