"""Lay out the demo's state directory before the backend first reads it.

Three things the backend expects to find on a real host:

* ``project/.env`` - the site settings compose would hand the container;
* ``assets/`` - the release files, linked from this checkout, plus the
  managed iperf3 artifacts a release build produces (stand-ins here, since
  building the real ones needs the release toolchain);
* ``checkout/`` - an installation directory the update page can look at: a
  throwaway git repository whose "remote" is a few commits ahead.

The runtime itself is not created here; the platform's own setup job does that.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import world

ASSET_LINKS = ("config", "http", "sensors", "libexec", "bootstrap", "install-mpp.sh")
IPERF_PLATFORMS = ("linux-amd64-glibc", "linux-arm64-glibc", "linux-armhf-glibc")

# What the branch gained since this installation was last updated. Neutral,
# made-up subjects in the project's style.
NEW_COMMITS = (
    "Show the return channel on the probe page",
    "Keep the sensor log filter when paging",
    "Explain a refused overlay endpoint in the form",
    "Retry a rollout only on the probes that failed",
)


def prepare_state(state: Path, repo: Path) -> tuple[str, str]:
    state.mkdir(parents=True, exist_ok=True)
    (state / "runtime").mkdir(exist_ok=True)
    _site_settings(state / "project")
    _assets(state / "assets", repo)
    return _checkout(state)


def _site_settings(project: Path) -> None:
    project.mkdir(exist_ok=True)
    (project / ".env").write_text(
        "# Site settings of the screenshot demo. Documentation addresses only.\n"
        f"NATS_FQDN={world.NATS_FQDN}\n"
        f"NATS_PORT={world.NATS_PORT}\n"
        f"NATS_HOST_IP={world.NATS_HOST_IP}\n"
        "CA_HTTP_PORT=80\n"
        f"CA_ORGANIZATION={world.CA_ORGANIZATION}\n"
        "PRTG_CORE_IP=192.0.2.20\n"
        f"IPERF_SSH_SOURCE_CIDR={world.NATS_HOST_IP}/32\n"
        "WEB_HTTPS_PORT=443\n"
        "PROBE_CHANNEL_PORT=8443\n",
        encoding="utf-8",
    )


def _assets(assets: Path, repo: Path) -> None:
    assets.mkdir(exist_ok=True)
    for name in ASSET_LINKS:
        link = assets / name
        if not link.is_symlink():
            link.symlink_to(repo / name)
    tool = assets / "tools" / "iperf3"
    tool.mkdir(parents=True, exist_ok=True)
    for source in (repo / "tools" / "iperf3").iterdir():
        link = tool / source.name
        if source.name != "artifacts" and not link.is_symlink():
            link.symlink_to(source)
    version = "3.21"
    artifacts = tool / "artifacts"
    entries = {}
    for platform in IPERF_PLATFORMS:
        payload = f"stand-in for iperf3 {version} built for {platform}\n".encode()
        target = artifacts / platform / "iperf3"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        entries[platform] = {
            "path": f"{platform}/iperf3",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    (artifacts / "manifest.json").write_text(
        json.dumps(
            {
                "format": 1,
                "tools": {"iperf3": {"version": version, "artifacts": entries}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _git(cwd: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "PRTG-NATS",
            "GIT_AUTHOR_EMAIL": "dev@example.test",
            "GIT_COMMITTER_NAME": "PRTG-NATS",
            "GIT_COMMITTER_EMAIL": "dev@example.test",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    if when is not None:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    return subprocess.run(  # noqa: S603 - fixed argv
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _checkout(state: Path) -> tuple[str, str]:
    checkout = state / "checkout"
    remote = state / "checkout-origin.git"
    if not (checkout / ".git").is_dir():
        now = datetime.now(UTC)
        remote.mkdir(parents=True, exist_ok=True)
        _git(remote, "init", "--bare", "--initial-branch=dev", "--quiet")
        work = state / "checkout-upstream"
        work.mkdir(exist_ok=True)
        _git(work, "init", "--initial-branch=dev", "--quiet")
        _git(work, "remote", "add", "origin", str(remote))
        (work / "README.md").write_text("# PRTG-NATS\n", encoding="utf-8")
        (work / "compose.yaml").write_text("name: prtg-nats\n", encoding="utf-8")
        _git(work, "add", ".")
        _git(
            work,
            "commit",
            "--quiet",
            "-m",
            "Release 0.4.0",
            when=now - timedelta(days=24),
        )
        _git(work, "tag", "v0.4.0")
        _git(work, "push", "--quiet", "origin", "dev", "--tags")
        _git(state, "clone", "--quiet", "--branch", "dev", str(remote), str(checkout))
        for index, subject in enumerate(NEW_COMMITS):
            (work / "CHANGES").write_text(f"{subject}\n", encoding="utf-8")
            _git(work, "add", ".")
            _git(
                work,
                "commit",
                "--quiet",
                "-m",
                subject,
                when=now - timedelta(days=6 - index, hours=3 * index),
            )
        _git(work, "push", "--quiet", "origin", "dev")
    head = _git(checkout, "rev-parse", "HEAD")
    version = _git(checkout, "describe", "--tags", "--always")
    return head, version
