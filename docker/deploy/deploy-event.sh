#!/usr/bin/env bash
#
# Forced-command deploy script for the event sites (talks.pycon.de, videos.pydata-berlin.org, ...).
#
# Install on the server as /usr/local/bin/deploy-event (root-owned, mode 0755) and pin each
# target's CI public key to it in ~/.ssh/authorized_keys, baking the TARGET into the forced command:
#
#   command="/usr/local/bin/deploy-event talks.pycon.de",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... ci-talks
#   command="/usr/local/bin/deploy-event videos.pydata-berlin.org",...                                                  ssh-ed25519 AAAA... ci-videos
#
# Because the target is fixed in the forced command, a leaked CI key can only ever deploy ITS OWN
# site. CI runs `ssh <user>@<host> "<git-sha>"`; that sha arrives in $SSH_ORIGINAL_COMMAND and is
# the only thing CI controls. This script ignores everything else the client might send.
#
# What it does, all from the SAME build so the static manifest always matches the assets:
#   1. validate the target (allowlist) and the tag (a git sha, nothing else)
#   2. pull the shared app image and the static-assets image at that tag
#   3. extract the assets and swap them into the target's nginx cache dir
#   4. point the target's compose at the new tag and roll the container
#   5. health-check; roll back to the previous tag if it does not come up
#
# It needs no sudo: the invoking user is in the `docker` group and owns each target's COMPOSE_DIR
# and STATIC_DIR. See docs/deployment/ci-cd.md for the one-time server setup.

set -euo pipefail

# ----- configuration ------------------------------------------------------------------
REGISTRY="ghcr.io/pioneershub"
APP_IMAGE="${REGISTRY}/event-talks"
STATIC_IMAGE="${REGISTRY}/event-talks-static"
# Max time to wait for the container to report healthy. Must comfortably exceed a cold start plus
# the entrypoint migrations; pairs with the compose healthcheck's start_period.
HEALTH_TIMEOUT=180
# Targets this script may deploy. Must match the GitHub allowlist in .github/workflows/deploy.yml.
ALLOWED_TARGETS=("talks.pycon.de" "videos.pydata-berlin.org")
# ---------------------------------------------------------------------------------------

log() { printf '>> %s\n' "$*"; }
die() { printf 'deploy-event: %s\n' "$*" >&2; exit 1; }

# 1a. The target comes from the authorized_keys forced command, not the client, so it is trusted.
#     Validate it anyway in case the script is invoked by hand.
target="${1:-}"
[[ -n "$target" ]] || die "no target (set via authorized_keys forced command: deploy-event <target>)"
allowed=false
for t in "${ALLOWED_TARGETS[@]}"; do [[ "$t" == "$target" ]] && allowed=true; done
$allowed || die "target not allowed: ${target}"

# 1b. The tag is the only client-supplied value. Accept a hex git sha and nothing else, so a leaked
#     key cannot point the deploy at an arbitrary image.
tag="$(printf '%s' "${SSH_ORIGINAL_COMMAND:-}" | tr -d '[:space:]')"
[[ "$tag" =~ ^[0-9a-f]{7,40}$ ]] || die "invalid tag '${tag}' (expected a git sha)"

# Per-target paths follow the standard server layout.
COMPOSE_DIR="${HOME}/${target}"
STATIC_DIR="/var/cache/${target}/staticfiles"
ENV_FILE="${COMPOSE_DIR}/.env"
[[ -f "$ENV_FILE" ]] || die "env file not found: ${ENV_FILE}"

# Container name = CONTAINER_PREFIX-django (CONTAINER_PREFIX is the domain in each target's .env).
prefix="$(sed -n 's/^CONTAINER_PREFIX=//p' "$ENV_FILE" | head -n1)"
[[ -n "$prefix" ]] || prefix="$target"
container="${prefix}-django"

# Remember the currently-deployed tag so we can roll back on a failed health check.
prev_tag="$(sed -n 's/^IMAGE_TAG=//p' "$ENV_FILE" | head -n1 || true)"

log "deploying ${target} -> ${tag} (previous: ${prev_tag:-none})"

# 2. Pull both images at this exact tag.
docker pull "${APP_IMAGE}:${tag}"
docker pull "${STATIC_IMAGE}:${tag}"

# 3. Extract the collected static assets from the static image and sync them into the target's
#    nginx cache dir. --chmod keeps files world-readable (nginx runs as www-data) without trying to
#    preserve the root ownership baked into the image.
tmp="$(mktemp -d)"
cid="$(docker create "${STATIC_IMAGE}:${tag}")"
# shellcheck disable=SC2329  # invoked indirectly via the EXIT trap below
cleanup() { docker rm -f "$cid" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
docker cp "${cid}:/staticfiles/." "${tmp}/"
rsync -rlpt --delete --chmod=D755,F644 "${tmp}/" "${STATIC_DIR}/"
log "synced $(find "${tmp}" -type f | wc -l | tr -d ' ') static files to ${STATIC_DIR}"

# 4. Point this target's compose at the new tag and roll the app container.
set_env_var() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}
set_env_var IMAGE_NAME "$APP_IMAGE"
set_env_var IMAGE_TAG "$tag"

# Poll the container's own healthcheck (keys off the container name, no dependency on a host port)
# until it reports healthy or HEALTH_TIMEOUT elapses. Returns 0 if healthy.
wait_healthy() {
  local deadline=$(( SECONDS + HEALTH_TIMEOUT )) status
  while true; do
    status="$(docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ else }}none{{ end }}' "$container" 2>/dev/null || echo missing)"
    [[ "$status" == "healthy" ]] && return 0
    if (( SECONDS >= deadline )); then
      log "  ${container} not healthy after ${HEALTH_TIMEOUT}s (status=${status})"
      return 1
    fi
    sleep 3
  done
}

cd "$COMPOSE_DIR"
docker compose up -d --no-build

# 5. Health-check, and on failure roll back. Only roll back to a real previous sha (never to an
#    unpinned 'latest' or an empty tag), and re-verify the rollback so we never report success while
#    the site is actually down.
log "waiting for ${container} to become healthy (up to ${HEALTH_TIMEOUT}s)"
if wait_healthy; then
  log "deploy of ${tag} to ${target} is healthy"
  docker image prune -f >/dev/null 2>&1 || true
  exit 0
fi

if [[ "$prev_tag" =~ ^[0-9a-f]{7,40}$ && "$prev_tag" != "$tag" ]]; then
  log "deploy of ${tag} FAILED; rolling back ${target} to ${prev_tag}"
  set_env_var IMAGE_TAG "$prev_tag"
  docker compose up -d --no-build
  if wait_healthy; then
    die "deploy of ${tag} failed; rolled back to ${prev_tag}, which is healthy"
  fi
  die "deploy of ${tag} failed AND rollback to ${prev_tag} is not healthy; ${target} may be DOWN"
fi
die "deploy of ${tag} to ${target} failed health check; no valid previous sha to roll back to"
