#!/usr/bin/env python3

"""Script v2 sensor: reachability and connection quality of an uplink.

The sensor measures against several targets at once and reports packet loss,
round-trip time and jitter per target - plus a quality index that condenses
the three quantities into one number.

Targets belong to one of two classes, and that is the actual point compared
to an ordinary ping sensor:

    --target           targets on the internet.
    --internal-target  targets on your own network, say behind a VPN tunnel.

Each class gets its own reachability channel. Only that makes "the internet
is gone" distinguishable from "just the tunnel is gone" without keeping two
sensors side by side. With --source the sensor additionally measures from a
specific local address and thereby deliberately takes a specific path.

The measuring itself is beyond this script: ICMP needs a raw socket, and
prtg.mpprobe.service runs with NoNewPrivileges=yes. The task therefore goes
over /run/prtg-sensor-link-quality.sock to a dedicated service.

Structured after the bundled examples under
/opt/paessler/share/doc/examples/scripts/python.
"""

# std-lib
import argparse
import json
import shlex
import socket
import sys
from typing import Any, NoReturn

HELPER_SOCKET = "/run/prtg-sensor-link-quality.sock"

# Stable numbers for the "Failure Code" channel. They allow targeted
# alerting on a specific cause without parsing the message text.
FAILURE_CODES = {
    "ok": 0,
    "bad-request": 1,
    "no-privileges": 2,
    "socket-failed": 3,
    "bind-failed": 4,
    "busy": 5,
    "internal-error": 6,
}
UNKNOWN_FAILURE = 99

# These causes say nothing about the uplink, only about the sensor itself.
# They do not belong in the measurement channels.
SENSOR_FAILURES = ("bad-request", "no-privileges", "socket-failed")

# Separate channel ranges per class, so an additional target in one does
# not shift the channels of the other. Within a class, parameter order
# rules - appending a target is therefore harmless, inserting one in the
# middle is not.
EXTERNAL_BASE = 30
INTERNAL_BASE = 50
CHANNELS_PER_TARGET = 3
MAX_EXTERNAL = 6
MAX_INTERNAL = 4

# Limits as in the privileged helper. They appear here a second time so a
# typo already shows up at --self-check, not only in operation.
MIN_PACKETS = 1
MAX_PACKETS = 100
MIN_INTERVAL_MS = 50
MAX_INTERVAL_MS = 5000
MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 10000
MAX_TOTAL_PACKETS = 400

# Parameters whose value does not belong in a message to PRTG. The targets
# are deliberately not here: they appear as channel names anyway, and a typo
# in them is exactly the hint this is about.
SECRET_PARAMETERS = ("--source",)

NO_TARGET_MESSAGE = (
    "No target configured. Add at least one --target, for example "
    '"--target 1.1.1.1 --target Google=8.8.8.8". Internal targets behind a '
    'tunnel go into --internal-target, for example "--internal-target '
    'File=10.0.0.10".'
)
TOO_MANY_MESSAGE = (
    "At most %d %s targets are supported; %d were given. More would push the "
    "sensor past a channel count PRTG can still display usefully."
)
TCP_WITHOUT_PORT_MESSAGE = (
    'The target "%s" uses tcp and therefore needs a port, for example '
    '"tcp:%s:443". Without a port there is nothing to connect to.'
)
PORT_WITHOUT_TCP_MESSAGE = (
    'The target "%s" carries a port, but ICMP has no ports. Write '
    '"tcp:%s" to measure by connecting, or drop the port to send echo '
    "requests."
)
BAD_PORT_MESSAGE = (
    'The port in "%s" is not a number between 1 and 65535.'
)
EMPTY_HOST_MESSAGE = (
    'The target "%s" has no host part. Expected [NAME=][tcp:]HOST[:PORT].'
)
UNCLOSED_BRACKET_MESSAGE = (
    'The target "%s" opens a bracket that is never closed. A literal IPv6 '
    'address is written in brackets, for example "[2001:db8::1]".'
)
BUDGET_MESSAGE = (
    "%d targets with %d packets each are %d packets per run; the sensor "
    "allows %d. Lower --packets or measure fewer targets."
)
SOURCE_MESSAGE = (
    "--source must be a literal IPv4 or IPv6 address of this probe, not a "
    "host name: the source of a measurement has to be unambiguous."
)

