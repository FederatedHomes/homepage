"""Deployment profile and production security configuration validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class DeploymentConfigError(ValueError):
    """Raised when deployment configuration is invalid or unsafe."""


class DeploymentProfile(str, Enum):
    """Supported deployment profiles."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


@dataclass(frozen=True)
class DeploymentConfig:
    """Resolved deployment configuration."""

    profile: DeploymentProfile
    superlink_address: str
    tls_root_certificates: Path | None = None
    superlink_certificate: Path | None = None
    superlink_private_key: Path | None = None
    tls_certificate_host_dir: Path | None = None
    supernode_auth_private_key_dir: Path | None = None
    supernode_auth_host_dir: Path | None = None
    superlink_state_host_dir: Path | None = None
    superlink_state_dir: Path | None = None

    @property
    def is_production(self) -> bool:
        return self.profile is DeploymentProfile.PRODUCTION

    @property
    def supernode_auth_enabled(self) -> bool:
        return self.is_production

    def superlink_tls_args(self) -> list[str]:
        if not self.is_production:
            return []
        assert self.tls_root_certificates is not None
        assert self.superlink_certificate is not None
        assert self.superlink_private_key is not None
        return [
            "--ssl-ca-certfile", str(self.tls_root_certificates),
            "--ssl-certfile", str(self.superlink_certificate),
            "--ssl-keyfile", str(self.superlink_private_key),
        ]

    def superlink_auth_args(self) -> list[str]:
        if not self.supernode_auth_enabled:
            return []
        return ["--enable-supernode-auth"]

    def superlink_state_args(self) -> list[str]:
        if not self.is_production:
            return []
        assert self.superlink_state_dir is not None
        return ["--database", str(self.superlink_state_dir / "superlink.db")]

    def supernode_tls_args(self) -> list[str]:
        if not self.is_production:
            return []
        assert self.tls_root_certificates is not None
        return ["--root-certificates", str(self.tls_root_certificates)]

    def supernode_auth_args(self, client_id: str) -> list[str]:
        if not self.supernode_auth_enabled:
            return []
        client_id = client_id.strip() if client_id else ""
        if not client_id or not _SAFE_CLIENT_ID.fullmatch(client_id):
            raise DeploymentConfigError(
                "SuperNode authentication requires a client ID containing only "
                "letters, numbers, '.', '_' or '-'."
            )
        assert self.supernode_auth_private_key_dir is not None
        key_path = self.supernode_auth_private_key_dir / client_id
        return ["--auth-supernode-private-key", str(key_path)]

    def supernode_auth_host_key(self, client_id: str) -> Path:
        """Return the host-side path for one SuperNode's private key."""
        if not self.supernode_auth_enabled:
            raise DeploymentConfigError("SuperNode authentication is disabled.")
        client_id = client_id.strip() if client_id else ""
        if not client_id or not _SAFE_CLIENT_ID.fullmatch(client_id):
            raise DeploymentConfigError("Invalid SuperNode client ID.")
        assert self.supernode_auth_host_dir is not None
        return self.supernode_auth_host_dir / client_id

    def cli_tls_config(self) -> dict[str, str | bool]:
        if self.is_production:
            assert self.tls_root_certificates is not None
            return {"address": self.superlink_address, "root-certificates": str(self.tls_root_certificates)}
        return {"address": self.superlink_address, "insecure": True}


PROFILE_ENV = "DEPLOYMENT_PROFILE"
SUPERLINK_ADDRESS_ENV = "SUPERLINK_ADDRESS"
TLS_ROOT_CERTIFICATES_ENV = "TLS_ROOT_CERTIFICATES"
SUPERLINK_CERTIFICATE_ENV = "SUPERLINK_CERTIFICATE"
SUPERLINK_PRIVATE_KEY_ENV = "SUPERLINK_PRIVATE_KEY"
TLS_CERTIFICATE_HOST_DIR_ENV = "TLS_CERTIFICATE_HOST_DIR"
SUPERNODE_AUTH_PRIVATE_KEY_DIR_ENV = "SUPERNODE_AUTH_PRIVATE_KEY_DIR"
SUPERNODE_AUTH_HOST_DIR_ENV = "SUPERNODE_AUTH_HOST_DIR"
SUPERLINK_STATE_HOST_DIR_ENV = "SUPERLINK_STATE_HOST_DIR"
SUPERLINK_STATE_DIR_ENV = "SUPERLINK_STATE_DIR"
DEPLOYMENT_ROLE_ENV = "DEPLOYMENT_ROLE"

SERVER_REQUIRED_ENV = (
    SUPERLINK_ADDRESS_ENV,
    TLS_ROOT_CERTIFICATES_ENV,
    SUPERLINK_CERTIFICATE_ENV,
    SUPERLINK_PRIVATE_KEY_ENV,
    TLS_CERTIFICATE_HOST_DIR_ENV,
    SUPERNODE_AUTH_PRIVATE_KEY_DIR_ENV,
    SUPERNODE_AUTH_HOST_DIR_ENV,
    SUPERLINK_STATE_HOST_DIR_ENV,
    SUPERLINK_STATE_DIR_ENV,
)

