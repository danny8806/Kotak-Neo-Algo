#!/usr/bin/env bash
# Activate an isolated, live-only Kotak Neo monitoring container on the VPS.
# Never touches or stops the existing Sniper paper dashboard on port 8004.
set -Eeuo pipefail
umask 077

BASE="${1:?Usage: activate_live_vps.sh BASE COMMIT_SHA}"
SHA="${2:?Need Git commit SHA}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid release SHA" >&2; exit 2; }
[[ "$BASE" == "/home/jadhavdnyaneshwar701/kotak-news-live" ]] || {
  echo "Unexpected deployment base; refusing unrelated paths" >&2; exit 2;
}
RELEASE="$BASE/releases/$SHA"
ENV_FILE="$BASE/config/live.env"
COMPOSE="$RELEASE/live_desk/compose.yaml"
SERVICE_NAME="kotak-news-live"
HOST_PORT=8010

[[ -r "$ENV_FILE" && -f "$COMPOSE" ]] || {
  echo "BLOCKED: missing private live.env or release compose file" >&2; exit 3;
}
actual_mode="$(stat -c %a "$ENV_FILE")"
[[ "$actual_mode" == "600" || "$actual_mode" == "400" ]] || {
  echo "BLOCKED: private live.env must have mode 600 or 400" >&2; exit 3;
}
# Provision a private URL-safe PostgreSQL password on the VPS when upgrading
# an existing install; do not print it or send it through GitHub Actions.
if ! grep -Eq '^POSTGRES_PASSWORD=.+' "$ENV_FILE"; then
  command -v openssl >/dev/null || { echo "BLOCKED: openssl is required to provision PostgreSQL" >&2; exit 3; }
  chmod 600 "$ENV_FILE"
  printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)" >> "$ENV_FILE"
fi
for name in SIGNAL_BRIDGE_TOKEN DASHBOARD_PASSWORD DASHBOARD_SESSION_SECRET; do
  grep -Eq "^${name}=.+" "$ENV_FILE" || {
    echo "BLOCKED: ${name} missing from server configuration" >&2; exit 3;
  }
done
grep -Eq '^POSTGRES_PASSWORD=[A-Za-z0-9_-]{32,}$' "$ENV_FILE" || {
  echo "BLOCKED: private URL-safe POSTGRES_PASSWORD is missing or invalid" >&2; exit 3;
}
# Compose defaults LIVE_ORDERS_ALLOWED to NO. The supervisor always starts
# disarmed; an operator must separately arm after every readiness check.
grep -Eq 'LIVE_ORDERS_ALLOWED:[[:space:]]*\$\{LIVE_ORDERS_ALLOWED:-NO\}' "$COMPOSE" || {
  echo "BLOCKED: Compose must default LIVE_ORDERS_ALLOWED to NO" >&2; exit 3;
}
command -v docker >/dev/null
command -v curl >/dev/null
docker compose version >/dev/null
[[ -f "$RELEASE/live_desk/static/index.html" ]] || {
  echo "BLOCKED: live browser UI missing" >&2; exit 3;
}

run_compose() {
  docker compose --project-name "$SERVICE_NAME" --env-file "$ENV_FILE" -f "$COMPOSE" "$@"
}
run_compose config -q
previous_release=""
if [[ -L "$BASE/current" ]]; then previous_release="$(readlink -f "$BASE/current")"; fi

echo "Building isolated live-only image for $SHA..."
run_compose build
echo "Starting PostgreSQL and localhost:8010; existing 8004 service remains untouched..."
run_compose up -d

verify() {
  local n health status
  for n in $(seq 1 35); do
    health="$(curl -fsS --max-time 3 http://127.0.0.1:$HOST_PORT/health 2>/dev/null || true)"
    if [[ "$health" == *'"mode":"LIVE_ONLY"'* && "$health" == *'"orders_armed":false'* ]]; then
      status="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:$HOST_PORT/api/state || true)"
      [[ "$status" == "401" ]] || { echo "BLOCKED: API authentication check failed ($status)" >&2; return 1; }
      echo "PASS: live-only mode, unauthenticated API rejected, orders DISARMED"
      return 0
    fi
    sleep 2
  done
  echo "FAILED: live service did not become healthy and disarmed" >&2
  return 1
}
if ! verify; then
  if [[ -n "$previous_release" && "$previous_release" != "$RELEASE" && -f "$previous_release/live_desk/compose.yaml" ]]; then
    echo "Attempting rollback of only the Kotak live-only container..."
    docker compose --project-name "$SERVICE_NAME" --env-file "$ENV_FILE" \
      -f "$previous_release/live_desk/compose.yaml" up -d --build --no-deps || true
  else
    echo "Stopping failed first live deployment; unrelated services untouched..."
    run_compose down || true
  fi
  exit 4
fi
ln -sfn "$RELEASE" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"
printf '%s\n' "$SHA" > "$BASE/last_verified_live_commit"
echo "DEPLOYED: isolated live monitoring container verified on 127.0.0.1:8010"
echo "Not publicly exposed. Configure dedicated HTTPS hostname for browser access."
echo "Service is DISARMED; no authenticated broker session or real signal was tested."