# prtg.standardlookups.yesno.stateyesok only knows 1 = Yes (Ok) and
# 2 = No (Error) - the counting comes from SNMP, where 1 means true and 2
# false. The lookup does not know a 0: PRTG then shows "undefined lookup
# value" and turns it into a mere warning where an error should stand.
LOOKUP_YES = 1
LOOKUP_NO = 2

# The lookup for channels that carry an alarm: 1 = Yes (Ok), 2 = No
# (Error). Every yes/no channel of this sensor carries an alarm - an
# unreachable target is not something that merely needs explaining.
ALARM_LOOKUP = "prtg.standardlookups.yesno.stateyesok"


class ConfigError(Exception):
    """The parameters do not add up to a runnable measurement."""


class ReportingParser(argparse.ArgumentParser):
    """Let argparse carry its own error message.

    The parameters are typed into a text field in PRTG and checked by
    nothing there; the first sensor run is the only place a typo can show
    up. argparse knows exactly what is wrong, but writes it to stderr and
    terminates the process. Here the message is carried as an exception
    instead and ends up in the output.
    """

    def error(self, message) -> NoReturn:
        raise ConfigError(message)


def redact(message: str, tokens: list[str]) -> str:
    """Remove values of protected parameters from an error message."""
    values = []
    for index, token in enumerate(tokens):
        for name in SECRET_PARAMETERS:
            if token == name and index + 1 < len(tokens):
                values.append(tokens[index + 1])
            elif token.startswith("%s=" % name):
                values.append(token[len(name) + 1:])
    for value in values:
        if value:
            message = message.replace(value, "...")
    return message


def setup():
    argparser = ReportingParser(
        description="The script measures reachability and connection quality.",
    )

    argparser.add_argument("--target", action="append", default=[],
                           metavar="[NAME=][tcp:]HOST[:PORT]",
                           help="A target on the internet. Repeatable.")
    argparser.add_argument("--internal-target", action="append", default=[],
                           metavar="[NAME=][tcp:]HOST[:PORT]",
                           help="A target inside the own network, for example "
                                "behind a tunnel. Repeatable.")
    argparser.add_argument("--packets", type=int, default=20,
                           help="Echo requests per target and run.")
    argparser.add_argument("--interval-ms", type=int, default=200,
                           help="Milliseconds between two packets.")
    argparser.add_argument("--timeout-ms", type=int, default=1000,
                           help="Milliseconds to wait for a single answer.")
    argparser.add_argument("--payload-bytes", type=int, default=56,
                           help="Payload size of an echo request.")
    argparser.add_argument("--source",
                           help="Local address the measurement binds to.")
    argparser.add_argument("--self-check", action="store_true",
                           help="Only verify that the sensor is able to run.")

    tokens: list[str] = []
    try:
        # Is a terminal?
        if sys.stdin.isatty():
            tokens = sys.argv[1:]
            args = argparser.parse_args()
        else:
            pipestring = sys.stdin.read().rstrip()
            tokens = shlex.split(pipestring)
            args = argparser.parse_args(tokens)

    except ConfigError as problem:
        fail(redact(str(problem), tokens))
    except ValueError:
        # shlex.split fails on an unpaired quotation mark.
        fail("Could not read the parameters: an unmatched quote. Check the "
             "configured parameters.")
    except SystemExit as termination:
        # Help (code 0) stays untouched so the terminal invocation keeps
        # working. Everything else goes through error() and thereby through
        # ConfigError; it no longer ends up here.
        if termination.code == 0:
            raise
        fail("Could not read the parameters. Check the configured parameters.")
    return vars(args)


def split_host_port(rest: str, spec: str):
    """Split host and port without breaking on an IPv6 address.

    A bare IPv6 address consists almost entirely of colons; which one
    separates a port cannot be decided without brackets. Hence the usual
    rule: the address goes in brackets, the port comes after. Without
    brackets, exactly one colon separates a port; several mean an address
    without a port.
    """
    if rest.startswith("["):
        closing = rest.find("]")
        if closing < 0:
            raise ConfigError(UNCLOSED_BRACKET_MESSAGE % spec)
        host = rest[1:closing]
        remainder = rest[closing + 1:]
        if not remainder:
            return host, None
        if not remainder.startswith(":"):
            raise ConfigError(EMPTY_HOST_MESSAGE % spec)
        return host, remainder[1:]

    if rest.count(":") == 1:
        host, _, port = rest.partition(":")
        return host, port
    return rest, None


