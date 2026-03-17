#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
ENV_TEMPLATE="$ROOT_DIR/.env.example"
CONFIG_FILE="$ROOT_DIR/config.json"
CONFIG_TEMPLATE="$ROOT_DIR/config.example.json"

HOST="127.0.0.1"
PORT="8765"
RUN_INSTALL=1
LAUNCH_DASHBOARD=1
NON_INTERACTIVE=0

WAYFINDER_API_KEY_VALUE="${WAYFINDER_API_KEY:-}"
KIMI_API_KEY_VALUE="${KIMI_API_KEY:-}"
TAVILY_API_KEY_VALUE="${TAVILY_API_KEY:-}"

usage() {
  cat <<'EOF'
Usage: ./scripts/quickstart.sh [options]

Creates local config files, optionally installs dependencies, and starts the dashboard.

Options:
  --wayfinder-api-key KEY   Set WAYFINDER_API_KEY in .env
  --kimi-api-key KEY        Set KIMI_API_KEY in .env
  --tavily-api-key KEY      Set TAVILY_API_KEY in .env
  --host HOST               Dashboard host (default: 127.0.0.1)
  --port PORT               Dashboard port (default: 8765)
  --skip-install            Skip `poetry install`
  --no-dashboard            Only set up files; do not launch the dashboard
  --non-interactive         Do not prompt for missing keys
  -h, --help                Show this help

You can also provide the keys through environment variables instead of flags:
  WAYFINDER_API_KEY, KIMI_API_KEY, TAVILY_API_KEY
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wayfinder-api-key)
      WAYFINDER_API_KEY_VALUE="${2:-}"
      shift 2
      ;;
    --kimi-api-key)
      KIMI_API_KEY_VALUE="${2:-}"
      shift 2
      ;;
    --tavily-api-key)
      TAVILY_API_KEY_VALUE="${2:-}"
      shift 2
      ;;
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --skip-install)
      RUN_INSTALL=0
      shift
      ;;
    --no-dashboard)
      LAUNCH_DASHBOARD=0
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

prompt_secret_if_empty() {
  local name="$1"
  local label="$2"
  local current="${!name:-}"
  if [[ -n "$current" || "$NON_INTERACTIVE" -eq 1 || ! -t 0 ]]; then
    return
  fi
  printf "%s (press enter to skip): " "$label" >&2
  stty -echo
  IFS= read -r current || true
  stty echo
  printf "\n" >&2
  printf -v "$name" '%s' "$current"
}

ensure_templates() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    if [[ -f "$CONFIG_TEMPLATE" ]]; then
      cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
    else
      cat >"$CONFIG_FILE" <<'EOF'
{
  "system": {
    "api_key": ""
  }
}
EOF
    fi
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$ENV_TEMPLATE" ]]; then
      cp "$ENV_TEMPLATE" "$ENV_FILE"
    else
      : >"$ENV_FILE"
    fi
  fi
}

upsert_env_value() {
  local key="$1"
  local value="$2"
  local escaped="$value"
  escaped="${escaped//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="\"$escaped\"" '
    BEGIN { done = 0 }
    $0 ~ "^" key "=" {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (!done) {
        print key "=" value
      }
    }
  ' "$ENV_FILE" >"$tmp"
  mv "$tmp" "$ENV_FILE"
}

upsert_env_if_provided() {
  local key="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    upsert_env_value "$key" "$value"
  fi
}

ensure_templates

prompt_secret_if_empty WAYFINDER_API_KEY_VALUE "Wayfinder API key"
prompt_secret_if_empty KIMI_API_KEY_VALUE "Kimi API key"
prompt_secret_if_empty TAVILY_API_KEY_VALUE "Tavily API key"

upsert_env_value "WAYFINDER_CONFIG_PATH" "./config.json"
upsert_env_value "AUTOLAB_STRATEGY_EXPORT_DIR" "wayfinder_autolab/live/generated_strategies"
upsert_env_if_provided "WAYFINDER_API_KEY" "$WAYFINDER_API_KEY_VALUE"
upsert_env_if_provided "KIMI_API_KEY" "$KIMI_API_KEY_VALUE"
upsert_env_if_provided "TAVILY_API_KEY" "$TAVILY_API_KEY_VALUE"

if ! command -v poetry >/dev/null 2>&1; then
  echo "Poetry is required but was not found in PATH." >&2
  exit 1
fi

cd "$ROOT_DIR"

if [[ "$RUN_INSTALL" -eq 1 ]]; then
  poetry install
fi

echo "Configured:"
echo "  .env        -> $ENV_FILE"
echo "  config.json -> $CONFIG_FILE"
echo "  dashboard   -> http://$HOST:$PORT/"

if [[ "$LAUNCH_DASHBOARD" -eq 1 ]]; then
  exec poetry run autolab dashboard --host "$HOST" --port "$PORT"
fi
