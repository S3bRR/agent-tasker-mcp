#!/usr/bin/env bash
# AgentTasker MCP Server setup

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
RECREATE=0
QUIET=0
CLIENT=generic
PROVIDERS_FILE=""
MCP_CONFIG=""
SEARCH_PROVIDER=""

usage() {
  cat <<'EOF'
Usage: ./setup.sh [options]

Options:
  --client NAME         Print config for claude, codex, cursor, vscode, opencode,
                        or generic (default). Does not edit your settings.
  --search-provider brave  Enable Brave search (needs BRAVE_SEARCH_API_KEY)
  --providers-file PATH Default HTTP search provider JSON file
  --mcp-config PATH     Other stdio MCP servers to connect to
  --venv-dir PATH       Virtual environment directory (default: ./.venv)
  --recreate            Delete and recreate the virtual environment
  --quiet               Reduce setup output
  --help                Show this help text

Environment:
  VENV_DIR          Same as --venv-dir
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv-dir)
      [[ $# -lt 2 ]] && { echo "Error: --venv-dir requires a path"; exit 1; }
      VENV_DIR="$2"
      shift 2
      ;;
    --client|--providers-file|--mcp-config|--search-provider)
      [[ $# -lt 2 ]] && { echo "Error: $1 requires a value"; exit 1; }
      case "$1" in
        --client) CLIENT="$2" ;;
        --providers-file) PROVIDERS_FILE="$2" ;;
        --mcp-config) MCP_CONFIG="$2" ;;
        --search-provider) SEARCH_PROVIDER="$2" ;;
      esac
      shift 2
      ;;
    --recreate)
      RECREATE=1
      shift
      ;;
    --quiet)
      QUIET=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown option '$1'"
      usage
      exit 1
      ;;
  esac
done

case "$CLIENT" in
  claude|codex|cursor|vscode|opencode|generic) ;;
  *) echo "Error: unsupported client '$CLIENT'"; exit 1 ;;
esac
if [[ -n "$SEARCH_PROVIDER" && "$SEARCH_PROVIDER" != "brave" ]]; then
  echo "Error: --search-provider supports brave"
  exit 1
fi
if [[ -n "$SEARCH_PROVIDER" && -n "$PROVIDERS_FILE" ]]; then
  echo "Error: choose --search-provider or --providers-file, not both"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 not found on PATH"
  exit 1
fi

PYTHON_BIN="$(command -v python3)"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  FOUND="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  echo "Error: Python 3.10+ required (found $FOUND at $PYTHON_BIN)"
  exit 1
fi

case "$VENV_DIR" in
  /*) ;;
  *) VENV_DIR="$ROOT_DIR/$VENV_DIR" ;;
esac

if [[ "$RECREATE" -eq 1 && -d "$VENV_DIR" ]]; then
  rm -rf "$VENV_DIR"
fi

create_virtual_environment() {
  if [[ "$QUIET" -eq 1 ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR" >/dev/null 2>&1 && return 0
  else
    "$PYTHON_BIN" -m venv "$VENV_DIR" && return 0
  fi

  rm -rf "$VENV_DIR"
  if "$PYTHON_BIN" -m virtualenv --version >/dev/null 2>&1; then
    [[ "$QUIET" -eq 0 ]] && echo "python3 -m venv failed; falling back to virtualenv"
    if [[ "$QUIET" -eq 1 ]]; then
      "$PYTHON_BIN" -m virtualenv "$VENV_DIR" >/dev/null
    else
      "$PYTHON_BIN" -m virtualenv "$VENV_DIR"
    fi
    return 0
  fi

  cat >&2 <<EOF
Error: failed to create a virtual environment with python3 -m venv.

Install Python's venv package for your OS, or install virtualenv so setup.sh can
use it as a fallback.

Examples:
  Debian/Ubuntu:  sudo apt install python3-venv
  Python/pip:     python3 -m pip install --user virtualenv
  pipx:           pipx install virtualenv
EOF
  return 1
}

if [[ ! -d "$VENV_DIR" ]]; then
  [[ "$QUIET" -eq 0 ]] && echo "Creating virtual environment: $VENV_DIR"
  create_virtual_environment
else
  [[ "$QUIET" -eq 0 ]] && echo "Using existing virtual environment: $VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"

# Setuptools can otherwise include deleted modules from an older local build.
rm -rf "$ROOT_DIR/build"

if [[ "$QUIET" -eq 1 ]]; then
  "$VENV_PY" -m pip install "$ROOT_DIR" >/dev/null
else
  "$VENV_PY" -m pip install "$ROOT_DIR"
fi

find "$ROOT_DIR" -maxdepth 1 -name '*.egg-info' -prune -exec rm -rf {} +

"$VENV_DIR/bin/agent-tasker-mcp-server" --help >/dev/null

cat <<EOF

Setup complete.

Python:
  $VENV_PY

Server entrypoint:
  $VENV_DIR/bin/agent-tasker-mcp-server

Run locally:
  $VENV_DIR/bin/agent-tasker-mcp-server --workers 10

Config for $CLIENT (merge into your harness settings, then restart it):
EOF

CONFIG_ARGS=(--print-config "$CLIENT")
[[ -n "$SEARCH_PROVIDER" ]] && CONFIG_ARGS+=(--search-provider "$SEARCH_PROVIDER")
[[ -n "$PROVIDERS_FILE" ]] && CONFIG_ARGS+=(--providers-file "$PROVIDERS_FILE")
[[ -n "$MCP_CONFIG" ]] && CONFIG_ARGS+=(--mcp-config "$MCP_CONFIG")
"$VENV_DIR/bin/agent-tasker-mcp-server" "${CONFIG_ARGS[@]}"

printf '\nSetup guide: %s/docs/setup.md\n' "$ROOT_DIR"
