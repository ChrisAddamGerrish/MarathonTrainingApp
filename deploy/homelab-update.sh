#!/bin/sh
# Keeps a home lab's Docker install up to date with master: run it every few minutes from cron.
#
# CI (.github/workflows/ci.yml) publishes a new image to ghcr.io whenever master passes its tests.
# This pulls the latest commit (for compose.yaml and the Caddyfile) and the latest image, and
# restarts the app only when the image changed. The data and secrets volumes are untouched.
#
# Needs MARATHON_IMAGE in .env (see .env.example), and git, docker and flock on the home lab.
#   */5 * * * * /path/to/MarathonTrainingApp/deploy/homelab-update.sh >> /path/to/marathon-update.log 2>&1
set -eu

cd "$(dirname "$0")/.."

# One run at a time, in case a pull takes longer than the cron interval.
exec 9>"${TMPDIR:-/tmp}/marathon-update.lock"
flock -n 9 || exit 0

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

if ! grep -q '^MARATHON_IMAGE=' .env 2>/dev/null; then
    log "MARATHON_IMAGE isn't set in .env, so there is no published image to follow" >&2
    exit 1
fi

# Fast-forward only: a local edit to a tracked file stops this, not the image update.
git pull --ff-only --quiet || log "git pull failed; updating the image anyway" >&2

before=$(docker compose images -q app 2>/dev/null || true)
docker compose pull --quiet app
# Recreates the container only if the image (or its settings) changed, and waits for it to be healthy.
docker compose up -d --no-build --wait app
after=$(docker compose images -q app)

if [ "$before" != "$after" ]; then
    log "deployed image $after (commit $(git rev-parse --short HEAD))"
    docker image prune -f > /dev/null
fi