CLIENT_REQUIRED_ENV = (
    SUPERLINK_ADDRESS_ENV,
    TLS_ROOT_CERTIFICATES_ENV,
    TLS_CERTIFICATE_HOST_DIR_ENV,
    SUPERNODE_AUTH_PRIVATE_KEY_DIR_ENV,
    SUPERNODE_AUTH_HOST_DIR_ENV,
)

_SAFE_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]+")


def _profile_from_value(value: str | None) -> DeploymentProfile:
    normalized = (value or DeploymentProfile.DEVELOPMENT.value).strip().lower()
    try:
        return DeploymentProfile(normalized)
    except ValueError as exc:
        allowed = ", ".join(profile.value for profile in DeploymentProfile)
        raise DeploymentConfigError(f"{PROFILE_ENV} must be one of: {allowed}; got '{normalized}'.") from exc


def validate_no_insecure_flag(profile: DeploymentProfile | str, command: Sequence[str]) -> None:
    resolved_profile = profile if isinstance(profile, DeploymentProfile) else _profile_from_value(profile)
    if resolved_profile is DeploymentProfile.PRODUCTION and "--insecure" in command:
        raise DeploymentConfigError(
            "Production deployment must not use Flower's --insecure flag. Configure TLS before starting the production federation."
        )


def load_deployment_config(
    environ: Mapping[str, str] | None = None,
    *,
    require_files: bool = False,
    role: str | None = None,
) -> DeploymentConfig:
    env = os.environ if environ is None else environ
    resolved_role = (role or env.get(DEPLOYMENT_ROLE_ENV, "server")).strip().lower()
    if resolved_role not in {"server", "client", "all"}:
        raise DeploymentConfigError("Deployment role must be one of: server, client, all.")

    profile = _profile_from_value(env.get(PROFILE_ENV))
    if profile is DeploymentProfile.DEVELOPMENT:
        superlink_address = env.get(SUPERLINK_ADDRESS_ENV, "").strip() or "superlink:9092"
        return DeploymentConfig(profile=profile, superlink_address=superlink_address)

    required_env = CLIENT_REQUIRED_ENV if resolved_role == "client" else SERVER_REQUIRED_ENV
    missing = [name for name in required_env if not env.get(name, "").strip()]
    if missing:
        raise DeploymentConfigError("Production deployment is missing required environment variables: " + ", ".join(missing))

    paths = {
        "tls_root_certificates": Path(env[TLS_ROOT_CERTIFICATES_ENV]),
        "superlink_certificate": Path(env[SUPERLINK_CERTIFICATE_ENV]) if env.get(SUPERLINK_CERTIFICATE_ENV) else None,
        "superlink_private_key": Path(env[SUPERLINK_PRIVATE_KEY_ENV]) if env.get(SUPERLINK_PRIVATE_KEY_ENV) else None,
        "tls_certificate_host_dir": Path(env[TLS_CERTIFICATE_HOST_DIR_ENV]),
        "supernode_auth_private_key_dir": Path(env[SUPERNODE_AUTH_PRIVATE_KEY_DIR_ENV]),
        "supernode_auth_host_dir": Path(env[SUPERNODE_AUTH_HOST_DIR_ENV]),
        "superlink_state_host_dir": Path(env[SUPERLINK_STATE_HOST_DIR_ENV]) if env.get(SUPERLINK_STATE_HOST_DIR_ENV) else None,
        "superlink_state_dir": Path(env[SUPERLINK_STATE_DIR_ENV]) if env.get(SUPERLINK_STATE_DIR_ENV) else None,
    }
    if require_files:
        missing_files = []
        host_tls_dir = paths["tls_certificate_host_dir"]
        for name in ("ca.crt", "superlink.crt", "superlink.key"):
            if resolved_role == "client" and name != "ca.crt":
                continue
            if not (host_tls_dir / name).is_file():
                missing_files.append(f"tls_certificate_host_dir/{name}={host_tls_dir / name}")
        auth_host_dir = paths["supernode_auth_host_dir"]
        if not auth_host_dir.is_dir():
            missing_files.append(f"supernode_auth_host_dir={auth_host_dir}")
        if resolved_role != "client":
            state_host_dir = paths["superlink_state_host_dir"]
            assert state_host_dir is not None
            if not state_host_dir.is_dir():
                missing_files.append(f"superlink_state_host_dir={state_host_dir}")
        if missing_files:
            raise DeploymentConfigError("Production security files/directories were not found: " + ", ".join(missing_files))

    return DeploymentConfig(profile=profile, superlink_address=env[SUPERLINK_ADDRESS_ENV].strip(), **paths)


def validate_environment(
    environ: Mapping[str, str] | None = None,
    *,
    require_files: bool = False,
    role: str | None = None,
) -> DeploymentConfig:
    return load_deployment_config(environ, require_files=require_files, role=role)
