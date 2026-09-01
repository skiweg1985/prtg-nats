"""The wire format between a watching probe and this platform, and the rule
that turns a stream of measurements into a history.

Two subjects, both under ``prtg-nats.watch``:

``prtg-nats.watch.targets.<account>``
    Request/reply. The sensor asks what it should measure, the platform
    answers with the device list. Asked at the start of every run, so a
    device added in the interface is measured on the next scan without a
    rollout.

``prtg-nats.watch.report.<account>``
    Publish. The sensor reports what it measured. Results carry their own
    timestamps and the sensor may send several per device, because it keeps
    what it could not deliver and sends it with the next report - which is
    what makes a restart of this platform cost nothing.

Nothing in this module touches the database or a socket. The state rule
below is the whole reason the feature works, so it is a function over
values that a test can run a thousand times in a millisecond.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import WatchCheckMethod, WatchState

SUBJECT_PREFIX = "prtg-nats.watch"
# The account is appended, so one subscription with a wildcard reads every
# probe and the account in the subject is what a report is checked against.
REPORT_SUBJECT = f"{SUBJECT_PREFIX}.report"
TARGETS_SUBJECT = f"{SUBJECT_PREFIX}.targets"
REPORT_WILDCARD = f"{REPORT_SUBJECT}.*"
TARGETS_WILDCARD = f"{TARGETS_SUBJECT}.*"

# Raised together whenever the meaning of a field changes. The sensor sends
# its own number, and a report from a version this platform does not know is
# dropped rather than guessed at.
PROTOCOL_VERSION = 1

# A report larger than this is refused before it is parsed. 512 KiB is around
# 2000 results, an order of magnitude above what a probe with a full device
# list produces in one run.
MAX_REPORT_BYTES = 512 * 1024


class WatchProtocolError(ValueError):
    """A message that does not follow the format above."""


@dataclass(frozen=True, slots=True)
class WatchTarget:
    """One device as the sensor needs to see it."""

    device_id: str
    address: str
    method: WatchCheckMethod
    port: int | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "address": self.address,
            "method": str(self.method),
            "port": self.port,
        }


@dataclass(frozen=True, slots=True)
class WatchTargetList:
    """The answer to a targets request.

    ``revision`` is the fingerprint of the list. The sensor sends the one it
    holds; an unchanged list comes back as ``unchanged`` with no devices,
    which keeps the common case a few hundred bytes instead of a few hundred
    kilobytes.
    """

    revision: str
    targets: tuple[WatchTarget, ...]
    unchanged: bool = False
    timeout_ms: int = 1500
    # What the platform considers a fresh measurement. The sensor does not
    # schedule itself - PRTG does that - but it needs the number to decide
    # how much undelivered history is still worth sending.
    stale_after_seconds: int = 300

    def to_wire(self) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "revision": self.revision,
            "unchanged": self.unchanged,
            "timeout_ms": self.timeout_ms,
            "stale_after_seconds": self.stale_after_seconds,
            "targets": [target.to_wire() for target in self.targets],
        }

    @classmethod
    def from_wire(cls, payload: bytes) -> WatchTargetList:
        document = _load(payload)
        _require_version(document)
        targets = []
        for entry in _as_list(document.get("targets"), "targets"):
            try:
                method = WatchCheckMethod(str(entry["method"]))
            except (KeyError, ValueError) as error:
                raise WatchProtocolError(f"unusable method: {error}") from error
            port = entry.get("port")
            targets.append(
                WatchTarget(
                    device_id=_as_text(entry.get("device_id"), "device_id"),
                    address=_as_text(entry.get("address"), "address"),
                    method=method,
                    port=int(port) if port is not None else None,
                )
            )
        return cls(
            revision=_as_text(document.get("revision"), "revision"),
            targets=tuple(targets),
            unchanged=bool(document.get("unchanged", False)),
            timeout_ms=int(document.get("timeout_ms", 1500)),
            stale_after_seconds=int(document.get("stale_after_seconds", 300)),
        )


@dataclass(frozen=True, slots=True)
class WatchResult:
    """One measurement of one device at one point in time."""

    device_id: str
    measured_at: datetime
    reachable: bool
    rtt_ms: float | None = None
    resolved_address: str | None = None
    error: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "at": self.measured_at.astimezone(UTC).isoformat(),
            "ok": self.reachable,
            "rtt_ms": self.rtt_ms,
            "address": self.resolved_address,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class WatchReport:
    """What one probe measured in one run, plus whatever it still owed."""

    account: str
    sent_at: datetime
    results: tuple[WatchResult, ...]
    revision: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "account": self.account,
            "sent_at": self.sent_at.astimezone(UTC).isoformat(),
            "revision": self.revision,
            "results": [result.to_wire() for result in self.results],
        }

    @classmethod
    def from_wire(cls, payload: bytes) -> WatchReport:
        if len(payload) > MAX_REPORT_BYTES:
            raise WatchProtocolError(
                f"report of {len(payload)} bytes exceeds {MAX_REPORT_BYTES}"
            )
        document = _load(payload)
        _require_version(document)
        results = []
        for entry in _as_list(document.get("results"), "results"):
            rtt = entry.get("rtt_ms")
            results.append(
                WatchResult(
                    device_id=_as_text(entry.get("device_id"), "device_id"),
                    measured_at=_as_time(entry.get("at"), "at"),
                    reachable=bool(entry.get("ok")),
                    rtt_ms=float(rtt) if rtt is not None else None,
                    resolved_address=_as_optional_text(entry.get("address")),
                    error=_as_optional_text(entry.get("error")),
                )
            )
        return cls(
            account=_as_text(document.get("account"), "account"),
            sent_at=_as_time(document.get("sent_at"), "sent_at"),
            results=tuple(sorted(results, key=lambda result: result.measured_at)),
            revision=str(document.get("revision", "")),
        )


@dataclass(frozen=True, slots=True)
class WatchDecision:
    """What one measurement makes of the state so far."""

    state: WatchState
    consecutive_failures: int


def next_state(
    *,
    current: WatchState,
    consecutive_failures: int,
    reachable: bool,
    failure_threshold: int,
) -> WatchDecision:
    """Fold one measurement into the running state.

    The threshold is the whole subtlety. A card terminal drops the odd echo
    request without anybody noticing at the till, and a history that records
    each one as an outage is a history nobody reads. So a failure counts up
    but does not flip the state until it has happened ``failure_threshold``
    times in a row, while a single success clears the count immediately -
    the device answered, and that is not in doubt.

    An unmeasured device turning out to be reachable becomes ``UP`` at once:
    ``UNKNOWN`` says nobody looked, so the first look settles it.
    """
    if reachable:
        return WatchDecision(state=WatchState.UP, consecutive_failures=0)

    failures = consecutive_failures + 1
    if failures >= max(1, failure_threshold):
        return WatchDecision(state=WatchState.DOWN, consecutive_failures=failures)
    # Below the threshold the state stands - including UNKNOWN, which must not
    # become UP just because something answered badly.
    held = WatchState.UP if current is WatchState.UP else current
    return WatchDecision(state=held, consecutive_failures=failures)


def latency_bucket_start(moment: datetime, *, minutes: int = 5) -> datetime:
    """Round down to the bucket a measurement belongs in."""
    moment = moment.astimezone(UTC)
    return moment.replace(
        minute=(moment.minute // minutes) * minutes, second=0, microsecond=0
    )


def _load(payload: bytes) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise WatchProtocolError(f"payload is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise WatchProtocolError("payload is not an object")
    return document


def _require_version(document: dict[str, Any]) -> None:
    version = document.get("version")
    if version != PROTOCOL_VERSION:
        raise WatchProtocolError(
            f"protocol version {version!r}, expected {PROTOCOL_VERSION}"
        )


def _as_list(value: Any, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WatchProtocolError(f"{field} is not a list")
    for entry in value:
        if not isinstance(entry, dict):
            raise WatchProtocolError(f"{field} holds something that is not an object")
    return value


def _as_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise WatchProtocolError(f"{field} is missing or not a string")
    return value


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    # Bounded here rather than at the column, so a probe sending nonsense
    # cannot fail the whole report on a database error.
    return text[:255] if text else None


def _as_time(value: Any, field: str) -> datetime:
    try:
        moment = datetime.fromisoformat(_as_text(value, field))
    except ValueError as error:
        raise WatchProtocolError(f"{field} is not a timestamp: {error}") from error
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
