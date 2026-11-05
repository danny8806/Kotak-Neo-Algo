#!/usr/bin/env bash
# Run on the authorized VPS after the separate live desk has been deployed.
# Stops only the old Compose project. Keeps its images, bind-mounted data and .env.
set -Eeuo pipefail

OLD_BASE=/home/jadhavdnyaneshwar701/sniper-paper-desk
OLD_COMPOSE="$OLD_BASE/compose.yaml"
NEW_BASE=/home/jadhavdnyaneshwar701/kotak-news-live
NEW_COMPOSE="$NEW_BASE/current/live_desk/compose.yaml"
NEW_ENV="$NEW_BASE/config/live.env"

for path in "$OLD_COMPOSE" "$NEW_COMPOSE" "$NEW_ENV"; do
  [[ -f "$path" ]] || { echo "BLOCKED: required cutover file missing: $path" >&2; exit 2; }
done
command -v docker >/dev/null
command -v curl >/dev/null
docker compose version >/dev/null

new_health() {
  local body state container
  container="$(docker compose --project-name kotak-news-live --env-file "$NEW_ENV" \
    -f "$NEW_COMPOSE" ps -q kotak-live-news)"
  [[ -n "$container" ]] || return 1
  state="$(docker inspect --format '{{.State.Health.Status}}' "$container")"
  [[ "$state" == healthy ]] || return 1
  body="$(curl -fsS --max-time 3 http://127.0.0.1:8010/health)"
  [[ "$body" == *'"mode":"LIVE_ONLY"'* && "$body" == *'"orders_armed":false'* ]]
}

new_health || { echo 'BLOCKED: new live desk is not healthy and disarmed' >&2; exit 3; }
auth_status="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:8010/api/state)"
[[ "$auth_status" == 401 ]] || { echo 'BLOCKED: new dashboard authentication check failed' >&2; exit 3; }

old_id="$(docker inspect --format '{{.Id}}' sniper-paper-desk 2>/dev/null || true)"
if [[ -z "$old_id" ]]; then
  echo 'Old sniper-paper-desk container is already absent; new live desk is healthy.'
  exit 0
fi
old_project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' sniper-paper-desk)"
old_service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' sniper-paper-desk)"
[[ "$old_project" == sniper-paper-desk && "$old_service" == stock-dashboard ]] || {
  echo 'BLOCKED: old container identity differs from expected Compose service' >&2; exit 4;
}

restore_old() {
  echo 'Cutover verification failed; attempting to restart the original Compose project.' >&2
  docker compose --project-name sniper-paper-desk --project-directory "$OLD_BASE" \
    -f "$OLD_COMPOSE" up -d --no-build || true
}
trap restore_old ERR
docker compose --project-name sniper-paper-desk --project-directory "$OLD_BASE" \
  -f "$OLD_COMPOSE" down --timeout 30
new_health
trap - ERR
echo 'CUTOVER COMPLETE: old paper Compose project is down; new live desk remains healthy on localhost:8010.'
echo 'Old bind-mounted data, .env and images were preserved. The new live service remains disarmed.'
