"""The return channel step from the recovery command line (#177).

The shell enrolment and helper update reach the same step the jobs run, so
these check that the command drives that step and not a copy of it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from cryptography import x509

from app import ops
from app.core.config import Settings
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.probe_helper.protocol import RETURN_CHANNEL_HELPER_VERSION
from tests.conftest import ScriptedTransport, write_probe_inventory

PROBE = "mpp-berlin-01"


@pytest.fixture
def wired(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, transport: ScriptedTransport
) -> ScriptedTransport:
    monkeypatch.setattr(ops, "_settings", lambda: settings)
    monkeypatch.setattr(ops, "_helper_client", lambda _: ProbeHelperClient(transport))
    return transport


async def test_issues_a_certificate_for_the_probe(
    wired: ScriptedTransport, project_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_probe_inventory(project_dir, PROBE)

    await ops._cmd_return_channel(argparse.Namespace(username=PROBE, if_missing=False))

    assert wired.commands() == ["channel-csr", "channel-install"]
    _, install = wired.calls[1]
    assert install.payload is not None
    certificate = x509.load_pem_x509_certificates(install.payload.encode())[0]
    assert certificate.subject.rfc4514_string() == f"CN={PROBE}"
    assert "probe.return_channel_installed" in capsys.readouterr().out


async def test_if_missing_keeps_a_certificate_the_probe_has(
    wired: ScriptedTransport, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    wired.responses["probe-info"] = (
        "OK probe-info\n"
        f"helper_version={RETURN_CHANNEL_HELPER_VERSION}\n"
        "channel_cert=2027-02-06\n"
    )

    await ops._cmd_return_channel(argparse.Namespace(username=PROBE, if_missing=True))

    assert wired.commands() == ["probe-info", "channel-service"]


async def test_refuses_a_probe_that_is_not_enrolled(wired: ScriptedTransport) -> None:
    with pytest.raises(SystemExit):
        await ops._cmd_return_channel(
            argparse.Namespace(username=PROBE, if_missing=False)
        )
    assert wired.commands() == []
