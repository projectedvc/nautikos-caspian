#!/usr/bin/env bash
set -euo pipefail

# One-command bootstrap for the Nautikos data origin. It only manages process
# IDs created by this project inside /home/jovyan/work/caspiansea.
WORK_ROOT="${NAUTIKOS_WORK_ROOT:-/home/jovyan/work/caspiansea}"
APP_ROOT="${NAUTIKOS_APP_ROOT:-$WORK_ROOT/app}"
DATA_ROOT="${NAUTIKOS_DATA_ROOT:-$WORK_ROOT/data-v2}"
PYTHON_BIN="${NAUTIKOS_PYTHON:-$APP_ROOT/server/.venv/bin/python}"
API_PORT="${NAUTIKOS_API_PORT:-8787}"

cd "$APP_ROOT"
mkdir -p "$DATA_ROOT/catalog/sentinel-2-earth-search"
cp server/seed-data/catalog/sentinel-2-earth-search/*.json \
  "$DATA_ROOT/catalog/sentinel-2-earth-search/"

stop_owned_process() {
  local pid_file="$1"
  local expected="$2"
  if [[ ! -f "$pid_file" ]]; then return 0; fi
  local pid
  pid="$(tr -cd '0-9' < "$pid_file")"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then return 0; fi
  local command
  command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  if [[ "$command" == *"$expected"* ]]; then
    kill "$pid"
  fi
}

for pid_file in data-api-v2.pid data-api-v3.pid data-api-v3b.pid data-api-v4.pid data-api-v5.pid data-api-v6.pid; do
  stop_owned_process "$WORK_ROOT/$pid_file" "uvicorn"
done
for pid_file in data-tunnel-v2.pid data-tunnel-v2b.pid data-tunnel-v3.pid; do
  stop_owned_process "$WORK_ROOT/$pid_file" "cloudflared"
done
stop_owned_process "$WORK_ROOT/download-earth-search.pid" "download_earth_search.py"
stop_owned_process "$WORK_ROOT/data-build-v4.pid" "download_earth_search.py"
sleep 2

export NAUTIKOS_DATA_ROOT="$DATA_ROOT"
export NAUTIKOS_ALLOWED_ORIGINS="${NAUTIKOS_ALLOWED_ORIGINS:-https://nautikos-caspian.vercel.app}"

nohup "$PYTHON_BIN" -m uvicorn server.nautikos_server.api:app \
  --host 0.0.0.0 --port "$API_PORT" --workers 1 \
  > "$WORK_ROOT/data-api-v6.log" 2>&1 &
echo $! > "$WORK_ROOT/data-api-v6.pid"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null; then break; fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$API_PORT/health"

nohup cloudflared tunnel --url "http://127.0.0.1:$API_PORT" \
  --protocol http2 --no-autoupdate \
  > "$WORK_ROOT/data-tunnel-v3.log" 2>&1 &
echo $! > "$WORK_ROOT/data-tunnel-v3.pid"

for _ in $(seq 1 30); do
  TUNNEL_URL="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$WORK_ROOT/data-tunnel-v3.log" | tail -1 || true)"
  if [[ -n "$TUNNEL_URL" ]]; then break; fi
  sleep 1
done
printf '%s\n' "${TUNNEL_URL:-}" > "$WORK_ROOT/data-tunnel-v3-url.txt"

# Resume-safe localization followed by one-time preparation of all historical
# RGB and environmental overlays used by the browser. Normal map use then
# reads only finished local files.
nohup bash -lc "cd '$APP_ROOT' && \
  '$PYTHON_BIN' server/scripts/download_earth_search.py \
    --years 2020:2026 \
    --catalog-root server/seed-data/catalog/sentinel-2-earth-search \
    --data-root '$DATA_ROOT' --workers 3 && \
  '$PYTHON_BIN' server/scripts/prewarm_cache.py \
    --api 'http://127.0.0.1:$API_PORT' \
    --years 2020:2026 \
    --products rgb,water_colour,water_extent,turbidity,suspended_matter,vegetation,soil_stress \
    --zooms 3:9 --workers 2" \
  > "$WORK_ROOT/data-build-v4.log" 2>&1 &
echo $! > "$WORK_ROOT/data-build-v4.pid"

echo "Nautikos API: http://127.0.0.1:$API_PORT"
echo "Public tunnel: ${TUNNEL_URL:-not-ready; inspect $WORK_ROOT/data-tunnel-v3.log}"
echo "Build log: $WORK_ROOT/data-build-v4.log"
