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
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from app.core.config import Settings
from app.core.logging import get_logger
from app.infrastructure.runtime_files import NATS_USERNAME_PATTERN
from app.services.events import EventBroadcaster, StreamEvent, sensor_log_topic

logger = get_logger(__name__)

# A sensor name becomes a directory name. Narrower than anything a sensor in
# this repository is called, so a crafted name cannot climb out of the store.
SENSOR_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

RECORD_RESULTS = ("ok", "error", "gap")

TimelineState = Literal["none", "ok", "outlier", "failing"]

DAY_FILE_SUFFIX = ".jsonl"

# How far past a stream's usual interval an empty timeline bucket still
# counts as covered by the run before it. Half an interval of slack keeps a
# run that came a little late from leaving a grey sliver.
COVERAGE_FACTOR = 1.5

# When a network's runs count as ended: no run for this many of its usual
# intervals while the sensor's other networks went on. Matches the probe,
# which stops measuring a job after three intervals unasked. The minimum
# gap is the probe's own floor of one measurement a minute.
ENDED_INTERVALS = 3
ENDED_MINIMUM_GAP = 60.0

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
    # The configuration of a network the sensor names with --profile. Only
    # records of that variant; None leaves the variant out of the filter.
    variant: str | None = None
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
class TimelineBucket:
    """The runs of one stream in one slice of the timeline.

    ``state`` is what the slice looks like at a glance: ``none`` without a
    run, ``ok`` when every run passed, ``outlier`` when its only failures
    were single runs between passing ones, ``failing`` when a failure lasted
    more than one run. ``code`` is the most frequent error code in it.
    """

    start: datetime
    ok: int
    error: int
    state: TimelineState
    code: str | None


@dataclass(frozen=True, slots=True)
class TimelineStream:
    """One sensor's runs for one network and variant, sliced into buckets."""

    sensor: str
    ssid: str | None
    ok: int
    error: int
    # Passed runs over every judged run; None without a judged run at all.
    success_rate: float | None
    # Runs per hour over the buckets the runs cover, so a probe that was
    # offline half the day does not read as measuring half as often.
    runs_per_hour: float | None
    last_failure: dict[str, Any] | None
    buckets: list[TimelineBucket]
    last_run: datetime | None = None
    # No run for three of its usual intervals while another network of the
    # same sensor kept running: a configuration PRTG no longer asks for.
    ended: bool = False
    # Two configurations of one SSID - PEAP and an EAP-TLS variant, say -
    # are rows of their own, or their success rates would mix. None for the
    # runs that named no variant.
    variant: str | None = None


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
    """A record without its body, plus the network and variant it was about.

    What a list shows. The raw lines can run to kilobytes per record, and a
    page lists many; the detail route hands out the body of the one opened.
    """
    summary = {key: value for key, value in entry.items() if key != "body"}
    summary["ssid"] = _body_text(entry, "ssid")
    summary["variant"] = variant_of(entry)
    return summary


def variant_of(entry: dict[str, Any]) -> str | None:
    """The variant a record was measured with, or None.

    wlan-auth writes the name it was given with --profile into the body as
    ``profile``; older records and runs without one have none.
    """
    return _body_text(entry, "profile")


def _body_text(entry: dict[str, Any], key: str) -> str | None:
    body = entry.get("body")
    value = body.get(key) if isinstance(body, dict) else None
    return value if isinstance(value, str) and value else None


# What a timeline row is: one sensor, one network, one variant.
StreamKey = tuple[str, str | None, str | None]


