#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

ensure_host_dependencies() {
  local role="${1:-server}"
  local missing_commands=()
  if ! command -v python3 >/dev/null 2>&1; then missing_commands+=("python3"); fi
  if ! command -v docker >/dev/null 2>&1; then
    missing_commands+=("docker")
  elif ! docker compose version >/dev/null 2>&1; then
    missing_commands+=("docker compose")
  fi
  if [ "$role" = server ] && ! command -v openssl >/dev/null 2>&1; then missing_commands+=("openssl"); fi
  if [ "$role" = client ] && ! command -v ssh-keygen >/dev/null 2>&1; then missing_commands+=("ssh-keygen"); fi
  if ((${#missing_commands[@]} > 0)); then
    echo >&2
    echo "Missing required host tools: ${missing_commands[*]}" >&2
    echo "Install the missing operating-system packages, then rerun ./setup.sh." >&2
    return 1
  fi
  if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo >&2
    echo "Missing required Python library: PyYAML" >&2
    if ! python3 -m pip --version >/dev/null 2>&1; then
      echo "pip is not available for Python 3." >&2
      echo "Install pip using your operating-system package manager, then rerun ./setup.sh." >&2
      return 1
    fi
    read -rp "Install PyYAML for this host now? [Y/n]: " install_choice
    if [[ "$install_choice" =~ ^[Nn]$ ]]; then
      echo "ERROR: PyYAML is required by the setup scripts." >&2
      echo "Install it with: python3 -m pip install PyYAML" >&2
      return 1
    fi
    echo "Installing PyYAML..."
    python3 -m pip install --user PyYAML || { echo "ERROR: Could not install PyYAML automatically." >&2; return 1; }
    python3 -c 'import yaml' >/dev/null 2>&1 || { echo "ERROR: PyYAML installation completed but yaml cannot be imported." >&2; return 1; }
    echo "PyYAML is available."
  else
    echo "PyYAML: available"
  fi
  echo "Host prerequisites verified for $role host."
}

load_environment() {
  if [ ! -f .env ]; then
    if [ -f .env.example ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo "ERROR: .env.example not found. Create .env before continuing."; return 1; fi
  fi
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
}

read_clients() { [ -f clients.yml ] || { echo "ERROR: clients.yml not found." >&2; return 1; }; }

select_host_role() {
  local configured_role="${DEPLOYMENT_ROLE:-}"
  if [ "$configured_role" = server ] || [ "$configured_role" = client ]; then printf '%s\n' "$configured_role"; return 0; fi
  printf '\n' >&2
  printf '%s\n' "What type of host are you preparing?" >&2
  printf '%s\n' "  1) Server host — runs Flower SuperLink and ServerApp" >&2
  printf '%s\n' "  2) Client host — runs one SuperNode and one ClientApp" >&2
  read -rp "Enter choice [1-2]: " role_choice
  case "$role_choice" in 1) printf '%s\n' server ;; 2) printf '%s\n' client ;; *) echo "ERROR: Invalid host role selection." >&2; return 1 ;; esac
}

read_client_ids() {
  local line
  CLIENT_IDS=()
  while IFS= read -r line; do [ -n "$line" ] && CLIENT_IDS+=("$line"); done <<EOF
$(python3 - <<'PY'
from pathlib import Path
import yaml
with Path("clients.yml").open(encoding="utf-8") as handle:
    clients = (yaml.safe_load(handle) or {}).get("clients", [])
for client in clients:
    value = str(client.get("id", "")).strip()
    if value: print(value)
PY
)
EOF
}

select_client_id() {
  local selected="${CLIENT_ID:-}"
  if [ -n "$selected" ]; then printf '%s\n' "$selected"; return 0; fi
  read_client_ids
  if ((${#CLIENT_IDS[@]} == 0)); then echo "ERROR: No clients are configured in clients.yml." >&2; return 1; fi
  printf '\n' >&2
  printf '%s\n' "Select the client assigned to this machine:" >&2
  local index=1 client_id
  for client_id in "${CLIENT_IDS[@]}"; do printf '  %s) %s\n' "$index" "$client_id" >&2; index=$((index + 1)); done
  read -rp "Enter client number: " selection
  if ! [[ "$selection" =~ ^[0-9]+$ ]] || [ "$selection" -lt 1 ] || [ "$selection" -gt "${#CLIENT_IDS[@]}" ]; then echo "ERROR: Invalid client selection." >&2; return 1; fi
  printf '%s\n' "${CLIENT_IDS[$((selection - 1))]}"
}

require_client_ca_certificate() {
  local ca_file="${TLS_CERTIFICATE_HOST_DIR:-./certificates/prod/tls}/ca.crt"
  if [ ! -f "$ca_file" ]; then
    echo >&2
    echo "ERROR: Required federation CA certificate was not found." >&2
    echo "Expected location: $ca_file" >&2
    echo "Copy the federation's ca.crt to that location, then rerun ./setup.sh." >&2
    return 1
  fi
  chmod 644 "$ca_file"
  echo "Federation CA certificate found: $ca_file"
}

create_starter_tls_material() {
  [ "$1" = server ] || return 0
  local tls_dir="${TLS_CERTIFICATE_HOST_DIR:-./certificates/prod/tls}"
  local starter_endpoint="${SUPERLINK_ADDRESS:-fl.example.internal:9092}"
  local starter_host="${STARTER_SUPERLINK_HOST:-${starter_endpoint%%:*}}"
  mkdir -p "$tls_dir"
  command -v openssl >/dev/null 2>&1 || { echo "ERROR: OpenSSL is required." >&2; return 1; }
  local ca_key="$tls_dir/.starter-ca.key" ca_crt="$tls_dir/ca.crt" superlink_key="$tls_dir/superlink.key" superlink_crt="$tls_dir/superlink.crt" csr="$tls_dir/.starter-superlink.csr" ext="$tls_dir/.starter-superlink.ext"
  if [ ! -f "$ca_crt" ] || [ ! -f "$superlink_crt" ] || [ ! -f "$superlink_key" ]; then
    echo "Creating starter federation CA and SuperLink certificate for $starter_host..."
    openssl genrsa -out "$ca_key" 4096 >/dev/null 2>&1
    openssl req -x509 -new -nodes -key "$ca_key" -sha256 -days 3650 -out "$ca_crt" -subj "/CN=FederatedHomes Starter CA" >/dev/null 2>&1
    openssl genrsa -out "$superlink_key" 2048 >/dev/null 2>&1
    openssl req -new -key "$superlink_key" -out "$csr" -subj "/CN=$starter_host" >/dev/null 2>&1
    if [[ "$starter_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:%s,DNS:localhost,IP:127.0.0.1\n' "$starter_host" > "$ext"
    else
      printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:%s,DNS:localhost,IP:127.0.0.1\n' "$starter_host" > "$ext"
    fi
    openssl x509 -req -in "$csr" -CA "$ca_crt" -CAkey "$ca_key" -CAcreateserial -out "$superlink_crt" -days 825 -sha256 -extfile "$ext" >/dev/null 2>&1
    rm -f "$csr" "$ext" "$tls_dir/ca.srl"
  fi
  rm -f "$ca_key"
  chmod 644 "$ca_crt" "$superlink_crt"
  chmod 600 "$superlink_key"
  echo "Server TLS material is ready in $tls_dir"
  echo "Share only ca.crt with client hosts."
}

create_starter_client_auth() {
  local client_id="$1" auth_dir="${SUPERNODE_AUTH_HOST_DIR:-./certificates/prod/auth}"
  local private_key="$auth_dir/$client_id" public_key="$auth_dir/$client_id.pub"
  require_client_ca_certificate
  mkdir -p "$auth_dir"
  command -v ssh-keygen >/dev/null 2>&1 || { echo "ERROR: ssh-keygen is required." >&2; return 1; }
  if [ -f "$private_key" ] || [ -f "$public_key" ]; then
    if [ -f "$private_key" ] && [ -f "$public_key" ]; then chmod 600 "$private_key"; chmod 644 "$public_key"; echo "SuperNode authentication key pair already exists for $client_id."; return 0; fi
    echo "ERROR: Incomplete SuperNode authentication key pair for $client_id." >&2; return 1
  fi
  echo "Creating SuperNode authentication key pair for $client_id..."
  ssh-keygen -q -t ecdsa -b 384 -f "$private_key" -N "" -C "flower-supernode-$client_id"
  chmod 600 "$private_key"; chmod 644 "$public_key"
  echo "SuperNode key pair created for $client_id."
}

create_directories() {
  local role="$1" client_id="${2:-}"
  if [ "$role" = server ]; then
    if [ "${DEPLOYMENT_PROFILE:-development}" = production ]; then
      [ -z "${SUPERLINK_STATE_HOST_DIR:-}" ] || mkdir -p "$SUPERLINK_STATE_HOST_DIR"
      [ -z "${SUPERLINK_STATE_HOST_DIR:-}" ] || echo "Prepared SuperLink state directory: $SUPERLINK_STATE_HOST_DIR"
      create_starter_tls_material server
    fi
    return 0
  fi
  if [ "$role" = client ]; then
    read_clients
    if [ "${DEPLOYMENT_PROFILE:-development}" = production ]; then create_starter_client_auth "$client_id"; fi
    python3 - "$client_id" <<'PY'
from pathlib import Path
import sys, yaml
requested = sys.argv[1].strip()
with Path("clients.yml").open(encoding="utf-8") as handle: clients = (yaml.safe_load(handle) or {}).get("clients", [])
if len(clients) < 2: raise SystemExit("ERROR: clients.yml must define at least 2 clients.")
selected = [c for c in clients if str(c.get("id", "")).strip() == requested]
if not selected: raise SystemExit(f"ERROR: Client ID '{requested}' is not defined in clients.yml.")
client = selected[0]
for field in ("data_dir", "checkpoint_dir"):
    value = str(client.get(field, "")).strip()
    if not value: raise SystemExit(f"ERROR: Client '{requested}' is missing '{field}'.")
    Path(value).mkdir(parents=True, exist_ok=True)
print(f"Prepared {requested}: data={client['data_dir']}, checkpoints={client['checkpoint_dir']}")
PY
  fi
}

prepare_development_auth() {
  [ "${DEPLOYMENT_PROFILE:-development}" = development ] || return 0
  [ "${DEPLOYMENT_ROLE:-all}" = all ] || return 0
  [ -f clients.yml ] && [ -f scripts/generate_supernode_auth.py ] || return 0
  local auth_dir="${DEV_SUPERNODE_AUTH_DIR:-certificates/dev/auth}"
  read_client_ids
  local missing=0 client_id
  for client_id in "${CLIENT_IDS[@]}"; do [ -f "$auth_dir/$client_id" ] && [ -f "$auth_dir/$client_id.pub" ] || missing=1; done
  if [ "$missing" -eq 1 ]; then python3 scripts/generate_supernode_auth.py --output-dir "$auth_dir" "${CLIENT_IDS[@]}"; fi
}

show_host_context() {
  local role="$1" client_id="${2:-}"
  echo
  echo "Deployment profile: ${DEPLOYMENT_PROFILE:-development}"
  echo "Host role:          $role"
  if [ "$role" = client ]; then
    echo "Client identity:    $client_id"
    echo "Identity scope:     only $client_id credentials are created/used on this host"
  fi
  echo
}

register_client_supernode() {
  local client_id="$1"
  [ "${DEPLOYMENT_PROFILE:-development}" = production ] || return 0
  local ca_file="${TLS_CERTIFICATE_HOST_DIR:-./certificates/prod/tls}/ca.crt"
  local public_key="${SUPERNODE_AUTH_HOST_DIR:-./certificates/prod/auth}/$client_id.pub"
  local control_address="${SUPERLINK_CONTROL_ADDRESS:-}"
  [ -n "$control_address" ] || { echo "ERROR: SUPERLINK_CONTROL_ADDRESS is required for client registration." >&2; return 1; }
  [ -f "$ca_file" ] || { echo "ERROR: SuperLink CA certificate not found: $ca_file" >&2; return 1; }
  [ -f "$public_key" ] || { echo "ERROR: SuperNode public key not found: $public_key" >&2; return 1; }
  echo "Registering $client_id with the SuperLink Control API..."
  if python3 scripts/register_supernode.py --client-id "$client_id" --public-key "$public_key" --superlink-address "$control_address" --root-certificates "$ca_file"; then
    echo "SuperNode $client_id is registered with the federation."
  else
    echo "ERROR: SuperNode registration failed for $client_id." >&2
    echo "Ensure the server SuperLink is running and TCP port 9093 is reachable from this client." >&2
    return 1
  fi
}

validate_auth_environment() {
  local role="$1" client_id="${2:-}"
  [ "${DEPLOYMENT_PROFILE:-development}" = production ] || return 0
  echo "Validating production TLS, SuperNode authentication, and deployment state..."
  python3 - "$role" "$client_id" <<'PY'
from pathlib import Path
import sys, yaml
from src.deployment_config import load_deployment_config
role, client_id = sys.argv[1], sys.argv[2].strip()
config = load_deployment_config(role=role, require_files=True)
print(f"Validated production SuperLink endpoint: {config.superlink_address}")
print(f"Validated SuperLink Control API: {config.superlink_control_address}")
print(f"Validated TLS material: {config.tls_certificate_host_dir}")
with Path("clients.yml").open(encoding="utf-8") as handle: clients = (yaml.safe_load(handle) or {}).get("clients", [])
if role == "client":
    if not client_id: raise SystemExit("ERROR: CLIENT_ID must be set for a client deployment.")
    selected = [c for c in clients if str(c.get("id", "")).strip() == client_id]
    if not selected: raise SystemExit(f"ERROR: Client ID '{client_id}' is not defined in clients.yml.")
    private = config.supernode_auth_host_key(client_id); public = private.with_name(private.name + ".pub")
    for path in (private, public):
        if not path.is_file(): raise SystemExit(f"Missing SuperNode authentication material: {path}")
    print(f"Validated SuperNode authentication key pair for {client_id}.")
else:
    if config.superlink_state_host_dir is None: raise SystemExit("ERROR: Server deployment requires SuperLink persistent state configuration.")
    print(f"Validated SuperLink state directory: {config.superlink_state_host_dir}")
PY
}

prepare_host() {
  load_environment; read_clients
  local role client_id=""
  role="$(select_host_role)"; export DEPLOYMENT_ROLE="$role"
  ensure_host_dependencies "$role"
  if [ "$role" = client ]; then client_id="$(select_client_id)"; export CLIENT_ID="$client_id"; fi
  show_host_context "$role" "$client_id"
  if [ "$role" = client ]; then
    if [ "${DEPLOYMENT_PROFILE:-development}" != production ]; then
      echo "ERROR: Physical client setup requires DEPLOYMENT_PROFILE=production." >&2
      echo "The development profile uses insecure Flower transport and does not create production credentials." >&2
      echo "Update .env to use the production profile, then rerun client setup." >&2
      return 1
    fi
  fi
  create_directories "$role" "$client_id"
  prepare_development_auth
  validate_auth_environment "$role" "$client_id"
  if [ "$role" = client ] && [ "${DEPLOYMENT_PROFILE:-development}" = production ]; then register_client_supernode "$client_id"; fi
  echo
  echo "Host preparation complete for role=$role${client_id:+, client=$client_id}."
}

generate_server_compose() {
  load_environment; read_clients; ensure_host_dependencies server; export DEPLOYMENT_ROLE=server
  python3 scripts/generate_compose.py --config clients.yml --output docker-compose.server.yml --profile "${DEPLOYMENT_PROFILE:-development}" --role server
  echo "Generated docker-compose.server.yml"
}

generate_client_compose() {
  load_environment; read_clients; ensure_host_dependencies client; export DEPLOYMENT_ROLE=client
  local client_id="${CLIENT_ID:-}"
  [ -n "$client_id" ] || client_id="$(select_client_id)"
  export CLIENT_ID="$client_id"
  python3 scripts/generate_compose.py --config clients.yml --output docker-compose.client.yml --profile "${DEPLOYMENT_PROFILE:-development}" --role client --client-id "$client_id"
  echo "Generated docker-compose.client.yml for $client_id"
}

start_server_federation() {
  generate_server_compose
  echo "Starting server infrastructure..."
  docker compose -f docker-compose.server.yml up --build
}

run_tests() {
  read_clients; load_environment; ensure_host_dependencies server
  export DEPLOYMENT_PROFILE=development DEPLOYMENT_ROLE=all
  prepare_development_auth
  validate_auth_environment all
  local output="${DEV_COMPOSE_FILE:-docker-compose.generated.yml}"
  python3 scripts/generate_compose.py --config clients.yml --output "$output" --profile development --role all
  docker compose -f "$output" run --rm test-runner
}

show_config() {
  load_environment; read_clients
  echo
  echo "Deployment profile: ${DEPLOYMENT_PROFILE:-development}"
  echo "Deployment role:    ${DEPLOYMENT_ROLE:-unset}"
  echo "Client ID:          ${CLIENT_ID:-unset}"
  echo "SuperLink:          ${SUPERLINK_ADDRESS:-unset}"
  echo "Control API:        ${SUPERLINK_CONTROL_ADDRESS:-unset}"
  echo
  cat clients.yml
}

run_local_development_compose() {
  read_clients; load_environment; ensure_host_dependencies server
  export DEPLOYMENT_PROFILE=development DEPLOYMENT_ROLE=all
  prepare_development_auth
  local output="${DEV_COMPOSE_FILE:-docker-compose.generated.yml}"
  python3 scripts/generate_compose.py --config clients.yml --output "$output" --profile development --role all
  docker compose -f "$output" up --build
}

main_menu() {
  while true; do
    echo
    echo "FederatedHomes Flower deployment setup"
    echo "  1) Prepare host"
    echo "  2) Generate server Compose"
    echo "  3) Generate client Compose"
    echo "  4) Start server infrastructure"
    echo "  5) Run tests"
    echo "  6) Show configuration"
    echo "  7) Start local all-in-one development federation"
    echo "  8) Exit"
    read -rp "Select an option [1-8]: " option
    case "$option" in
      1) prepare_host ;; 2) generate_server_compose ;; 3) generate_client_compose ;; 4) start_server_federation ;; 5) run_tests ;; 6) show_config ;; 7) run_local_development_compose ;; 8) exit 0 ;; *) echo "ERROR: Invalid option. Please choose 1-8." >&2 ;;
    esac
  done
}

main_menu
