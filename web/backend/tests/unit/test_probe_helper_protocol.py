"""The helper wire format, tested against captured shapes.

These fixtures are what libexec/prtg-nats-probe-helper actually prints. If the
probe side ever changes them, these tests are the first thing that fails - which
is the point.
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    ProbeHelperOutdatedError,
    ProbeProtocolError,
    ProbeRejectedError,
)
from app.domain.enums import ServiceState
from app.domain.models import ObservedProbeState, parse_probe_info, parse_sensor_list
from app.infrastructure.probe_helper import (
    CURRENT_HELPER_VERSION,
    HelperCommand,
    HelperRequest,
    normalise_optional,
    parse_response,
    refusal_error,
)
from tests.conftest import REPO_ROOT

PROBE_INFO = """OK probe-info
package=2.1.0
service=active
helper_version=1
helper_sha256=c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00
hostname=berlin-probe-01.example.test
ca_sha256=3b2f1a0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a
config=/etc/paessler/mpprobe/config.yaml
id=11111111-2222-3333-4444-555555555555
access_key=ABCDEF123456
name=Berlin Probe 01
"""

# What a probe enrolled before helper versions existed answers: the same shape
# minus the two fields. Kept as its own fixture because it is the state every
# installed probe is in until it has been updated once.
PROBE_INFO_WITHOUT_HELPER_VERSION = """OK probe-info
package=2.1.0
service=active
hostname=berlin-probe-01.example.test
ca_sha256=3b2f1a0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a
config=/etc/paessler/mpprobe/config.yaml
id=11111111-2222-3333-4444-555555555555
access_key=ABCDEF123456
name=Berlin Probe 01
"""

SENSOR_LIST = (
    "OK sensor-list\n"
    "internet-speed\tversion=2\tsha256=aaaa\tinterfaces=\thelper=none\n"
    "wlan-auth\tversion=1\tsha256=bbbb\tinterfaces=wlan0,wlan1\thelper=active\n"
)


def test_request_encoding_is_tab_separated() -> None:
    request = HelperRequest(
        command=HelperCommand.SENSOR_STAGE,
        arguments=("txn-1", "internet-speed", "script"),
    )
    assert request.encode() == "sensor-stage\ttxn-1\tinternet-speed\tscript\n"


def test_request_rejects_a_tab_in_an_argument() -> None:
    """The tab is the field separator, so it cannot appear inside a field."""
    with pytest.raises(ProbeProtocolError):
        HelperRequest(command=HelperCommand.SENSOR_REMOVE, arguments=("a\tb",))


def test_request_rejects_more_than_three_arguments() -> None:
    with pytest.raises(ProbeProtocolError):
        HelperRequest(command=HelperCommand.STATUS, arguments=("a", "b", "c", "d"))


def test_probe_info_is_parsed() -> None:
    response = parse_response(PROBE_INFO, expected=HelperCommand.PROBE_INFO)
    assert response.value("service") == "active"
    assert response.value("package") == "2.1.0"
    assert response.required("id") == "11111111-2222-3333-4444-555555555555"


def test_header_fields_are_parsed() -> None:
    """`status` answers "OK active config=/path" - state and a field in one line."""
    response = parse_response(
        "OK active config=/etc/paessler/mpprobe/config.yaml\n",
        expected=HelperCommand.STATUS,
    )
    assert response.command == "active"
    assert response.header_fields["config"] == "/etc/paessler/mpprobe/config.yaml"


def test_a_response_without_ok_is_a_protocol_error() -> None:
    with pytest.raises(ProbeProtocolError):
        parse_response("something went wrong\n", expected=HelperCommand.STATUS)


def test_an_empty_response_is_a_protocol_error() -> None:
    with pytest.raises(ProbeProtocolError):
        parse_response("", expected=HelperCommand.PROBE_INFO)


def test_missing_required_field_is_a_protocol_error() -> None:
    response = parse_response(
        "OK probe-info\nservice=active\n", expected=HelperCommand.PROBE_INFO
    )
    with pytest.raises(ProbeProtocolError):
        response.required("package")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("none", None), ("unknown", None), ("", None), ("  ", None), ("2.1.0", "2.1.0")],
)
def test_none_placeholder_becomes_absent(raw: str, expected: str | None) -> None:
    """The helper writes the literal string `none` where a value is absent."""
    assert normalise_optional(raw) == expected


def test_observed_state_from_probe_info() -> None:
    from datetime import UTC, datetime

    response = parse_response(PROBE_INFO, expected=HelperCommand.PROBE_INFO)
    observed = parse_probe_info("mpp-berlin-01", response, datetime.now(UTC))

    assert observed.reachable
    assert observed.service is ServiceState.ACTIVE
    assert observed.package_version == "2.1.0"
    assert observed.probe_name == "Berlin Probe 01"
    # Presence is recorded; the value is not carried into observed state.
    assert observed.has_access_key
    assert observed.helper_version == 1
    assert not observed.helper_outdated


def test_a_probe_without_a_helper_version_counts_as_outdated() -> None:
    """The state every probe is in until it has been updated once."""
    from datetime import UTC, datetime

    response = parse_response(
        PROBE_INFO_WITHOUT_HELPER_VERSION, expected=HelperCommand.PROBE_INFO
    )
    observed = parse_probe_info("mpp-berlin-01", response, datetime.now(UTC))

    assert observed.helper_version is None
    assert observed.helper_sha256 is None
    assert observed.helper_outdated


def test_an_unreachable_probe_is_not_called_outdated() -> None:
    """Silence says nothing about the helper, and a warning on every probe
    that happens to be down would say nothing either."""
    from datetime import UTC, datetime

    observed = ObservedProbeState(
        nats_username="mpp-berlin-01",
        observed_at=datetime.now(UTC),
        reachable=False,
    )

    assert not observed.helper_outdated


def test_sensor_list_records_are_parsed() -> None:
    response = parse_response(SENSOR_LIST, expected=HelperCommand.SENSOR_LIST)
    sensors = parse_sensor_list(response)

    assert [sensor.name for sensor in sensors] == ["internet-speed", "wlan-auth"]
    assert sensors[0].version == "2"
    assert sensors[0].interfaces == ()
    assert sensors[1].interfaces == ("wlan0", "wlan1")
    assert sensors[1].helper_state == "active"


def test_every_command_exists_on_the_probe() -> None:
    """A command this side can send that the probe cannot answer is a bug.

    The helper replies "Unsupported management request" to anything its
    dispatch does not list, and that failure would surface on a customer's
    machine rather than here.
    """
    helper = (REPO_ROOT / "libexec" / "prtg-nats-probe-helper").read_text(
        encoding="utf-8"
    )
    # The newline the split consumed goes back on, so the first case label
    # matches the same way as every other one.
    dispatch = "\n" + helper.partition('\ncase "${command_name}" in\n')[2]
    assert dispatch.strip(), "the helper's dispatch moved; this test needs updating"

    missing = [
        command.value
        for command in HelperCommand
        if f"\n  {command.value})\n" not in dispatch
    ]
    assert not missing, f"the probe helper does not handle: {missing}"


def test_an_unknown_request_is_reported_as_an_outdated_helper() -> None:
    """The refusal that is not about the request at all.

    This is the failure an operator meets first: a job asks for something the
    probe predates, and "the probe refused" would send them looking for a
    mistake they did not make.
    """
    error = refusal_error(
        "mpp-berlin-01",
        HelperCommand.MPP_UNINSTALL,
        "ERROR: Unsupported management request\n",
    )

    assert isinstance(error, ProbeHelperOutdatedError)
    assert error.params["command"] == "mpp-uninstall"


def test_any_other_refusal_stays_a_rejection() -> None:
    error = refusal_error(
        "mpp-berlin-01",
        HelperCommand.INSTALL_CA,
        "ERROR: CA certificate has expired\n",
    )

    assert isinstance(error, ProbeRejectedError)
    assert error.details == "ERROR: CA certificate has expired"


def test_the_helper_declares_the_version_this_side_ships() -> None:
    """Both halves of the protocol carry the number, so they can drift.

    The platform signs and sends the file in libexec/; if its HELPER_VERSION
    and the constant here disagree, a probe would report a version this side
    never expected.
    """
    helper = (REPO_ROOT / "libexec" / "prtg-nats-probe-helper").read_text(
        encoding="utf-8"
    )
    declared = [
        line for line in helper.splitlines() if line.startswith("HELPER_VERSION=")
    ]

    assert declared == [f"HELPER_VERSION={CURRENT_HELPER_VERSION}"]
