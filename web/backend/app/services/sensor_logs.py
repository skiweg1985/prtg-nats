"""Sensor logs: what probes send over their return channel, kept as files.

Records are measurement output, not management state, so they live in the
runtime volume as JSON Lines - one file per probe, sensor and day - where the
shell tooling and the backups already look (ADR 0002, ADR 0013). Reading them
back is a scan over the day files a query covers. At the volumes one platform
sees that is cheap; an index, if one is ever needed, is a cache next to the
files and never a replacement for them.

One process serves the API (ADR 0004), so a lock in memory is enough to keep
two batches from the same probe interleaving their checks and their appends.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.infrastructure.runtime_files import NATS_USERNAME_PATTERN
from app.services.events import EventBroadcaster, StreamEvent, sensor_log_topic

logger = get_logger(__name__)

# A sensor name becomes a directory name. Narrower than anything a sensor in
# this repository is called, so a crafted name cannot climb out of the store.
SENSOR_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

RECORD_RESULTS = ("ok", "error", "gap")

DAY_FILE_SUFFIX = ".jsonl"

# Serialises appends. Held across the duplicate check and the write, which is
# what makes resubmitting a batch idempotent rather than usually idempotent.
_write_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class SensorLogFilter:
    since: datetime
    until: datetime
    probe: str | None = None
    sensor: str | None = None
    ssid: str | None = None
    result: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class SensorLogPage:
    items: list[dict[str, Any]]
    total: int


@dataclass(frozen=True, slots=True)
class SensorLogSegment:
    """Consecutive runs of one stream that ended the same way.

    Same result and same code: an error that changes its code is a different
    problem, and folding it into the one before would hide that.
    """

    result: str
    code: str | None
    count: int
    first: dict[str, Any]
    last: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SensorLogStream:
    """The runs of one sensor for one network, folded into segments."""

    sensor: str
    ssid: str | None
    total: int
    results: dict[str, int]
    segments: list[SensorLogSegment]
    # Older segments past the ones returned; reachable in the full log.
    more_segments: int


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    stored: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)


def format_time(value: datetime) -> str:
    """UTC with a Z, the one spelling every stored record uses."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# How far ahead of this host a probe's clock may run before its record is
# filed under the time it arrived instead. Further ahead, the day file would
# outlive the retention and stay out of every default view.
CLOCK_SKEW_ALLOWED = timedelta(hours=1)


def record_time(value: datetime, now: datetime) -> str:
    """The stored time of an incoming record, kept to what can be filed.

    A record is never refused for its time: the probe drops a refused batch
    whole, and one bad clock would cost every record beside it.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    try:
        stored = format_time(value)
    except OverflowError:
        return format_time(now)
    if parse_time(stored) is None or value - now > CLOCK_SKEW_ALLOWED:
        return format_time(now)
    return stored


def parse_time(value: object) -> datetime | None:
    """An ISO 8601 time as an aware datetime, UTC if it named no zone.

    None for anything else, so a stored line with a broken time is skipped
    rather than failing the whole read.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def summary_of(entry: dict[str, Any]) -> dict[str, Any]:
    """A record without its body, plus the network it was about.

    What a list shows. The raw lines can run to kilobytes per record, and a
    page lists many; the detail route hands out the body of the one opened.
    """
    body = entry.get("body")
    ssid = body.get("ssid") if isinstance(body, dict) else None
    summary = {key: value for key, value in entry.items() if key != "body"}
    summary["ssid"] = ssid if isinstance(ssid, str) else None
    return summary


