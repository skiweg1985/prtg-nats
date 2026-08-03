"""The helper wire format, tested against captured shapes.

These fixtures are what libexec/prtg-nats-probe-helper actually prints. If the
probe side ever changes them, these tests are the first thing that fails - which
is the point.
"""

from __future__ import annotations

import pytest

from app.core.errors import ProbeProtocolError
from app.domain.enums import ServiceState
from app.domain.models import parse_probe_info, parse_sensor_list
from app.infrastructure.probe_helper import (
    HelperCommand,
    HelperRequest,
    normalise_optional,
    parse_response,
)

PROBE_INFO = """OK probe-info
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


def test_sensor_list_records_are_parsed() -> None:
    response = parse_response(SENSOR_LIST, expected=HelperCommand.SENSOR_LIST)
    sensors = parse_sensor_list(response)

    assert [sensor.name for sensor in sensors] == ["internet-speed", "wlan-auth"]
    assert sensors[0].version == "2"
    assert sensors[0].interfaces == ()
    assert sensors[1].interfaces == ("wlan0", "wlan1")
    assert sensors[1].helper_state == "active"
