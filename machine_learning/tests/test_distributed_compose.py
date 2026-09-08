"""Tests for multi-host Flower Compose role separation."""

from pathlib import Path

import pytest

from scripts.generate_compose import build_compose
from src.deployment_config import DeploymentConfigError, DeploymentProfile


CLIENTS = [
    {"id": "client-1", "data_dir": "./data/client-1", "checkpoint_dir": "./checkpoints/client-1"},
    {"id": "client-2", "data_dir": "./data/client-2", "checkpoint_dir": "./checkpoints/client-2"},
]


def production_client_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tls_dir = tmp_path / "tls"
    auth_dir = tmp_path / "auth"
    tls_dir.mkdir()
    auth_dir.mkdir()
    (tls_dir / "ca.crt").write_text("test", encoding="utf-8")
    for client in CLIENTS:
        (auth_dir / client["id"]).write_text("test", encoding="utf-8")

    monkeypatch.setenv("DEPLOYMENT_PROFILE", "production")
    monkeypatch.setenv("SUPERLINK_ADDRESS", "192.168.1.100:9092")
    monkeypatch.setenv("TLS_ROOT_CERTIFICATES", "/etc/flower/tls/ca.crt")
    monkeypatch.setenv("TLS_CERTIFICATE_HOST_DIR", str(tls_dir))
    monkeypatch.setenv("SUPERNODE_AUTH_PRIVATE_KEY_DIR", "/etc/flower/auth")
    monkeypatch.setenv("SUPERNODE_AUTH_HOST_DIR", str(auth_dir))


def production_server_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    production_client_env(monkeypatch, tmp_path)
    (tmp_path / "tls" / "superlink.crt").write_text("test", encoding="utf-8")
    (tmp_path / "tls" / "superlink.key").write_text("test", encoding="utf-8")
    state_dir = tmp_path / "state" / "superlink"
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("SUPERLINK_CERTIFICATE", "/etc/flower/tls/superlink.crt")
    monkeypatch.setenv("SUPERLINK_PRIVATE_KEY", "/etc/flower/tls/superlink.key")
    monkeypatch.setenv("SUPERLINK_STATE_HOST_DIR", str(state_dir))
    monkeypatch.setenv("SUPERLINK_STATE_DIR", "/var/lib/flower")


def test_server_role_contains_only_server_side_services(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    production_server_env(monkeypatch, tmp_path)
    compose = build_compose(CLIENTS, profile=DeploymentProfile.PRODUCTION, role="server")
    services = compose["services"]

    assert set(services) == {"superlink", "superexec-serverapp"}
    assert "trainer" not in services
    assert "test-runner" not in services
    assert not any(name.startswith("supernode-") for name in services)
    assert not any(name.startswith("superexec-clientapp-") for name in services)


def test_client_role_requires_client_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    production_client_env(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Client deployment requires"):
        build_compose(CLIENTS, profile=DeploymentProfile.PRODUCTION, role="client")


def test_client_role_contains_only_selected_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    production_client_env(monkeypatch, tmp_path)
    compose = build_compose(
        CLIENTS,
        profile=DeploymentProfile.PRODUCTION,
        role="client",
        client_id="client-2",
    )
    services = compose["services"]

    assert set(services) == {"supernode-client-2", "superexec-clientapp-client-2"}
    assert "superlink" not in services
    assert "trainer" not in services
    node_command = services["supernode-client-2"]["command"]
    assert node_command[node_command.index("--superlink") + 1] == "192.168.1.100:9092"
    assert "--insecure" not in node_command


def test_client_role_does_not_require_server_certificate_or_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    production_client_env(monkeypatch, tmp_path)
    compose = build_compose(
        CLIENTS,
        profile=DeploymentProfile.PRODUCTION,
        role="client",
        client_id="client-1",
    )
    assert "supernode-client-1" in compose["services"]
