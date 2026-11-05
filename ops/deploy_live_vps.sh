#!/usr/bin/env bash
# GitHub Actions SSH deployment runner for the existing Sniper VPS.
# PINNED SSH host key required. No paper container, company Nginx, or port 8004 changes.
set -Eeuo pipefail
umask 077

SHA="${GITHUB_SHA:-}"
HOST="${VPS_HOST:-200.234.44.93}"
USER_NAME="${VPS_USER:-root}"
BASE="/home/jadhavdnyaneshwar701/kotak-news-live"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "BLOCKED: unexpected commit SHA" >&2; exit 2; }
[[ "$HOST" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "BLOCKED: unexpected VPS hostname" >&2; exit 2; }
[[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "BLOCKED: unexpected VPS user" >&2; exit 2; }

if [[ -z "${VPS_SSH_PRIVATE_KEY:-}" || -z "${VPS_SSH_KNOWN_HOSTS:-}" ]]; then
  echo "NOT DEPLOYED: GitHub Actions secrets VPS_SSH_PRIVATE_KEY and/or VPS_SSH_KNOWN_HOSTS missing." >&2
  echo "Configure an approved SSH deploy credential in repository Actions secrets; never commit it." >&2
  exit 3
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
printf '%s\n' "$VPS_SSH_PRIVATE_KEY" > "$WORK/key"
printf '%s\n' "$VPS_SSH_KNOWN_HOSTS" > "$WORK/known_hosts"
chmod 600 "$WORK/key" "$WORK/known_hosts"
SSH_OPTS=(-i "$WORK/key" -o IdentitiesOnly=yes -o BatchMode=yes
          -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$WORK/known_hosts"
          -o ConnectTimeout=12)
SCP_OPTS=(-i "$WORK/key" -o IdentitiesOnly=yes -o BatchMode=yes
          -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$WORK/known_hosts"
          -o ConnectTimeout=12)

# Transmit only the actual LIVE source and bridge validator. Never old app assets,
# local .env, stored models, paper ledgers, research archives, or any user secrets.
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='.env.*' \
    -czf "$WORK/live.tgz" \
    bridge scripts live_desk \
    requirements-bridge.txt requirements-live-test.txt requirements-live.txt \
    ops/activate_live_vps.sh ops/retire_old_dashboard.sh
REMOTE="${USER_NAME}@${HOST}"
REMOTE_TAR="/tmp/kotak-news-live-${SHA}.tgz"
echo "Uploading live-only release to the separate $BASE/release directory..."
ssh "${SSH_OPTS[@]}" "$REMOTE" \
  "install -d -m 700 '$BASE/releases/$SHA' && install -d -m 700 '$BASE/config'"
scp "${SCP_OPTS[@]}" "$WORK/live.tgz" "$REMOTE:$REMOTE_TAR"
ssh "${SSH_OPTS[@]}" "$REMOTE" \
  "tar -xzf '$REMOTE_TAR' -C '$BASE/releases/$SHA' && rm -f '$REMOTE_TAR' && bash '$BASE/releases/$SHA/ops/activate_live_vps.sh' '$BASE' '$SHA'"
echo "VPS deployment command returned success. Verify HTTPS routing independently."
