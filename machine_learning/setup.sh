#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

create_directories() {
  mkdir -p data/global checkpoints/global

  if [ "${DEPLOYMENT_PROFILE:-development}" = "production" ] && [ -n "${SUPERLINK_STATE_HOST_DIR:-}" ]; then
    mkdir -p "$SUPERLINK_STATE_HOST_DIR"
  fi

  if [ ! -f clients.yml ]; then
    echo "Warning: clients.yml not found."
    echo "Create clients.yml before running the federated learning stack."
    return
  fi

  python3 - <<'PY'
from pathlib import Path
import yaml
config_path = Path("clients.yml")
with config_path.open("r", encoding="utf-8") as handle:
    config = yaml.safe_load(handle) or {}
clients = config.get("clients", [])
if len(clients) < 2:
    raise SystemExit("ERROR: clients.yml must define at least 2 clients.")
for client in clients:
    client_id = str(client.get("id", "")).strip()
    data_dir = str(client.get("data_dir", "")).strip()
    checkpoint_dir = str(client.get("checkpoint_dir", "")).strip()
    if not client_id:
        raise SystemExit("ERROR: Every client must define an 'id'.")
    if not data_dir:
        raise SystemExit(f"ERROR: Client '{client_id}' is missing 'data_dir'.")
    if not checkpoint_dir:
        raise SystemExit(f"ERROR: Client '{client_id}' is missing 'checkpoint_dir'.")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(checkpoint_dir).mkdir(parents=True, exist_ok=True)
    print(f"Prepared {client_id}: data={data_dir}, checkpoints={checkpoint_dir}")
PY
}

create_env_file() {
  if [ ! -f .env ]; then
    if [ -f .env.example ]; then
      cp .env.example .env
      echo "Created .env from .env.example"
    else
      echo "Warning: .env.example not found."
      echo "Create .env manually if needed."
    fi
  fi
  if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
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
  local profile="${DEPLOYMENT_PROFILE:-development}"
  if [ "$profile" != "production" ]; then
    return
  fi
  echo "Validating production TLS, SuperNode authentication, and SuperLink state material..."
  python3 - <<'PY'
from src.deployment_config import validate_environment
config = validate_environment(require_files=True)
print(f"Validated production SuperLink: {config.superlink_address}")
print(f"Validated SuperNode auth directory: {config.supernode_auth_host_dir}")
print(f"Validated SuperLink state directory: {config.superlink_state_host_dir}")
PY

  python3 - <<'PY'
from pathlib import Path
import yaml
from src.deployment_config import load_deployment_config
config = load_deployment_config()
with Path("clients.yml").open("r", encoding="utf-8") as handle:
    clients = (yaml.safe_load(handle) or {}).get("clients", [])
missing = []
for client in clients:
    client_id = str(client.get("id", "")).strip()
    if not config.supernode_auth_host_key(client_id).is_file():
        missing.append(client_id)
if missing:
    raise SystemExit("Missing SuperNode authentication keys for: " + ", ".join(missing))
print(f"Validated {len(clients)} SuperNode authentication keys.")
PY
}

generate_compose_file() {
  if [ ! -f clients.yml ]; then
    echo "ERROR: clients.yml not found."
    return 1
  fi
  if [ ! -f scripts/generate_compose.py ]; then
    echo "ERROR: scripts/generate_compose.py not found."
    return 1
  fi
  validate_auth_environment
  echo "Generating Docker Compose configuration..."
  python3 scripts/generate_compose.py --config clients.yml --output docker-compose.generated.yml --profile "${DEPLOYMENT_PROFILE:-development}"
  if [ ! -f docker-compose.generated.yml ]; then
    echo "ERROR: Docker Compose file was not generated."
    return 1
  fi
  echo "Generated docker-compose.generated.yml"
}

warn_missing_csvs() {
  if [ ! -f clients.yml ]; then
    echo "WARNING: clients.yml not found; cannot check client CSV files."
    return
  fi
  python3 - <<'PY'
from pathlib import Path
import yaml
with Path("clients.yml").open("r", encoding="utf-8") as handle:
    clients = (yaml.safe_load(handle) or {}).get("clients", [])
missing = []
for client in clients:
    data_dir = Path(str(client.get("data_dir", "")).strip())
    for filename in ("train.csv", "val.csv"):
        if not (data_dir / filename).is_file():
            missing.append(str(data_dir / filename))
if missing:
    print("WARNING: Some client CSV files are missing.")
    print("\nThe affected clients will use synthetic mock data until\nthe required CSV files are provided.\n")
    print("Missing files:")
    for file in missing:
        print(f"  {file}")
else:
    print("All configured client train/validation CSV files are present.")
PY
}

setup() {
  echo "=========================================="
  echo "Federated Learning Environment Setup"
  echo "=========================================="
  create_directories
  create_env_file
  prepare_development_auth
  validate_auth_environment
  generate_compose_file
  warn_missing_csvs
  echo
  echo "Setup completed."
}

run_tests() {
  echo "=========================================="
  echo "Running application tests in Docker"
  echo "=========================================="
  if [ ! -d tests ]; then echo "ERROR: tests directory not found."; return 1; fi
  if ! find tests -maxdepth 1 -name 'test_*.py' -print -quit | grep -q .; then echo "ERROR: No pytest test files found in tests/."; return 1; fi
  if [ ! -f docker-compose.generated.yml ]; then generate_compose_file; fi
  echo "=========================================="
  echo "Building shared Flower SuperExec image"
  echo "=========================================="
  docker build -f Dockerfile.superexec -t flwr_superexec:local .
  echo
  echo "Shared SuperExec image built successfully."
  echo
  docker compose -f docker-compose.generated.yml run --rm test-runner
}

start_trainer() {
  if [ ! -f docker-compose.generated.yml ]; then generate_compose_file; fi
  docker build -f Dockerfile.superexec -t flwr_superexec:local .
  if docker compose -f docker-compose.generated.yml up trainer; then
    echo "Trainer completed successfully."
    docker compose -f docker-compose.generated.yml down
  else
    echo "Trainer exited with an error."
    echo "Leaving the Compose stack running for inspection."
    return 1
  fi
}

print_config() {
  echo
  echo "Configured clients:"
  python3 - <<'PY'
from pathlib import Path
import yaml
if not Path("clients.yml").exists():
    print("  clients.yml not found")
    raise SystemExit(0)
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

Select an option:
  1) Setup required directories, authentication material, compose configuration, and environment file.
  2) Generate Compose configuration only
  3) Start the trainer service
  4) Setup and then start the trainer
  5) Run application tests in Docker
  6) Show configured clients
  7) Exit
EOF
}

print_menu
read -rp "Enter choice [1-7]: " choice
case "$choice" in
  1) setup ;;
  2) create_env_file && generate_compose_file ;;
  3) start_trainer ;;
  4) setup && start_trainer ;;
  5) run_tests ;;
  6) print_config ;;
  7) echo "Exiting."; exit 0 ;;
  *) echo "Invalid choice. Exiting."; exit 1 ;;
esac
