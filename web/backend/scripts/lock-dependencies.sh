#!/usr/bin/env bash
# Resolve the backend dependencies anew and write both lockfiles.
#
# pyproject.toml keeps lower bounds; the lockfiles hold the exact versions and
# hashes the image and the checks install. --universal resolves for every
# platform at once, so an arm64 and an amd64 build get the same versions. The
# development lock is constrained by the runtime lock, so tests run against
# what the image ships.
#
# Usage, from anywhere: web/backend/scripts/lock-dependencies.sh [--upgrade]
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

common=(
  --universal
  --generate-hashes
  --python-version 3.11
  --custom-compile-command "./scripts/lock-dependencies.sh"
  --quiet
)

uv pip compile pyproject.toml "${common[@]}" "$@" -o requirements.lock
uv pip compile pyproject.toml --extra dev -c requirements.lock "${common[@]}" "$@" \
  -o requirements-dev.lock
