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
    MINIMUM_HELPER_VERSION,
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
platform=linux-arm64-glibc
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
    "internet-speed\tversion=2\tsha256=aaaa\tinterfaces=\thelper=none\t"
    "tool=iperf3\ttool_version=3.21\ttool_platform=linux-arm64-glibc\t"
    "tool_sha256=cccc\ttool_source=managed\t"
    "tool_path=/opt/prtg-nats/tools/iperf3/3.21/linux-arm64-glibc/iperf3\t"
    "tool_compatible=yes\n"
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
    assert observed.helper_version == MINIMUM_HELPER_VERSION
    assert observed.platform == "linux-arm64-glibc"
    # Supported is not the same as current: this helper can still answer the
    # original management protocol, but the fleet filter must offer its update.
    assert observed.helper_outdated


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
    assert sensors[0].tool_name == "iperf3"
    assert sensors[0].tool_version == "3.21"
    assert sensors[0].tool_platform == "linux-arm64-glibc"
    assert sensors[0].tool_sha256 == "cccc"
    assert sensors[0].tool_source == "managed"
    assert sensors[0].tool_path.endswith("/3.21/linux-arm64-glibc/iperf3")
    assert sensors[0].tool_compatible is True
    assert sensors[1].interfaces == ("wlan0", "wlan1")
    assert sensors[1].helper_state == "active"


# Which helper answers what. One enum describes the wire format, two scripts
# implement it, and this is the only place that says which request belongs to
# which - so a command added to the enum without a home fails here rather than
# on a customer's machine.
ENDPOINT_ONLY = {
    HelperCommand.ENDPOINT_INFO,
    HelperCommand.ENDPOINT_SETUP,
    HelperCommand.ENDPOINT_REMOVE,
}
# Spoken by both, and meaning the same on both: remove the management access.
SHARED = {HelperCommand.UNENROLL}


def _dispatch(helper_name: str) -> str:
    helper = (REPO_ROOT / "libexec" / helper_name).read_text(encoding="utf-8")
    # The newline the split consumed goes back on, so the first case label
    # matches the same way as every other one.
    dispatch = "\n" + helper.partition('\ncase "${command_name}" in\n')[2]
    assert dispatch.strip(), f"{helper_name}'s dispatch moved; this test needs updating"
    return dispatch


def test_every_command_exists_on_the_host_that_answers_it() -> None:
    """A command this side can send that the far side cannot answer is a bug.

    Both helpers reply "Unsupported management request" to anything their
    dispatch does not list, and that failure would surface on a customer's
    machine rather than here.
    """
    probe = _dispatch("prtg-nats-probe-helper")
    endpoint = _dispatch("prtg-nats-iperf-helper")

    missing_on_probe = [
        command.value
        for command in HelperCommand
        if command not in ENDPOINT_ONLY and f"\n  {command.value})\n" not in probe
    ]
    assert not missing_on_probe, f"the probe helper does not handle: {missing_on_probe}"

    missing_on_endpoint = [
        command.value
        for command in HelperCommand
        if command in (ENDPOINT_ONLY | SHARED)
        and f"\n  {command.value})\n" not in endpoint
    ]
    assert not missing_on_endpoint, (
        f"the iperf helper does not handle: {missing_on_endpoint}"
    )


def test_the_endpoint_helper_answers_nothing_a_probe_would() -> None:
    """The endpoint's vocabulary stays four requests wide.

    It reaches a host that usually stands on a public address, so a request
    that grew onto it by accident - a sensor rollout, a configuration write -
    would be rights nobody decided to grant.
    """
    endpoint = _dispatch("prtg-nats-iperf-helper")

    unexpected = [
        command.value
        for command in HelperCommand
        if command not in (ENDPOINT_ONLY | SHARED)
        and f"\n  {command.value})\n" in endpoint
    ]
    assert not unexpected, f"the iperf helper answers more than it should: {unexpected}"


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


def test_a_blocking_sensor_transaction_is_a_structured_refusal_parameter() -> None:
    error = refusal_error(
        "mpp-berlin-01",
        HelperCommand.SENSOR_ACTIVATE,
        "ERROR: Sensor dns-check has an active transaction\n"
        "active_transaction=tx-old\n",
    )

    assert isinstance(error, ProbeRejectedError)
    assert error.params["active_transaction"] == "tx-old"


def test_an_invalid_active_transaction_field_is_not_trusted() -> None:
    error = refusal_error(
        "mpp-berlin-01",
        HelperCommand.SENSOR_ACTIVATE,
        "ERROR: blocked\nactive_transaction=../../foreign\n",
    )

    assert "active_transaction" not in error.params


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
