#!/usr/bin/env bash
set -euo pipefail

SESSION="agents_dev"

# -------- helpers --------
version_ge() {  # returns 0 if $1 >= $2
  [ "$(printf '%s\n' "$@" | sort -V | head -n 1)" != "$1" ]
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

os="$(uname -s)"

# -------- ensure tmux --------
if ! need_cmd tmux; then
  echo "ℹ️ tmux not found. Installing or giving instructions..."
  case "$os" in
    Linux)
      # This covers Ubuntu + WSL (Ubuntu)
      if need_cmd apt; then
        sudo apt update
        sudo apt install -y tmux
      else
        echo "❌ 'apt' not found. Please install tmux with your distro's pkg manager."
        exit 1
      fi
      ;;
    Darwin)
      if need_cmd brew; then
        brew install tmux
      else
        echo "❌ Homebrew not found. Install Homebrew from https://brew.sh/ then run: brew install tmux"
        exit 1
      fi
      ;;
    *)
      echo "❌ Unsupported OS: $os"
      exit 1
      ;;
  esac
fi

# -------- detect Python (prefer 3.11+) --------
PYTHON=""
if need_cmd python3.11; then
  PYTHON=python3.11
elif need_cmd python3; then
  PYTHON=python3
elif need_cmd python; then
  PYTHON=python
fi

if [ -n "$PYTHON" ]; then
  VERSION=$($PYTHON -c "import sys; print('.'.join(map(str, sys.version_info[:3])))")
  echo "🔍 Found Python: $VERSION"
else
  VERSION="0"
  echo "⚠️ Python not found."
fi

# Install/upgrade Python 3.11 if needed (Ubuntu/WSL auto; macOS via brew if present)
if ! version_ge "${VERSION:-0}" "3.11.0"; then
  echo "⚠️ Python < 3.11 detected. Setting up Python 3.11..."
  case "$os" in
    Linux)
      if need_cmd apt; then
        sudo apt update
        sudo apt install -y python3.11 python3.11-venv python3.11-distutils
        PYTHON=python3.11
      else
        echo "❌ Couldn't install Python automatically (no apt)."
        exit 1
      fi
      ;;
    Darwin)
      if need_cmd brew; then
        brew install python@3.11
        # brew may install as /opt/homebrew/bin/python3.11 or /usr/local...
        if need_cmd python3.11; then
          PYTHON=python3.11
        else
          PYTHON="$(brew --prefix)/bin/python3.11"
        fi
      else
        echo "❌ Please install Homebrew and run: brew install python@3.11"
        exit 1
      fi
      ;;
    *)
      echo "❌ Unsupported OS for auto Python install."
      exit 1
      ;;
  esac
fi

# -------- ensure pip --------
if ! $PYTHON -m pip --version >/dev/null 2>&1; then
  echo "📦 Ensuring pip..."
  if ! $PYTHON -m ensurepip --upgrade >/dev/null 2>&1; then
    curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON
  fi
fi

# -------- project layout checks --------
# Run from repo root (script's directory can be anywhere)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"

[ -d backend ] || { echo "❌ backend folder not found"; exit 1; }
[ -d frontend/autonomous_agents_webapp ] || { echo "❌ frontend/autonomous_agents_webapp not found"; exit 1; }
[ -f backend/requirements.txt ] || { echo "❌ backend/requirements.txt not found"; exit 1; }

# -------- backend venv + deps --------
echo "🧱 Setting up backend venv..."
cd backend
if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
cd ..

# -------- tmux session (3 windows) --------
# If a session exists, kill it to start fresh (optional; comment if you prefer reuse)
if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
fi

# Window 1: sf_mcp_server
tmux new-session -d -s "$SESSION" -n "sf_mcp_server" \
  -c "$(pwd)/backend/sf_mcp_server" \
  "source ../.venv/bin/activate && echo '🚀 Starting sf_mcp_server on :3000' && uvicorn sf_mcp_server:app --port 3000"

# Window 2: agent_api_server
tmux new-window -t "$SESSION:" -n "agent_api_server" \
  -c "$(pwd)/backend/agent_api_server" \
  "source ../.venv/bin/activate && echo '🚀 Starting agent_api_server on :8080' && uvicorn mcp_client_fastapi:app --port 8080"

# Window 3: frontend
tmux new-window -t "$SESSION:" -n "frontend" \
  -c "$(pwd)/frontend/autonomous_agents_webapp" \
  "echo '📦 npm install...'; npm install && echo '🚀 Starting frontend dev server...' && npm run dev"

# Focus a nice default window
tmux select-window -t "$SESSION:1"

# Attach (if not already inside tmux)
if [ -z "${TMUX:-}" ]; then
  tmux attach -t "$SESSION"
else
  echo "✅ Started in tmux session '$SESSION'. Use: tmux switch-client -t $SESSION"
fi
