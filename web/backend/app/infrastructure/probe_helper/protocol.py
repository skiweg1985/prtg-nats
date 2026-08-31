"""The wire format of the management channel.

Every enrolled probe carries ``libexec/prtg-nats-probe-helper`` behind an SSH
forced command, and every enrolled iperf endpoint carries
``libexec/prtg-nats-iperf-helper`` the same way. Both read exactly one
tab-separated request line, optionally followed by payload on stdin, and answer
with a line-oriented response - one format, two vocabularies.

This module owns the format and nothing else - no transport, no I/O - so the
parsing rules are testable against captured fixtures without a probe in sight.

Request::

    <command>\\t<arg1>\\t<arg2>\\t<arg3>\\n
    [payload on stdin]

Response::

    OK <command>
    key=value
    ...

or, for the list-shaped answers::

    OK sensor-list
    <name>\\tversion=<v>\\tsha256=<h>\\tinterfaces=<i>\\thelper=<state>

A failure is reported by a non-zero exit status with the reason on stderr.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.errors import ProbeProtocolError

# The tab is the field separator of the protocol, so no argument may contain
# one. The helper validates its own tokens as well; this is the near-side half
# of that agreement.
FIELD_SEPARATOR = "\t"

# HELPER_VERSION in libexec/prtg-nats-probe-helper, and the lowest one this
# platform is willing to talk to. Raised together with the helper's own number
# whenever a request is added, or an answer changes its meaning, in a way
# something here relies on. A helper from before the number existed reports
# none at all and counts as older than any of these.
#
# Version 2 answers sensor-list with the checksum of the catalogue file rather
# than of the rewritten one. Version 1 still speaks the same protocol, so the
# minimum stays where it is - it merely reports every sensor with dependencies
# as modified, which an offered helper update resolves.
#
# Version 3 restores only what an activation actually replaced. Before it, a
# sensor-rollback for a transaction that failed while staging deleted the
# installed sensor it had not touched yet - a failed update took the working
# version with it. The protocol is unchanged, so the minimum stays at 1, but a
# probe below 3 should be updated before the next rollout.
#
# Version 4 adds sensor-write-file and sensor-remove-file, so a variant can
# carry a certificate or a key and not only KEY=VALUE lines. The minimum stays
# at 1 because everything else is unchanged: a probe below 4 takes settings and
# credentials as before and refuses only the files, which is reported as the
# helper being behind rather than as a broken request.
# Version 5 adds wireless-interfaces, so the platform can offer the radio
# interfaces of a probe instead of asking somebody to type a name. The minimum
# stays at 1: a probe below 5 refuses the request as unknown, which reads as
# the helper being behind, and reserving still works from the shell.
# Version 6 adds helper_sha256 to sensor-list, so a privileged helper changed
# on the probe becomes visible. Before it, only the sensor script carried a
# digest, and a helper edited in place read as current. The minimum stays at
# 1: a probe below 6 omits the field, and an absent digest is not a deviation.
# Version 7 adds the exact userspace platform to probe-info and the signed
# sensor-tool-stage request. sensor-list reports the active managed tool. A
# rollout that needs one updates the helper before it stages any sensor state.
# Version 8 makes sensor activation, commit, rollback, explicit recovery and
# removal mutually exclusive per sensor. Active transaction markers make
# retries idempotent and prevent an old rollback from replacing a newer
# deployment. sensor-list also reports an unstartable tool as incompatible
# instead of failing the request.
#
# Version 9 adds the overlay: overlay-configure, overlay-info, overlay-remove
# and access-source, and probe-info reports the overlay fields alongside the
# rest. The minimum stays at 1 - a probe below 9 refuses the four requests as
# unknown and omits the fields, which reads as the helper being behind. It is
# also the version that makes putting a probe on the overlay possible at all,
# so the platform offers the update before it tries.
CURRENT_HELPER_VERSION = 9
MINIMUM_HELPER_VERSION = 1
# The overlay requests arrived with version 9. Asked for separately from the
# current version so a probe that is merely a version or two behind is not
# told it cannot be put on the overlay when it can.
OVERLAY_HELPER_VERSION = 9

# Where a variant's files land on the probe. The helper builds the same path
# from its own validated tokens - this side only needs to know it, because the
# path is what goes into the profile as the value the sensor script reads.
PROBE_SENSOR_CONFIG_ROOT = "/etc/prtg-nats/sensors"


def probe_profile_file_path(sensor: str, profile: str, filename: str) -> str:
    """The absolute path a deployed file of one variant has on the probe."""
    return f"{PROBE_SENSOR_CONFIG_ROOT}/{sensor}/files/{profile}/{filename}"


# What the helper answers to a request it does not know. Recognised verbatim
# because it is the one refusal that says "the probe is behind" rather than
# "the request was wrong", and it deserves its own error.
UNSUPPORTED_REQUEST_MESSAGE = "Unsupported management request"


class HelperCommand(StrEnum):
    """Every request either helper accepts.

    Mirrors the dispatch in libexec/prtg-nats-probe-helper and, for the
    endpoint block at the bottom, in libexec/prtg-nats-iperf-helper. Adding one
    here without adding it there produces "Unsupported management request",
    which is exactly the failure mode we want: loud and immediate.

    One enum for both because the wire format is one format. Which host
    understands which request is decided by the client that sends it - an
    endpoint has no sensors and a probe has no iperf3 service, so a request
    aimed at the wrong one is refused on arrival rather than half executed.
    """

    STATUS = "status"
    PROBE_INFO = "probe-info"
    STAGE = "stage"
    WRITE_CONFIG = "write-config"
    INSTALL_CA = "install-ca"
    ACTIVATE = "activate"
    ROLLBACK = "rollback"
    COMMIT = "commit"

    SENSOR_STAGE = "sensor-stage"
    SENSOR_TOOL_STAGE = "sensor-tool-stage"
    SENSOR_ACTIVATE = "sensor-activate"
    SENSOR_ROLLBACK = "sensor-rollback"
    SENSOR_RECOVER = "sensor-recover"
    SENSOR_COMMIT = "sensor-commit"
    SENSOR_LIST = "sensor-list"
    SENSOR_PREPARE = "sensor-prepare"
    SENSOR_REMOVE = "sensor-remove"
    WIRELESS_INTERFACES = "wireless-interfaces"
    SENSOR_RESERVE_INTERFACE = "sensor-reserve-interface"
    SENSOR_RELEASE_INTERFACE = "sensor-release-interface"
    SENSOR_WRITE_PROFILE = "sensor-write-profile"
    SENSOR_REMOVE_PROFILE = "sensor-remove-profile"
    SENSOR_WRITE_FILE = "sensor-write-file"
    SENSOR_REMOVE_FILE = "sensor-remove-file"

    OVERLAY_CONFIGURE = "overlay-configure"
    OVERLAY_INFO = "overlay-info"
    OVERLAY_REMOVE = "overlay-remove"
    # Rewrites the from= clause of the management key. Its own request rather
    # than part of overlay-configure: a probe that leaves the overlay has to
    # keep the address the platform reaches it from today, and that is the
    # same operation in the other direction.
    ACCESS_SOURCE = "access-source"

    HELPER_UPDATE = "helper-update"
    MPP_UNINSTALL = "mpp-uninstall"
    # Spoken by both helpers, and meaning the same thing on both: remove the
    # management access from the host side. It is always the last request a
    # channel carries.
    UNENROLL = "unenroll"

    # --- iperf measurement endpoints ---------------------------------------
    ENDPOINT_INFO = "endpoint-info"
    ENDPOINT_SETUP = "endpoint-setup"
    ENDPOINT_REMOVE = "endpoint-remove"


@dataclass(frozen=True, slots=True)
class HelperRequest:
    command: HelperCommand
    arguments: tuple[str, ...] = ()
    # Sent on stdin after the request line. Used for the staged password and
    # for file content, so neither ever appears in a process list.
    payload: str | None = None

    def __post_init__(self) -> None:
        if len(self.arguments) > 3:
            raise ProbeProtocolError(
                params={"command": self.command.value},
                details=(
                    f"helper accepts at most 3 arguments, got {len(self.arguments)}"
                ),
            )
        for argument in self.arguments:
            if FIELD_SEPARATOR in argument or "\n" in argument:
                raise ProbeProtocolError(
                    params={"command": self.command.value},
                    details="argument contains a tab or newline",
                )

    def encode(self) -> str:
        parts = [self.command.value, *self.arguments]
        return FIELD_SEPARATOR.join(parts) + "\n"


@dataclass(frozen=True, slots=True)
class HelperResponse:
    command: str
    # Leading "OK <command>" may carry trailing key=value pairs, e.g.
    # "OK active config=/etc/paessler/mpprobe/config.yaml".
    header_fields: dict[str, str] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)
    records: tuple[dict[str, str], ...] = ()
    raw: str = ""

    def value(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def required(self, key: str) -> str:
        try:
            return self.values[key]
        except KeyError as exc:
            raise ProbeProtocolError(
                params={"command": self.command, "field": key},
                details=f"missing field {key!r} in helper response",
            ) from exc


def parse_response(raw: str, *, expected: HelperCommand) -> HelperResponse:
    """Turn helper output into a structured response.

    Tolerant about what it does not know - an older helper simply reports fewer
    keys - and strict about the frame: a response that does not start with
    ``OK`` is a protocol error, never an empty result.
    """
    lines = raw.strip().splitlines()
    if not lines:
        raise ProbeProtocolError(
            params={"command": expected.value}, details="empty helper response"
        )

    header = lines[0].strip()
    if not header.startswith("OK"):
        raise ProbeProtocolError(
            params={"command": expected.value},
            details=f"unexpected helper response header: {header[:200]}",
        )

    header_parts = header.split()[1:]
    command = expected.value
    header_fields: dict[str, str] = {}
    for index, part in enumerate(header_parts):
        if "=" in part:
            key, _, value = part.partition("=")
            header_fields[key] = value
        elif index == 0:
            # "OK probe-info" repeats the command; "OK active" reports state.
            command = part

    values: dict[str, str] = {}
    records: list[dict[str, str]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        if FIELD_SEPARATOR in line:
            records.append(_parse_record(line))
        elif "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
        # Anything else is output we do not model; it stays in `raw`.

    return HelperResponse(
        command=command,
        header_fields=header_fields,
        values=values,
        records=tuple(records),
        raw=raw,
    )


def _parse_record(line: str) -> dict[str, str]:
    """A TSV record: first column is the name, the rest are key=value."""
    columns = line.split(FIELD_SEPARATOR)
    record: dict[str, str] = {"name": columns[0].strip()}
    for column in columns[1:]:
        if "=" not in column:
            continue
        key, _, value = column.partition("=")
        record[key.strip()] = value.strip()
    return record


def normalise_optional(value: str | None) -> str | None:
    """The helper writes the literal ``none`` where a value is absent."""
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in {"", "none", "unknown"} else stripped
