"""Tests for TLS-aware Docker Compose generation."""

from pathlib import Path

import pytest

from scripts.generate_compose import build_compose
from src.deployment_config import DeploymentConfigError, DeploymentProfile


CLIENTS = [
    {"id": "client1", "data_dir": "./data/client1", "checkpoint_dir": "./checkpoints/client1"},
    {"id": "client2", "data_dir": "./data/client2", "checkpoint_dir": "./checkpoints/client2"},
]


def configure_production_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ca = tmp_path / "ca.crt"
    cert = tmp_path / "superlink.crt"
    key = tmp_path / "superlink.key"
    auth_host_dir = tmp_path / "auth-host"
    state_host_dir = tmp_path / "state" / "superlink"
    auth_host_dir.mkdir()
    state_host_dir.mkdir(parents=True)

    for path in (ca, cert, key):
        path.write_text("test", encoding="utf-8")
    for client in CLIENTS:
        (auth_host_dir / client["id"]).write_text("test", encoding="utf-8")

    monkeypatch.setenv("DEPLOYMENT_PROFILE", "production")
    monkeypatch.setenv("SUPERLINK_ADDRESS", "fl.example.internal:9092")
    monkeypatch.setenv("TLS_ROOT_CERTIFICATES", "/etc/flower/tls/ca.crt")
    monkeypatch.setenv("SUPERLINK_CERTIFICATE", "/etc/flower/tls/superlink.crt")
    monkeypatch.setenv("SUPERLINK_PRIVATE_KEY", "/etc/flower/tls/superlink.key")
    monkeypatch.setenv("TLS_CERTIFICATE_HOST_DIR", str(tmp_path))
    # This is the path INSIDE the container. The host-side source is tested
    # separately through SUPERNODE_AUTH_HOST_DIR below.
    monkeypatch.setenv("SUPERNODE_AUTH_PRIVATE_KEY_DIR", "/etc/flower/auth")
    monkeypatch.setenv("SUPERNODE_AUTH_HOST_DIR", str(auth_host_dir))
    # Persistent SuperLink registration state is required for production.
    monkeypatch.setenv("SUPERLINK_STATE_HOST_DIR", str(state_host_dir))
    monkeypatch.setenv("SUPERLINK_STATE_DIR", "/var/lib/flower")


def test_development_compose_keeps_insecure_transport() -> None:
    compose = build_compose(CLIENTS, profile=DeploymentProfile.DEVELOPMENT)

    assert "--insecure" in compose["services"]["superlink"]["command"]
    assert "--insecure" in compose["services"]["supernode-client1"]["command"]
    assert compose["services"]["trainer"]["command"][2] == "local-deployment"


def test_production_compose_requires_explicit_tls_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DEPLOYMENT_PROFILE",
        "SUPERLINK_ADDRESS",
        "TLS_ROOT_CERTIFICATES",
        "SUPERLINK_CERTIFICATE",
        "SUPERLINK_PRIVATE_KEY",
        "TLS_CERTIFICATE_HOST_DIR",
        "SUPERNODE_AUTH_PRIVATE_KEY_DIR",
        "SUPERNODE_AUTH_HOST_DIR",
        "SUPERLINK_STATE_HOST_DIR",
        "SUPERLINK_STATE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("DEPLOYMENT_PROFILE", "production")

    with pytest.raises(DeploymentConfigError, match="required environment variables"):
        build_compose(CLIENTS, profile=DeploymentProfile.PRODUCTION)


def test_production_compose_uses_tls_and_authentication(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_production_environment(monkeypatch, tmp_path)

    compose = build_compose(CLIENTS, profile=DeploymentProfile.PRODUCTION)

    superlink_command = compose["services"]["superlink"]["command"]
    node_command = compose["services"]["supernode-client1"]["command"]

    assert "--insecure" not in superlink_command
    assert "--ssl-ca-certfile" in superlink_command
    assert "--ssl-certfile" in superlink_command
    assert "--ssl-keyfile" in superlink_command
    assert "--enable-supernode-auth" in superlink_command
    assert "--database" in superlink_command
    assert superlink_command[superlink_command.index("--database") + 1] == "/var/lib/flower/superlink.db"
    assert "--insecure" not in node_command
    assert "--root-certificates" in node_command
    assert "--auth-supernode-private-key" in node_command
    assert node_command[node_command.index("--auth-supernode-private-key") + 1] == "/etc/flower/auth/client1"
    assert compose["services"]["superlink"]["volumes"] == [
        f"{tmp_path}/ca.crt:/etc/flower/tls/ca.crt:ro",
        f"{tmp_path}/superlink.crt:/etc/flower/tls/superlink.crt:ro",
        f"{tmp_path}/superlink.key:/etc/flower/tls/superlink.key:ro",
        f"{tmp_path}/state/superlink:/var/lib/flower:rw",
    ]
    assert compose["services"]["supernode-client1"]["volumes"] == [
        f"{tmp_path}/ca.crt:/etc/flower/tls/ca.crt:ro",
        f"{tmp_path}/auth-host/client1:/etc/flower/auth/client1:ro",
    ]
    assert compose["services"]["trainer"]["command"][2] == "production-deployment"


def test_production_supernodes_use_internal_superlink_address(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_production_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("SUPERLINK_ADDRESS", "public.example.internal:9092")

    compose = build_compose(CLIENTS, profile=DeploymentProfile.PRODUCTION)

    command = compose["services"]["supernode-client1"]["command"]
    assert command[command.index("--superlink") + 1] == "superlink:9092"
