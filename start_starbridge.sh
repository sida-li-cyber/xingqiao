#!/usr/bin/env bash
# ============================================================
#  StarBridge one-click launcher (Linux / macOS)
#  Starts backend(:8000) + simulation core + frontend(:8080),
#  then prints the URL. Stop with:  ./start_starbridge.sh stop
# ============================================================
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"

STOP=0
[ "$1" = "stop" ] && STOP=1
if [ $STOP -eq 1 ]; then
    echo "Stopping StarBridge ..."
    if [ -f .starbridge.pids ]; then
        while read -r pid; do kill "$pid" 2>/dev/null || true; done < .starbridge.pids
        rm -f .starbridge.pids
    fi
    pkill -f "realtime_backend.run" 2>/dev/null || true
    pkill -f "demo_sim_core.py"     2>/dev/null || true
    pkill -f "http.server 8080"     2>/dev/null || true
    echo "Done."
    exit 0
fi

echo "=========================================="
echo "  StarBridge - One-click Launcher"
echo "=========================================="

command -v python3 >/dev/null || { echo "[ERROR] python3 not found"; exit 1; }

# 1) venv
if [ ! -x ".venv/bin/python" ]; then
    echo "[1/4] Creating virtual environment .venv ..."
    python3 -m venv .venv
fi
PY="$ROOT/.venv/bin/python"

# 2) deps
echo "[2/4] Checking dependencies (first run may take 1-2 min) ..."
"$PY" -m pip install --quiet --disable-pip-version-check -r requirements-runtime.txt

# 3) start three processes
echo "[3/4] Starting backend / core / frontend ..."
: > .starbridge.pids
"$PY" -m realtime_backend.run --port 8000 >> .starbridge.log 2>&1 &
echo $! >> .starbridge.pids
sleep 3
# Real ships (AIS): auto-load the converted tracks JSON when present.
AIS_ARGS=()
if [ -f realtime_backend/data/ais/ships_marine_cadastre.json ]; then
    AIS_ARGS=(--ais-file "$ROOT/realtime_backend/data/ais/ships_marine_cadastre.json")
    echo "[AIS] Real ship tracks found - AIS replay layer enabled."
fi
(cd hypatia-master/satviz && "$PY" demo_sim_core.py --port 8000 "${AIS_ARGS[@]}") >> .starbridge.log 2>&1 &
echo $! >> .starbridge.pids
"$PY" -m http.server 8080 --directory hypatia-master/satviz >> .starbridge.log 2>&1 &
echo $! >> .starbridge.pids

# 4) open browser (best effort)
echo "[4/4] Opening browser ..."
sleep 3
URL="http://127.0.0.1:8080/static_html/index.html"
(command -v xdg-open >/dev/null && xdg-open "$URL") || (command -v open >/dev/null && open "$URL") || true

echo ""
echo "=========================================="
echo "  StarBridge is UP."
echo "  Frontend : $URL"
echo "  Backend  : http://127.0.0.1:8000/health"
echo "  Log      : .starbridge.log"
echo "  Stop     : ./start_starbridge.sh stop"
echo "=========================================="
