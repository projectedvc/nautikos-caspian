#!/usr/bin/env bash
set -euo pipefail

# One-command bootstrap for the Nautikos data origin. It only manages process
# IDs created by this project inside /home/jovyan/work/caspiansea.
WORK_ROOT="${NAUTIKOS_WORK_ROOT:-/home/jovyan/work/caspiansea}"
APP_ROOT="${NAUTIKOS_APP_ROOT:-$WORK_ROOT/app}"
DATA_ROOT="${NAUTIKOS_DATA_ROOT:-$WORK_ROOT/data-v2}"
PYTHON_BIN="${NAUTIKOS_PYTHON:-$APP_ROOT/server/.venv/bin/python}"
API_PORT="${NAUTIKOS_API_PORT:-8787}"
CLOUDFLARED_BIN="${NAUTIKOS_CLOUDFLARED:-$(command -v cloudflared 2>/dev/null || true)}"
if [[ -z "$CLOUDFLARED_BIN" ]] && [[ -x "$WORK_ROOT/.runtime/cloudflared" ]]; then
  CLOUDFLARED_BIN="$WORK_ROOT/.runtime/cloudflared"
fi

cd "$APP_ROOT"
mkdir -p "$DATA_ROOT/catalog/sentinel-2-earth-search"
cp server/seed-data/catalog/sentinel-2-earth-search/*.json \
  "$DATA_ROOT/catalog/sentinel-2-earth-search/"
mkdir -p "$DATA_ROOT/catalog/sentinel-1-earth-search"
cp server/seed-data/catalog/sentinel-1-earth-search/*.json \
  "$DATA_ROOT/catalog/sentinel-1-earth-search/"

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
stop_owned_process "$WORK_ROOT/cog-build-v1.pid" "build_cog_products.py"
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

if [[ -n "$CLOUDFLARED_BIN" ]]; then
  nohup "$CLOUDFLARED_BIN" tunnel --url "http://127.0.0.1:$API_PORT" \
    --protocol http2 --no-autoupdate \
    > "$WORK_ROOT/data-tunnel-v3.log" 2>&1 &
  echo $! > "$WORK_ROOT/data-tunnel-v3.pid"
else
  printf '%s\n' "cloudflared binary was not found" > "$WORK_ROOT/data-tunnel-v3.log"
fi

for _ in $(seq 1 30); do
  TUNNEL_URL="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$WORK_ROOT/data-tunnel-v3.log" | tail -1 || true)"
  if [[ -n "$TUNNEL_URL" ]]; then break; fi
  sleep 1
done
printf '%s\n' "${TUNNEL_URL:-}" > "$WORK_ROOT/data-tunnel-v3-url.txt"

# One resume-safe builder owns the six schema-3 analytical COGs. Set
# NAUTIKOS_RUN_COG_BUILDER=0 when this bootstrap should only restart the API.
if [[ "${NAUTIKOS_RUN_COG_BUILDER:-1}" == "1" ]]; then
  nohup "$PYTHON_BIN" server/scripts/build_cog_products.py \
    --years 2020:2026 --products all \
    --data-root "$DATA_ROOT" --workers "${NAUTIKOS_COG_WORKERS:-2}" \
    --continue-on-error \
    > "$WORK_ROOT/cog-build-v1.log" 2>&1 &
  echo $! > "$WORK_ROOT/cog-build-v1.pid"
fi

echo "Nautikos API: http://127.0.0.1:$API_PORT"
echo "Public tunnel: ${TUNNEL_URL:-not-ready; inspect $WORK_ROOT/data-tunnel-v3.log}"
echo "COG build log: $WORK_ROOT/cog-build-v1.log"