def parse_target(spec: str, scope: str) -> dict[str, Any]:
    """Translate a target spec from PRTG into a task for the helper.

    Allowed is [NAME=][tcp:]HOST[:PORT]. The name is the channel name in
    PRTG; without it the address appears there, which quickly becomes
    unreadable with several targets.
    """
    text = spec.strip()
    if not text:
        raise ConfigError(EMPTY_HOST_MESSAGE % spec)

    name = ""
    # An equals sign occurs neither in a host name nor in an address, so
    # the split is unambiguous.
    if "=" in text:
        name, _, text = text.partition("=")
        name = name.strip()
        text = text.strip()

    mode = "icmp"
    for prefix, chosen in (("tcp:", "tcp"), ("icmp:", "icmp")):
        if text.lower().startswith(prefix):
            mode = chosen
            text = text[len(prefix):]
            break

    host, port_text = split_host_port(text, spec)
    host = host.strip()
    if not host:
        raise ConfigError(EMPTY_HOST_MESSAGE % spec)

    port = None
    if port_text is not None:
        try:
            port = int(port_text)
        except ValueError:
            raise ConfigError(BAD_PORT_MESSAGE % spec) from None
        if not 1 <= port <= 65535:
            raise ConfigError(BAD_PORT_MESSAGE % spec)

    if mode == "tcp" and port is None:
        raise ConfigError(TCP_WITHOUT_PORT_MESSAGE % (spec, host))
    if mode == "icmp" and port is not None:
        raise ConfigError(PORT_WITHOUT_TCP_MESSAGE % (spec, text))

    return {
        "name": (name or default_name(host, mode, port))[:64],
        "host": host,
        "mode": mode,
        "port": port or 0,
        "scope": scope,
    }


def default_name(host: str, mode: str, port) -> str:
    if mode == "tcp":
        return "%s:%d" % (host, port)
    return host


def collect_targets(args: dict[str, Any]) -> list[dict[str, Any]]:
    """All configured targets, external first, with their channel numbers."""
    groups = (
        ("external", args["target"], EXTERNAL_BASE, MAX_EXTERNAL, "external"),
        ("internal", args["internal_target"], INTERNAL_BASE, MAX_INTERNAL,
         "internal"),
    )
    targets = []
    for scope, specs, base, limit, label in groups:
        if len(specs) > limit:
            raise ConfigError(TOO_MANY_MESSAGE % (limit, label, len(specs)))
        for position, spec in enumerate(specs):
            target = parse_target(spec, scope)
            target["channel"] = base + position * CHANNELS_PER_TARGET
            targets.append(target)
    if not targets:
        raise ConfigError(NO_TARGET_MESSAGE)
    return targets