class SensorLogStore:
    def __init__(self, settings: Settings) -> None:
        self._root = settings.sensor_log_dir
        self._retention_days = settings.sensor_log_retention_days

    # --- Writing ------------------------------------------------------------

    def append(self, probe: str, records: Sequence[dict[str, Any]]) -> IngestOutcome:
        """Store what is new, skip what is already there.

        A probe drops a record from its buffer only once a batch holding it was
        acknowledged, so after a lost answer it sends the same records again.
        The record ID is set on the probe and the record's time never changes,
        so a record already stored sits in exactly one day file: the one it
        would be written to now.

        ``probe`` comes from the transport, never from the records.
        """
        if not NATS_USERNAME_PATTERN.match(probe):
            raise ValueError(f"not a probe identity: {probe!r}")
        stored: list[dict[str, Any]] = []
        duplicates: list[str] = []
        with _write_lock:
            known: dict[Path, set[str]] = {}
            pending: dict[Path, list[dict[str, Any]]] = {}
            for raw in records:
                entry = {**raw, "probe": probe}
                when = parse_time(entry.get("time"))
                sensor = entry.get("sensor")
                if when is None or not isinstance(sensor, str):
                    raise ValueError("record without a time or a sensor")
                if not SENSOR_NAME_PATTERN.match(sensor):
                    raise ValueError(f"not a sensor name: {sensor!r}")
                entry["time"] = format_time(when)
                path = self._day_file(probe, sensor, when.astimezone(UTC).date())
                ids = known.get(path)
                if ids is None:
                    ids = {str(item.get("id")) for item in self._read_file(path)}
                    known[path] = ids
                record_id = str(entry["id"])
                if record_id in ids:
                    duplicates.append(record_id)
                    continue
                ids.add(record_id)
                pending.setdefault(path, []).append(entry)
                stored.append(entry)
            for path, entries in pending.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    for entry in entries:
                        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return IngestOutcome(stored=stored, duplicates=duplicates)

    # --- Reading ------------------------------------------------------------

    def query(
        self, filters: SensorLogFilter, *, limit: int, offset: int = 0
    ) -> SensorLogPage:
        """Matching records, newest first, one page of them."""
        needle = filters.text.casefold() if filters.text else None
        matches: list[tuple[datetime, dict[str, Any]]] = []
        for path in self._day_files(filters):
            for entry in self._read_file(path):
                when = parse_time(entry.get("time"))
                if when is None or not filters.since <= when <= filters.until:
                    continue
                if filters.result and entry.get("result") != filters.result:
                    continue
                body = entry.get("body")
                if filters.ssid and not (
                    isinstance(body, dict) and body.get("ssid") == filters.ssid
                ):
                    continue
                if needle and needle not in _searchable(entry):
                    continue
                matches.append((when, entry))
        matches.sort(key=lambda item: (item[0], str(item[1].get("id"))), reverse=True)
        page = [summary_of(entry) for _, entry in matches[offset : offset + limit]]
        return SensorLogPage(items=page, total=len(matches))

    def streams(
        self, filters: SensorLogFilter, *, segments: int
    ) -> list[SensorLogStream]:
        """Every sensor and network in the window, newest segment first.

        A sensor that runs every minute produced over a thousand rows a day,
        and listing them buried the one failure in between. Folding runs that
        ended the same way keeps the failure as its own line and costs a
        steady stream one line. Folded here rather than in the browser so the
        page does not have to download a day of runs to show a dozen lines.
        """
        page = self.query(filters, limit=sys.maxsize)
        grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        for item in page.items:
            key = (str(item.get("sensor")), item.get("ssid"))
            grouped.setdefault(key, []).append(item)

        out: list[SensorLogStream] = []
        for (sensor, ssid), items in grouped.items():
            folded: list[SensorLogSegment] = []
            results: dict[str, int] = {}
            for item in items:
                result = str(item.get("result"))
                code = item.get("code")
                results[result] = results.get(result, 0) + 1
                current = folded[-1] if folded else None
                if current and current.result == result and current.code == code:
                    # Newest first, so each further run is an older one.
                    folded[-1] = SensorLogSegment(
                        result=result,
                        code=code,
                        count=current.count + 1,
                        first=item,
                        last=current.last,
                    )
                else:
                    folded.append(
                        SensorLogSegment(
                            result=result, code=code, count=1, first=item, last=item
                        )
                    )
            out.append(
                SensorLogStream(
                    sensor=sensor,
                    ssid=ssid,
                    total=len(items),
                    results=results,
                    segments=folded[:segments],
                    more_segments=max(len(folded) - segments, 0),
                )
            )
        out.sort(key=lambda stream: (stream.sensor, stream.ssid or ""))
        return out

    def get(self, record_id: str) -> dict[str, Any] | None:
        """One record by ID, wherever it is.

        A scan over every file. The ID alone says nothing about probe, sensor
        or day, and a link to a run has to keep working without them.
        """
        for path in self._all_day_files():
            for entry in self._read_file(path):
                if str(entry.get("id")) == record_id:
                    return entry
        return None

    # --- Retention ----------------------------------------------------------

    def remove_expired(self, today: date | None = None) -> int:
        """Delete day files older than the retention, and the empty folders.

        Judged by the date in the file name, not the file's mtime: a day file
        keeps growing while a buffered probe catches up, and what it holds is
        that day's records however late they arrived. Today counts as one of
        the days kept, so a retention of 1 keeps today's file alone.
        """
        today = today or datetime.now(UTC).date()
        cutoff = today - timedelta(days=self._retention_days - 1)
        removed = 0
        with _write_lock:
            for path in self._all_day_files():
                day = _day_of(path)
                if day is not None and day < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            if self._root.is_dir():
                for probe_dir in self._root.iterdir():
                    if not probe_dir.is_dir():
                        continue
                    for sensor_dir in probe_dir.iterdir():
                        if sensor_dir.is_dir() and not any(sensor_dir.iterdir()):
                            sensor_dir.rmdir()
                    if not any(probe_dir.iterdir()):
                        probe_dir.rmdir()
        return removed

    # --- Files --------------------------------------------------------------

    def _day_file(self, probe: str, sensor: str, day: date) -> Path:
        return self._root / probe / sensor / f"{day.isoformat()}{DAY_FILE_SUFFIX}"

    def _all_day_files(self) -> Iterator[Path]:
        if not self._root.is_dir():
            return
        yield from sorted(self._root.glob(f"*/*/*{DAY_FILE_SUFFIX}"))

    def _day_files(self, filters: SensorLogFilter) -> Iterator[Path]:
        """Only the files a query can match, decided from their path."""
        first = filters.since.astimezone(UTC).date()
        last = filters.until.astimezone(UTC).date()
        for path in self._all_day_files():
            sensor_dir = path.parent
            if filters.sensor and sensor_dir.name != filters.sensor:
                continue
            if filters.probe and sensor_dir.parent.name != filters.probe:
                continue
            day = _day_of(path)
            if day is not None and first <= day <= last:
                yield path

    @staticmethod
    def _read_file(path: Path) -> Iterator[dict[str, Any]]:
        try:
            handle = path.open(encoding="utf-8")
        except FileNotFoundError:
            return
        with handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # A line cut short by a full disk or a crash mid-write.
                    # Losing it is better than losing the whole day with it.
                    logger.warning(
                        "skipping an unreadable sensor log line",
                        extra={"file": str(path), "line": number},
                    )
                    continue
                if isinstance(entry, dict):
                    yield entry


def _day_of(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.name.removesuffix(DAY_FILE_SUFFIX))
    except ValueError:
        return None


def _searchable(entry: dict[str, Any]) -> str:
    """Summary, code and every value in the body, one per line.

    The values as the sensor wrote them: searching the serialised body would
    match field names and JSON punctuation, and miss a quote or backslash in
    raw output, which the serialisation escapes.
    """
    parts = [
        str(entry.get("summary") or ""),
        str(entry.get("code") or ""),
        *_values(entry.get("body")),
    ]
    return "\n".join(parts).casefold()


def _values(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
    elif value is not None:
        yield str(value)


async def ingest(
    store: SensorLogStore,
    broadcaster: EventBroadcaster,
    probe: str,
    records: Sequence[dict[str, Any]],
) -> IngestOutcome:
    """Store a batch and announce what was new to every open page."""
    outcome = await asyncio.to_thread(store.append, probe, records)
    for entry in outcome.stored:
        await broadcaster.publish(
            StreamEvent(
                topic=sensor_log_topic(),
                kind="sensor_log.record",
                payload=summary_of(entry),
            )
        )
    return outcome
