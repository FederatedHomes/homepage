#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

load_environment() {
  if [ ! -f .env ]; then
    if [ -f .env.example ]; then
      cp .env.example .env
      echo "Created .env from .env.example"
    else
      echo "ERROR: .env.example not found. Create .env before continuing."
      return 1
    fi
  fi

  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
}

read_clients() {
  if [ ! -f clients.yml ]; then
    echo "ERROR: clients.yml not found."
    return 1
  fi
}

select_host_role() {
  local configured_role="${DEPLOYMENT_ROLE:-}"
  if [ "$configured_role" = "server" ] || [ "$configured_role" = "client" ]; then
    printf '%s\n' "$configured_role"
    return 0
  fi

  echo >&2
  echo "What type of host are you preparing?" >&2
  echo "  1) Server host — runs Flower SuperLink and ServerApp" >&2
  echo "  2) Client host — runs one SuperNode and one ClientApp" >&2
  read -rp "Enter choice [1-2]: " role_choice

  case "$role_choice" in
    1) printf '%s\n' "server" ;;
    2) printf '%s\n' "client" ;;
    *)
      echo "ERROR: Invalid host role selection." >&2
      return 1
      ;;
  esac
}

select_client_id() {
  local selected="${CLIENT_ID:-}"
  if [ -n "$selected" ]; then
    printf '%s\n' "$selected"
    return 0
  fi

  mapfile -t client_ids < <(python3 - <<'PY'
from pathlib import Path
import yaml
with Path("clients.yml").open("r", encoding="utf-8") as handle:
    clients = (yaml.safe_load(handle) or {}).get("clients", [])
for client in clients:
    client_id = str(client.get("id", "")).strip()
    if client_id:
        print(client_id)
PY
)

  if ((${#client_ids[@]} == 0)); then
    echo "ERROR: No clients are configured in clients.yml." >&2
    return 1
  fi

  echo >&2
  echo "Select the client assigned to this machine:" >&2
  local index=1
  for client_id in "${client_ids[@]}"; do
    echo "  $index) $client_id" >&2
    index=$((index + 1))
  done
  read -rp "Enter client number: " selection

  if ! [[ "$selection" =~ ^[0-9]+$ ]] || [ "$selection" -lt 1 ] || [ "$selection" -gt "${#client_ids[@]}" ]; then
    echo "ERROR: Invalid client selection." >&2
    return 1
  fi
  printf '%s\n' "${client_ids[$((selection - 1))]}"
}

create_directories() {
  local role="$1"

  if [ "$role" = "server" ]; then
    if [ "${DEPLOYMENT_PROFILE:-development}" = "production" ] && [ -n "${SUPERLINK_STATE_HOST_DIR:-}" ]; then
      mkdir -p "$SUPERLINK_STATE_HOST_DIR"
      echo "Prepared SuperLink state directory: $SUPERLINK_STATE_HOST_DIR"
    fi
    return 0
  fi

  if [ "$role" = "client" ]; then
    local client_id="${2:-}"
    read_clients
    python3 - "$client_id" <<'PY'
from pathlib import Path
import sys
import yaml

requested_client = sys.argv[1].strip()
with Path("clients.yml").open("r", encoding="utf-8") as handle:
    clients = (yaml.safe_load(handle) or {}).get("clients", [])

if len(clients) < 2:
    raise SystemExit("ERROR: clients.yml must define at least 2 clients.")

selected = [c for c in clients if str(c.get("id", "")).strip() == requested_client]
if not selected:
    raise SystemExit(f"ERROR: Client ID '{requested_client}' is not defined in clients.yml.")

client = selected[0]
client_id = str(client.get("id", "")).strip()
data_dir = str(client.get("data_dir", "")).strip()
checkpoint_dir = str(client.get("checkpoint_dir", "")).strip()
if not data_dir:
    raise SystemExit(f"ERROR: Client '{client_id}' is missing 'data_dir'.")
if not checkpoint_dir:
    raise SystemExit(f"ERROR: Client '{client_id}' is missing 'checkpoint_dir'.")
Path(data_dir).mkdir(parents=True, exist_ok=True)
Path(checkpoint_dir).mkdir(parents=True, exist_ok=True)
print(f"Prepared {client_id}: data={data_dir}, checkpoints={checkpoint_dir}")
PY
  fi
}

prepare_development_auth() {
  if [ "${DEPLOYMENT_PROFILE:-development}" != "development" ]; then
    return
  fi
  if [ ! -f clients.yml ] || [ ! -f scripts/generate_supernode_auth.py ]; then
    return
  fi
  local auth_dir="${DEV_SUPERNODE_AUTH_DIR:-certificates/dev/auth}"
  mapfile -t client_ids < <(python3 - <<'PY'
from pathlib import Path
import yaml
with Path("clients.yml").open("r", encoding="utf-8") as handle:
    config = yaml.safe_load(handle) or {}
for client in config.get("clients", []):
    print(str(client.get("id", "")).strip())
PY
)
  if ((${#client_ids[@]} == 0)); then
    return
  fi
  local missing=0
  for client_id in "${client_ids[@]}"; do
    if [ ! -f "$auth_dir/$client_id" ] || [ ! -f "$auth_dir/$client_id.pub" ]; then
      missing=1
      break
    fi
  done
  if [ "$missing" -eq 1 ]; then
    echo "Generating development-only SuperNode identities in $auth_dir"
    python3 scripts/generate_supernode_auth.py --output-dir "$auth_dir" "${client_ids[@]}"
  else
    echo "Development SuperNode identities already exist in $auth_dir"
  fi
}

validate_auth_environment() {
  local role="$1"
  local client_id="${2:-}"

  if [ "${DEPLOYMENT_PROFILE:-development}" != "production" ]; then
    return
  fi

  echo "Validating production TLS, SuperNode authentication, and deployment state..."
  python3 - "$role" "$client_id" <<'PY'
from pathlib import Path
import sys
import yaml
from src.deployment_config import load_deployment_config

role = sys.argv[1]
client_id = sys.argv[2].strip()
config = load_deployment_config(role=role, require_files=True)
print(f"Validated production SuperLink endpoint: {config.superlink_address}")
print(f"Validated TLS material: {config.tls_certificate_host_dir}")

with Path("clients.yml").open("r", encoding="utf-8") as handle:
    clients = (yaml.safe_load(handle) or {}).get("clients", [])

if role == "client":
    if not client_id:
        raise SystemExit("ERROR: CLIENT_ID must be set for a client deployment.")
    clients = [c for c in clients if str(c.get("id", "")).strip() == client_id]
    if not clients:
        raise SystemExit(f"ERROR: Client ID '{client_id}' is not defined in clients.yml.")
else:
    if config.superlink_state_host_dir is None:
        raise SystemExit("ERROR: Server deployment requires SuperLink persistent state configuration.")
    print(f"Validated SuperLink state directory: {config.superlink_state_host_dir}")

missing = []
for client in clients:
    current_id = str(client.get("id", "")).strip()
    if not config.supernode_auth_host_key(current_id).is_file():
        missing.append(current_id)
if missing:
    raise SystemExit("Missing SuperNode authentication keys for: " + ", ".join(missing))
print(f"Validated {len(clients)} SuperNode authentication key(s).")
PY
}

prepare_host() {
  load_environment
  read_clients

  local role
  role="$(select_host_role)"
  export DEPLOYMENT_ROLE="$role"

  local client_id=""
  if [ "$role" = "client" ]; then
    client_id="$(select_client_id)"
    export CLIENT_ID="$client_id"
  fi

  create_directories "$role" "$client_id"
  prepare_development_auth
  validate_auth_environment "$role" "$client_id"

  echo
  if [ "$role" = "server" ]; then
    echo "Server host preparation completed."
    echo "No client data or checkpoint directories were created."
  else
    echo "Client host preparation completed for $client_id."
  fi
}

generate_server_compose() {
  read_clients
  load_environment
  export DEPLOYMENT_ROLE=server
  create_directories server
  prepare_development_auth
  validate_auth_environment server

  local output="${SERVER_COMPOSE_FILE:-docker-compose.server.yml}"
  echo "Generating server Compose configuration..."
  python3 scripts/generate_compose.py \
    --config clients.yml \
    --output "$output" \
    --profile "${DEPLOYMENT_PROFILE:-development}" \
    --role server
  echo "Generated $output"
  echo "Server services: SuperLink, ServerApp, and trainer."
}

generate_client_compose() {
  read_clients
  load_environment
  export DEPLOYMENT_ROLE=client

  local client_id
  client_id="$(select_client_id)"
  export CLIENT_ID="$client_id"

  create_directories client "$client_id"
  prepare_development_auth
  validate_auth_environment client "$client_id"

  local output="${CLIENT_COMPOSE_FILE:-docker-compose.client-${client_id}.yml}"
  echo "Generating Compose configuration for $client_id..."
  python3 scripts/generate_compose.py \
    --config clients.yml \
    --output "$output" \
    --profile "${DEPLOYMENT_PROFILE:-development}" \
    --role client \
    --client-id "$client_id"
  echo "Generated $output"
  echo "Client services: SuperNode and ClientApp for $client_id."
}

run_local_development_compose() {
  read_clients
  load_environment
  echo "Generating local all-in-one DEVELOPMENT Compose configuration..."
  python3 scripts/generate_compose.py \
    --config clients.yml \
    --output docker-compose.generated.yml \
    --profile development \
    --role all
  echo "Generated docker-compose.generated.yml"
  echo "This file is for local development/integration testing only."
}

run_tests() {
  echo "=========================================="
  echo "Running application tests in Docker"
  echo "=========================================="
  read_clients
  if ! find tests -maxdepth 1 -name 'test_*.py' -print -quit | grep -q .; then
    echo "ERROR: No pytest test files found in tests/."
    return 1
  fi

  docker build -f Dockerfile.superexec -t flwr_superexec:local .
  python3 scripts/generate_compose.py \
    --config clients.yml \
    --output docker-compose.test.yml \
    --profile development \
    --role all
  docker compose -f docker-compose.test.yml run --rm test-runner
  rm -f docker-compose.test.yml
}

start_server_training() {
  load_environment
  export DEPLOYMENT_ROLE=server
  local compose_file="${SERVER_COMPOSE_FILE:-docker-compose.server.yml}"
  if [ ! -f "$compose_file" ]; then
    echo "Server Compose file not found; generating it now."
    generate_server_compose
  fi

  docker build -f Dockerfile.superexec -t flwr_superexec:local .
  echo "Starting federated training on the server host..."
  if docker compose -f "$compose_file" up trainer; then
    echo "Federated training completed successfully."
    docker compose -f "$compose_file" down
  else
    echo "Federated training failed. Leaving the server stack running for inspection."
    return 1
  fi
}

print_config() {
  echo
  echo "Deployment profile: ${DEPLOYMENT_PROFILE:-development}"
  echo "Deployment role:    ${DEPLOYMENT_ROLE:-not set}"
  echo "SuperLink address:  ${SUPERLINK_ADDRESS:-not set}"
  echo
  echo "Configured clients:"
  read_clients || return 1
  python3 - <<'PY'
from pathlib import Path
import yaml
with Path("clients.yml").open("r", encoding="utf-8") as handle:
    clients = (yaml.safe_load(handle) or {}).get("clients", [])
for client in clients:
    print(f"  {str(client.get('id', '')).strip()}")
    print(f"    data:        {str(client.get('data_dir', '')).strip()}")
    print(f"    checkpoints: {str(client.get('checkpoint_dir', '')).strip()}")
PY
  echo
}

print_menu() {
  cat <<'EOF'

Federated Learning Setup

Select an option:
  1) Prepare this host — choose SERVER or CLIENT role
  2) Generate SERVER Compose file
  3) Generate CLIENT Compose file for one configured client
  4) Start federated training on the SERVER host
  5) Run application tests in Docker
  6) Show deployment and client configuration
  7) Generate local all-in-one DEVELOPMENT Compose file
  8) Exit
EOF
}

print_menu
read -rp "Enter choice [1-8]: " choice
case "$choice" in
  1) prepare_host ;;
  2) generate_server_compose ;;
  3) generate_client_compose ;;
  4) start_server_training ;;
  5) run_tests ;;
  6) load_environment && print_config ;;
  7) run_local_development_compose ;;
  8) echo "Exiting."; exit 0 ;;
  *) echo "Invalid choice. Exiting."; exit 1 ;;
esac
