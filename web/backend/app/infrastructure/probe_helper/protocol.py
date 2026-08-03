"""The wire format of the probe management channel.

Every enrolled probe carries ``libexec/prtg-nats-probe-helper`` behind an SSH
forced command. It reads exactly one tab-separated request line, optionally
followed by payload on stdin, and answers with a line-oriented response.

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
# whenever a request is added that something here relies on. A helper from
# before the number existed reports none at all and counts as older than any
# of these.
CURRENT_HELPER_VERSION = 1
MINIMUM_HELPER_VERSION = 1

# What the helper answers to a request it does not know. Recognised verbatim
# because it is the one refusal that says "the probe is behind" rather than
# "the request was wrong", and it deserves its own error.
UNSUPPORTED_REQUEST_MESSAGE = "Unsupported management request"


class HelperCommand(StrEnum):
    """Every request the helper accepts.

    Mirrors the dispatch in libexec/prtg-nats-probe-helper. Adding one here
    without adding it there produces "Unsupported management request", which is
    exactly the failure mode we want: loud and immediate.
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
    SENSOR_ACTIVATE = "sensor-activate"
    SENSOR_ROLLBACK = "sensor-rollback"
    SENSOR_COMMIT = "sensor-commit"
    SENSOR_LIST = "sensor-list"
    SENSOR_PREPARE = "sensor-prepare"
    SENSOR_REMOVE = "sensor-remove"
    SENSOR_RESERVE_INTERFACE = "sensor-reserve-interface"
    SENSOR_RELEASE_INTERFACE = "sensor-release-interface"
    SENSOR_WRITE_PROFILE = "sensor-write-profile"
    SENSOR_REMOVE_PROFILE = "sensor-remove-profile"

    HELPER_UPDATE = "helper-update"
    MPP_UNINSTALL = "mpp-uninstall"
    UNENROLL = "unenroll"


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
