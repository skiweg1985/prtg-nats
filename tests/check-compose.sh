#!/usr/bin/env bash
# Render compose.yaml with example values and check what it exposes: which
# service sees which part of the runtime volume, that NATS_PORT reaches the
# port binding, and that a missing required value makes Compose refuse.
#
# Needs Docker Compose. Run from the repository root; it writes a throwaway
# .env there and removes it again.
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ -e .env ]]; then
  echo "A .env exists here; this check writes its own. Run it in a clean checkout." >&2
  exit 1
fi
trap 'rm -f .env .env.tmp' EXIT

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is not available on this runner; check skipped."
  exit 0
fi
# Site settings no longer have a built-in default; the check sets
# example values, the way an automated deployment would.
cp .env.example .env
cat >> .env <<'ENVEOF'
NATS_FQDN=nats.example.test
NATS_HOST_IP=127.0.0.1
PRTG_CORE_IP=127.0.0.1
ENVEOF
sed -i 's/^ *//' .env
docker compose --project-directory . config --quiet
echo "The Compose configuration is valid."

# Who may see which part of the runtime volume. Parsed rather than
# grepped: a pattern that silently stops matching is a check that
# passes while checking nothing.
docker compose --project-directory . config --format json > /tmp/compose.json
python3 - /tmp/compose.json <<'PY'
import json, sys

services = json.load(open(sys.argv[1]))["services"]
failures = []

def mounts(name):
    for volume in services.get(name, {}).get("volumes", []):
        yield (
            volume.get("source"),
            (volume.get("volume") or {}).get("subpath"),
            volume.get("target"),
            volume.get("type"),
        )

# Runtime state belongs in the volume, never on a host path. A bind
# mount of the checkout is what used to pin an installation to one
# machine, and it is what the runtime volume replaced.
for name in services:
    for source, _, target, kind in mounts(name):
        if kind == "bind" and source and source.rstrip("/").endswith(
            ("/runtime", "/prtg_nats_gh")
        ):
            failures.append(f"{name} bind-mounts runtime state: {source}")

# NATS reads its configuration and serves its certificates. It has no
# business seeing the CA key, the credentials, or the private key of
# the interface certificate - so it gets subpaths, not the volume.
nats = list(mounts("nats"))
runtime_subpaths = {sub for src, sub, _, _ in nats if src == "runtime"}
if runtime_subpaths != {"conf", "certs"}:
    failures.append(
        f"nats should mount exactly conf/ and certs/, got {runtime_subpaths}"
    )

# The interface private key goes to the proxy and nowhere else.
for name in services:
    if name == "web-proxy":
        continue
    for source, subpath, _, _ in mounts(name):
        if source == "runtime" and subpath is None and name != "web-api" \
                and name != "runtime-init":
            failures.append(f"{name} mounts the whole runtime volume")
        if subpath == "web-certs":
            failures.append(f"{name} can read the interface private key")

# The overlay hub's key is its own secret, and no service defined
# here has any business reading it. The hub itself is not one: it
# needs NET_ADMIN in the host network namespace, so it is created
# through the Docker API when an administrator enables the overlay,
# and this file only builds its image. A service that grew a mount
# of that directory would be a privilege nobody asked for.
for name in services:
    for source, subpath, _, _ in mounts(name):
        if subpath == "overlay":
            failures.append(f"{name} can read the overlay hub key")
if services.get("overlay", {}).get("volumes"):
    failures.append("the overlay build service should mount nothing")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("Volume exposure is as intended.")
PY

# The port from .env has to reach the port binding.
sed -i 's/^NATS_PORT=.*/NATS_PORT=24999/' .env
docker compose --project-directory . config |
  grep -q 'published: "24999"' ||
  { echo "NATS_PORT does not reach the port binding"; exit 1; }
echo "NATS_PORT reaches the port binding."

# A missing required value has to make Compose itself refuse.
grep -v '^NATS_HOST_IP' .env > .env.tmp && mv .env.tmp .env
if docker compose --project-directory . config --quiet 2>/dev/null; then
  echo "A missing NATS_HOST_IP went unnoticed"; exit 1
fi
echo "A missing required value is detected."