def validate(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Check the parameters before a single packet flows.

    A pure function without network access: that way --self-check can run
    the same validation on the probe before anyone even enters the
    parameters in PRTG. Every message ends with the line that has to stand
    there instead - it is the only place an administrator can learn what
    was wrong.
    """
    targets = collect_targets(args)

    for name, value, lowest, highest in (
        ("--packets", args["packets"], MIN_PACKETS, MAX_PACKETS),
        ("--interval-ms", args["interval_ms"], MIN_INTERVAL_MS,
         MAX_INTERVAL_MS),
        ("--timeout-ms", args["timeout_ms"], MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
        ("--payload-bytes", args["payload_bytes"], 12, 1400),
    ):
        if not lowest <= value <= highest:
            raise ConfigError('%s must be between %d and %d, not %d.'
                              % (name, lowest, highest, value))

    total = len(targets) * args["packets"]
    if total > MAX_TOTAL_PACKETS:
        raise ConfigError(BUDGET_MESSAGE % (len(targets), args["packets"],
                                            total, MAX_TOTAL_PACKETS))

    if args["source"]:
        if not is_literal_address(args["source"]):
            raise ConfigError(SOURCE_MESSAGE)
    return targets


def is_literal_address(value: str) -> bool:
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, value)
        except OSError:
            continue
        return True
    return False


def call_helper(job: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Task the privileged service and read back its result."""
    payload = json.dumps(job).encode("utf-8")
    answer = bytearray()

    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        fail("Could not create a local socket.")
    try:
        connection.settimeout(timeout)
        connection.connect(HELPER_SOCKET)
        connection.sendall(payload)
        # Only end-of-file tells the service the task is complete.
        connection.shutdown(socket.SHUT_WR)
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            answer.extend(chunk)
    except socket.timeout:
        fail("The privileged helper did not answer in time.")
    except FileNotFoundError:
        fail("The privileged helper is not installed on this probe "
             "(%s is missing)." % HELPER_SOCKET)
    except PermissionError:
        fail("Not allowed to reach the privileged helper at %s."
             % HELPER_SOCKET)
    except ConnectionRefusedError:
        fail("The privileged helper is not running. Check "
             "prtg-sensor-link-quality.socket on this probe.")
    except OSError as error:
        fail("Could not reach the privileged helper: %s" % error.strerror)
    finally:
        connection.close()

    if not answer.strip():
        fail("The privileged helper returned no answer.")
    try:
        result = json.loads(answer.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        fail("The privileged helper returned an unreadable answer.")
    if not isinstance(result, dict):
        fail("The privileged helper returned an unreadable answer.")
    return result


def budget_seconds(args: dict[str, Any]) -> int:
    """How long the helper may take at most.

    Derived from the task instead of fixed: interval times packet count is
    the running time, plus the wait for the last answer. The surcharge
    covers resolution and waiting for the lock.
    """
    span = args["packets"] * args["interval_ms"] / 1000.0
    return int(span + args["timeout_ms"] / 1000.0) + 30


def lookup_value(condition) -> int:
    """Translate a yes/no into the values PRTG understands."""
    return LOOKUP_YES if condition else LOOKUP_NO


def channel(identifier: int, name: str, value, **extra) -> dict[str, Any]:
    result = {"id": identifier, "name": name, "type": "integer", "value": value}
    result.update(extra)
    return result


def number(identifier: int, name: str, value, **extra) -> dict[str, Any]:
    """A channel with decimal places.

    Necessary for round-trip times: an internal target answers in fractions
    of a millisecond, and as an integer a permanent 0 would stand there - no
    statement at all exactly where the line is fastest.
    """
    return channel(identifier, name, round(float(value), 2), type="float",
                   **extra)


def quality_index(latency_ms, jitter_ms, loss_percent) -> int:
    """Combine round-trip time, jitter and loss into a number from 0 to 100.

    The R factor of the simplified E-model per ITU-T G.107. It is meant for
    voice, and exactly that makes it usable here: voice is the most
    sensitive common application, so the value trips before anyone calls.
    Jitter counts double because a receiver has to compensate for it, and
    that compensation acts as additional delay.

    Deliberately not a home-grown measure: an invented formula would look
    the same but could be looked up against nothing.
    """
    effective = latency_ms + 2.0 * jitter_ms + 10.0
    if effective < 160.0:
        factor = 93.2 - effective / 40.0
    else:
        factor = 93.2 - (effective - 120.0) / 10.0
    factor -= loss_percent * 2.5
    return int(round(max(0.0, min(100.0, factor))))


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the overall statement from the individual results.

    Round-trip time, jitter and quality index compute over one measurement
    kind only: a TCP handshake contains the far end's reaction and sits
    systematically above an echo. Averaging both would produce a number no
    path ever had. If there are echo targets, they decide; otherwise the
    TCP targets take their place. Which ones they are depends on the
    configuration, not the measurement - so the channels mean the same
    thing over time.

    The reported loss is exempt from that: a lost packet is a lost packet,
    no matter how it travelled. It therefore counts across all targets.
    """
    reachable = [entry for entry in results if entry.get("reachable")]
    summary = {
        "reachable_count": len(reachable),
        "target_count": len(results),
        "all_reachable": len(reachable) == len(results),
    }

    for scope in ("external", "internal"):
        group = [entry for entry in results if entry.get("scope") == scope]
        if group:
            summary["%s_reachable" % scope] = any(entry.get("reachable")
                                                  for entry in group)

    sent = sum(entry.get("sent", 0) for entry in results)
    received = sum(entry.get("received", 0) for entry in results)
    summary["loss_percent"] = (100.0 if not sent
                               else (sent - received) * 100.0 / sent)
    summary["worst_loss_percent"] = max(
        [float(entry.get("loss_percent", 100)) for entry in results] or [100.0])

    timed = [entry for entry in reachable if entry.get("mode") == "icmp"]
    if not timed:
        timed = [entry for entry in reachable if entry.get("mode") == "tcp"]
    latencies = [float(entry["rtt_avg_ms"]) for entry in timed
                 if entry.get("rtt_avg_ms") is not None]
    jitters = [float(entry["jitter_ms"]) for entry in timed
               if entry.get("jitter_ms") is not None]

    summary["latency_ms"] = sum(latencies) / len(latencies) if latencies else 0.0
    summary["jitter_ms"] = sum(jitters) / len(jitters) if jitters else 0.0
    summary["worst_latency_ms"] = max(latencies) if latencies else 0.0

    # The index computes over the same targets as round-trip time and
    # jitter, so only the answering ones. A dead target belongs in
    # reachability, not in quality: otherwise a single target that drops
    # ICMP on principle would pull the index to a permanent 0 - and a
    # channel that always shows 0 no longer says anything about the line.
    # How many targets answer at all is in channels 11, 20, 21 and 22.
    timed_sent = sum(entry.get("sent", 0) for entry in timed)
    timed_received = sum(entry.get("received", 0) for entry in timed)
    quality_loss = (0.0 if not timed_sent
                    else (timed_sent - timed_received) * 100.0 / timed_sent)
    # Without a single round-trip time there is nothing to rate. A 0 is
    # then the right statement: the worst state, not a missing value.
    summary["quality_index"] = (
        quality_index(summary["latency_ms"], summary["jitter_ms"],
                      quality_loss) if latencies else 0)
    return summary


def merge_results(targets: list[dict[str, Any]],
                  results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair every configured target with its result - or a substitute.

    Everything the sensor reports thereby depends on the configuration, not
    on whatever the helper happened to report about. Without this step, a
    failure without target data would make the "Internet Reachable" and
    "Internal Reachable" channels disappear entirely - precisely the two
    that are supposed to name an outage.

    Class and measurement kind also come from here: they are a property of
    the configuration, not information from the far end.
    """
    by_name = {entry.get("name"): entry for entry in results}
    merged = []
    for target in targets:
        outcome = by_name.get(target["name"])
        if outcome is None:
            outcome = {"name": target["name"], "sent": 0, "received": 0,
                       "reachable": False, "loss_percent": 100,
                       "code": "no-result"}
        merged.append(dict(outcome, scope=target["scope"],
                           mode=target["mode"]))
    return merged


def target_channels(targets: list[dict[str, Any]],
                    results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Three channels per target - even for a target without a result.

    The channel structure depends solely on the configuration, never on the
    measurement. A channel that disappears during an outage and comes back
    afterwards tears its history apart in PRTG; a channel with 100 % loss,
    by contrast, tells exactly what happened.
    """
    channels = []
    for target in targets:
        outcome = results.get(target["name"]) or {}
        base = target["channel"]
        channels.append(number(base, "%s Loss" % target["name"],
                               outcome.get("loss_percent", 100),
                               kind="percent"))
        channels.append(number(base + 1, "%s Latency" % target["name"],
                               outcome.get("rtt_avg_ms", 0),
                               kind="time_milliseconds"))
        channels.append(number(base + 2, "%s Jitter" % target["name"],
                               outcome.get("jitter_ms", 0),
                               kind="time_milliseconds"))
    return channels


def present(targets: list[dict[str, Any]], results: list[dict[str, Any]],
            duration_ms: int, code: str, message: str) -> dict[str, Any]:
    """Bring the result into the shape PRTG expects."""
    merged = merge_results(targets, results)
    summary = aggregate(merged)
    by_name = {entry.get("name"): entry for entry in merged}
    succeeded = code == "ok"

    channels = [
        channel(10, "Test Result", lookup_value(succeeded), type="lookup",
                lookup_name=ALARM_LOOKUP),
        channel(11, "Targets Reachable", summary["reachable_count"]),
        number(12, "Packet Loss", summary["loss_percent"], kind="percent"),
        number(13, "Latency", summary["latency_ms"],
               kind="time_milliseconds"),
        number(14, "Jitter", summary["jitter_ms"], kind="time_milliseconds"),
        number(15, "Worst Latency", summary["worst_latency_ms"],
               kind="time_milliseconds"),
        number(16, "Worst Packet Loss", summary["worst_loss_percent"],
               kind="percent"),
        channel(17, "Test Duration", int(duration_ms),
                kind="time_milliseconds"),
        channel(18, "Failure Code", FAILURE_CODES.get(code, UNKNOWN_FAILURE)),
        channel(19, "Quality Index", summary["quality_index"], kind="custom",
                display_unit="R"),
        channel(20, "All Targets Reachable",
                lookup_value(summary["all_reachable"]), type="lookup",
                lookup_name=ALARM_LOOKUP),
    ]
    for identifier, name, key in ((21, "Internet Reachable",
                                   "external_reachable"),
                                  (22, "Internal Reachable",
                                   "internal_reachable")):
        if key in summary:
            channels.append(channel(identifier, name,
                                    lookup_value(summary[key]), type="lookup",
                                    lookup_name=ALARM_LOOKUP))
    channels.extend(target_channels(targets, by_name))

    # Ascending, so the channel list in PRTG has the same order no matter
    # which optional values are present.
    channels.sort(key=lambda entry: entry["id"])

    return {
        "version": 2,
        "status": "ok",
        "message": describe(summary, merged, code, message)[:2000],
        "channels": channels,
    }


def describe(summary: dict[str, Any], results: list[dict[str, Any]],
             code: str, message: str) -> str:
    if code != "ok":
        return "%s (%s)" % (message or "The measurement did not run", code)

    unreachable = [entry for entry in results if not entry.get("reachable")]
    if unreachable:
        # The names of the failed targets come first: whoever reads the
        # message in a notification wants to know what is gone before
        # anything else.
        summary_text = "%d of %d targets unreachable: %s" % (
            len(unreachable), summary["target_count"],
            ", ".join("%s (%s)" % (entry.get("name"), entry.get("code", "?"))
                      for entry in unreachable[:5]))
    else:
        summary_text = "All %d targets reachable" % summary["target_count"]

    summary_text += ", %.1f %% loss, %.1f ms, %.1f ms jitter, quality %d" % (
        summary["loss_percent"], summary["latency_ms"], summary["jitter_ms"],
        summary["quality_index"])

    for scope, label in (("external", "internet"), ("internal", "internal")):
        key = "%s_reachable" % scope
        if key in summary and not summary[key]:
            summary_text += "; no %s target answered" % label
    return summary_text


def failure_result(targets: list[dict[str, Any]], code: str,
                   message: str) -> dict[str, Any]:
    """Report a failure as a valid measurement.

    The alarm hangs on the "Test Result" channel, not on the sensor status.
    That keeps the history of the measurement channels readable across an
    outage. Only when the sensor itself cannot work does it become a sensor
    error.
    """
    if code in SENSOR_FAILURES:
        fail("%s (%s)" % (message, code))
    return present(targets, [], 0, code, message)


def self_check(args: dict[str, Any]) -> dict[str, Any]:
    """Check the ability to run - and the parameters, if any came along.

    The helper does not only check that it is reachable, but also that it
    may open an ICMP socket. Otherwise a missing permission would only show
    up on the first real run and look like a network failure there.
    """
    answer = call_helper({"action": "ping"}, 30)
    if answer.get("result") != "ok":
        fail("Self-check failed: %s" % answer.get("message", "unknown"))

    configured = bool(args["target"] or args["internal_target"]
                      or args["source"])
    if configured:
        try:
            validate(args)
        except ConfigError as problem:
            fail(str(problem))

    message = "The privileged helper is reachable and can open an ICMP socket."
    if configured:
        message += " The configured parameters are valid."
    return {
        "version": 2,
        "status": "ok",
        "message": message,
        "channels": [
            channel(10, "Test Result", LOOKUP_YES, type="lookup",
                    lookup_name=ALARM_LOOKUP),
        ],
    }


def work(args: dict[str, Any]):
    if args["self_check"]:
        return self_check(args)

    try:
        targets = validate(args)
    except ConfigError as problem:
        fail(str(problem))

    job = {
        "action": "measure",
        "targets": [{"name": target["name"], "host": target["host"],
                     "mode": target["mode"], "port": target["port"],
                     "scope": target["scope"]} for target in targets],
        "packets": args["packets"],
        "interval_ms": args["interval_ms"],
        "timeout_ms": args["timeout_ms"],
        "payload_bytes": args["payload_bytes"],
        "source": args["source"],
    }
    answer = call_helper(job, budget_seconds(args))

    if answer.get("result") != "ok":
        return failure_result(targets, answer.get("code", "internal-error"),
                              answer.get("message", ""))

    return present(targets, answer.get("targets") or [],
                   answer.get("duration_ms", 0), "ok", "")


def fail(message: str) -> NoReturn:
    print(
        json.dumps(
            {
                "version": 2,
                "status": "error",
                "message": message[:2000],
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    # Retrieve script arguments
    arguments = setup()

    # Execute sensor logic
    sensor_result = work(arguments)

    # Format the result to JSON and print them to stdout.
    print(json.dumps(sensor_result))

    # Scripts always need to exit with EXITCODE=0.
    sys.exit(0)
