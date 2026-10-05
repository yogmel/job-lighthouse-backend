#!/usr/bin/env bash
# Runs on the droplet. Pulls one image and restarts the stack on it.
#
#   usage: deploy.sh <image-ref> [<ghcr-user>]
#
# If <ghcr-user> is given, a GHCR token is read from stdin and used for the
# pull only (logged out afterwards). Without it, the image must already be
# pullable (e.g. a manual rollback after `docker login`).
set -euo pipefail

IMAGE_REF="${1:?usage: deploy.sh <image-ref> [<ghcr-user>]}"
GHCR_USER="${2:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/job-lighthouse}"
APP_SERVICES=(migrate job-runner companies)

cd "$DEPLOY_DIR"
test -f .env || { echo "missing $DEPLOY_DIR/.env" >&2; exit 1; }

# Exported APP_IMAGE wins over image.env during this deploy. image.env is
# only rewritten once the deploy succeeds, so a failed one leaves it on the
# last good image.
touch image.env
export APP_IMAGE="$IMAGE_REF"
compose() { docker compose --env-file .env --env-file image.env "$@"; }

if [[ -n "$GHCR_USER" ]]; then
  read -r GHCR_TOKEN
  trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
fi

# Only the app image. Postgres/Nginx are pulled on first `up` and then stay
# put, so a deploy never silently bumps the database.
compose pull "${APP_SERVICES[@]}"
# Compose only warns when a buildable service's pull fails.
docker image inspect "$IMAGE_REF" >/dev/null
# A service that never gets healthy (e.g. crash-looping) fails the deploy
# instead of hanging it.
compose up -d --no-build --wait --wait-timeout 180 --remove-orphans

ids=()
while read -r id; do ids+=("$id"); done < <(compose ps -aq "${APP_SERVICES[@]}")
if (( ${#ids[@]} != ${#APP_SERVICES[@]} )); then
  echo "expected ${#APP_SERVICES[@]} app containers, found ${#ids[@]}" >&2
  exit 1
fi
for id in "${ids[@]}"; do
  running="$(docker inspect --format '{{.Config.Image}}' "$id")"
  if [[ "$running" != "$IMAGE_REF" ]]; then
    echo "container $id runs $running, expected $IMAGE_REF" >&2
    exit 1
  fi
done

# PROJ-011: Nginx resolves the app containers' IPs only at start, and `up`
# leaves it running while it recreates them. Reload so it picks up the new
# IPs (and any nginx/default.conf change); otherwise it proxies to stale ones.
compose exec -T nginx nginx -s reload

# Kept on disk so any later `docker compose` call uses the deployed image
# instead of trying to build (there is no source here).
echo "APP_IMAGE=$IMAGE_REF" > image.env
echo "deployed $IMAGE_REF"

# Old sha- tags pile up otherwise; images in use are kept.
docker image prune -af --filter "until=168h" >/dev/null