def stream_key(item: dict[str, Any]) -> StreamKey:
    """The timeline row a summary belongs to.

    Runs without a variant stay in the network's plain row, which is where
    every run went before the variant was recorded.
    """
    return (str(item.get("sensor")), item.get("ssid"), item.get("variant"))


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
                if filters.variant and variant_of(entry) != filters.variant:
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

    def timeline(
        self, filters: SensorLogFilter, *, bucket: timedelta
    ) -> tuple[datetime, list[TimelineStream]]:
        """Runs per sensor and network, sliced into buckets of equal size.

        Narrowed to one sensor by the filter for a sensor's page; without one,
        every sensor of the probe for the overview on the probe page.

        Buckets are aligned to multiples of their size, not to ``since``, so
        a page that refreshes a minute later gets the same slices and the bar
        does not shift under the pointer. Returns the start of the first
        bucket with the streams.

        A failed run counts as an outlier when the runs on both sides of it
        passed: a single "no reply" between a hundred good sign-ins is worth
        seeing, not worth a red block. A failure at the newest end has no
        later run to clear it and counts as failing - it is the state now.
        """
        size = bucket.total_seconds()
        first = datetime.fromtimestamp(
            (filters.since.timestamp() // size) * size, tz=UTC
        )
        count = max(int(-(-(filters.until - first).total_seconds() // size)), 1)

        page = self.query(filters, limit=sys.maxsize)
        grouped: dict[StreamKey, list[dict[str, Any]]] = {}
        for item in page.items:
            if item.get("result") not in ("ok", "error"):
                continue
            grouped.setdefault(stream_key(item), []).append(item)

        out: list[TimelineStream] = []
        usual: dict[StreamKey, float] = {}
        for (sensor, ssid, variant), newest_first in grouped.items():
            runs = list(reversed(newest_first))
            oks = [0] * count
            errors = [0] * count
            failing = [False] * count
            # What the newest run in each bucket leaves behind for the empty
            # buckets after it: a lasting failure, or nothing to worry about.
            tail: list[TimelineState | None] = [None] * count
            codes: list[dict[str, int]] = [{} for _ in range(count)]
            for index, item in enumerate(runs):
                when = parse_time(item.get("time"))
                if when is None:
                    continue
                slot = int((when - first).total_seconds() // size)
                if not 0 <= slot < count:
                    continue
                if item.get("result") == "ok":
                    oks[slot] += 1
                    tail[slot] = "ok"
                    continue
                errors[slot] += 1
                code = str(item.get("code") or "")
                codes[slot][code] = codes[slot].get(code, 0) + 1
                before = runs[index - 1] if index > 0 else None
                after = runs[index + 1] if index + 1 < len(runs) else None
                isolated = (
                    after is not None
                    and after.get("result") == "ok"
                    and (before is None or before.get("result") == "ok")
                )
                if not isolated:
                    failing[slot] = True
                tail[slot] = "ok" if isolated else "failing"

            # A sensor that runs every half hour leaves most 15-minute buckets
            # without a run of their own. Those are not silence: a bucket
            # still within the stream's usual interval of the run before it
            # carries that run's state, and only a longer pause shows as none.
            times = [t for item in runs if (t := parse_time(item.get("time")))]
            gaps = sorted(
                (later - earlier).total_seconds() for earlier, later in pairwise(times)
            )
            reach = gaps[len(gaps) // 2] * COVERAGE_FACTOR if gaps else 0.0
            newest: list[datetime | None] = [None] * count
            for when in times:
                slot = int((when - first).total_seconds() // size)
                if 0 <= slot < count:
                    newest[slot] = when

            buckets: list[TimelineBucket] = []
            last_run: datetime | None = None
            last_state: TimelineState = "none"
            for slot in range(count):
                start = first + bucket * slot
                state: TimelineState
                if oks[slot] + errors[slot] == 0:
                    covered = (
                        last_run is not None
                        and (start - last_run).total_seconds() < reach
                    )
                    state = last_state if covered else "none"
                elif failing[slot]:
                    state = "failing"
                elif errors[slot]:
                    state = "outlier"
                else:
                    state = "ok"
                common = (
                    max(codes[slot].items(), key=lambda pair: pair[1])[0]
                    if codes[slot]
                    else ""
                )
                if newest[slot] is not None:
                    last_run = newest[slot]
                    last_state = tail[slot] or "ok"
                buckets.append(
                    TimelineBucket(
                        start=start,
                        ok=oks[slot],
                        error=errors[slot],
                        state=state,
                        code=common or None,
                    )
                )

            ok, error = sum(oks), sum(errors)
            active = sum(1 for b in buckets if b.state != "none")
            last_failure = next(
                (item for item in newest_first if item.get("result") == "error"),
                None,
            )
            out.append(
                TimelineStream(
                    sensor=sensor,
                    ssid=ssid,
                    ok=ok,
                    error=error,
                    success_rate=ok / (ok + error) if ok + error else None,
                    runs_per_hour=(ok + error) / (active * size / 3600)
                    if active
                    else None,
                    last_failure=last_failure,
                    buckets=buckets,
                    last_run=times[-1] if times else None,
                    variant=variant,
                )
            )
            usual[(sensor, ssid, variant)] = gaps[len(gaps) // 2] if gaps else 0.0

        # Ended is judged against the sensor's other networks, not the
        # clock: a probe that went offline stops every network at once, and
        # that is not a configuration nobody asks for.
        latest: dict[str, datetime] = {}
        for stream in out:
            if stream.last_run and (
                stream.sensor not in latest or stream.last_run > latest[stream.sensor]
            ):
                latest[stream.sensor] = stream.last_run
        judged = []
        for stream in out:
            gap = max(
                usual[(stream.sensor, stream.ssid, stream.variant)],
                ENDED_MINIMUM_GAP,
            )
            ended = bool(
                stream.last_run
                and (latest[stream.sensor] - stream.last_run).total_seconds()
                > ENDED_INTERVALS * gap
            )
            judged.append(replace(stream, ended=ended))
        judged.sort(
            key=lambda stream: (stream.sensor, stream.ssid or "", stream.variant or "")
        )
        return first, judged

    def retained_since(self, today: date | None = None) -> datetime:
        """The oldest moment the retention still keeps records for."""
        today = today or datetime.now(UTC).date()
        oldest = today - timedelta(days=self._retention_days - 1)
        return datetime(oldest.year, oldest.month, oldest.day, tzinfo=UTC)

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
