#!/usr/bin/env bash
set -e

# --- Function to compare versions ---
version_ge() { 
  # returns 0 if $1 >= $2
  [ "$(printf '%s\n' "$@" | sort -V | head -n 1)" != "$1" ]
}

# --- Detect Python ---
if command -v python3 &>/dev/null; then
  PYTHON=python3
elif command -v python &>/dev/null; then
  PYTHON=python
else
  PYTHON=""
fi

# --- Check version or install ---
if [ -n "$PYTHON" ]; then
  VERSION=$($PYTHON -c "import sys; print('.'.join(map(str, sys.version_info[:3])))")
  echo "🔍 Found Python: $VERSION"

  if ! version_ge "$VERSION" "3.11.0"; then
    echo "⚠️ Python version too old ($VERSION). Installing Python 3.11..."
    sudo apt update
    sudo apt install -y python3.11 python3.11-venv python3.11-distutils
    PYTHON=python3.11
  fi
else
  echo "⚠️ Python not found. Installing Python 3.11..."
  sudo apt update
  sudo apt install -y python3.11 python3.11-venv python3.11-distutils
  PYTHON=python3.11
fi

# --- Ensure pip ---
if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
  echo "📦 Installing pip..."
  curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON
fi

# --- Setup backend environment ---
cd backend || { echo "❌ backend folder not found"; exit 1; }

echo "📦 Creating virtual environment..."
$PYTHON -m venv .venv
source .venv/bin/activate
echo "📦 Installing dependencies..."
#pip install --upgrade pip
pip install -r requirements.txt

cd sf_mcp_server || { echo "❌ sf_mcp_server folder not found"; exit 1; }
# --- Run backend servers ---
echo "🚀 Starting sf_mcp_server on port 3000..."
uvicorn sf_mcp_server:app --port 3000 &

cd ../agent_api_server || { echo "❌ agent_api_server folder not found"; exit 1; }
echo "🚀 Starting agent_api_server on port 8080..."
uvicorn mcp_client_fastapi:app --port 8080 &

cd ../../frontend/autonomous_agents_webapp || { echo "❌ frontend folder not found"; exit 1; }
# --- Setup frontend environment ---
npm install
# --- Start frontend ---
echo "🚀 Starting frontend dev server..."
npm run dev
