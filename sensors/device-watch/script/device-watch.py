#!/usr/bin/env python3

"""Script v2 sensor: is every device on this site switched on.

The collector half of the availability monitoring. It asks the platform
which devices this probe should watch, measures them, and reports the
result back - all over the NATS server this probe already talks to. PRTG
gets a summary in four channels; the per-device history lives in the
platform, which is where the dashboard reads it.

Three things it deliberately does not do.

It does not carry a device list of its own. The list comes over NATS at the
start of every run, so a printer added in the interface is measured on the
next scan and nobody rolls anything out.

It does not need its own credentials. The NATS URL, account, password and
CA come out of /etc/paessler/mpprobe/config.yaml, which this script's
service user can read because prtg.mpprobe.service must read it too.

It does not keep anything between runs. A report that cannot be delivered
is lost, and the platform records the gap as unmeasured rather than as an
outage - which is the honest answer, and cheaper than a spool directory
this service user may not even be able to create.

Structured after the bundled examples under
/opt/paessler/share/doc/examples/scripts/python.
"""

# std-lib
import argparse
import json
import os
import shlex
import socket
import ssl
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn

HELPER_SOCKET = "/run/prtg-sensor-device-watch.sock"
MPP_CONFIG = "/etc/paessler/mpprobe/config.yaml"

# The subjects, as in web/backend/app/domain/watch.py. Raised together with
# the version there whenever the meaning of a field changes.
SUBJECT_PREFIX = "prtg-nats.watch"
PROTOCOL_VERSION = 1

# Stable numbers for the "Failure Code" channel, so an alert can name a
# cause without anybody parsing the message text.
FAILURE_CODES = {
    "ok": 0,
    "bad-request": 1,
    "no-privileges": 2,
    "no-config": 3,
    "no-connection": 4,
    "no-answer": 5,
    "busy": 6,
    "internal-error": 7,
}
UNKNOWN_FAILURE = 99

# Channel ids. Fixed rather than derived: PRTG remembers a channel by its
# id, and a number that moves takes its history with it.
CHANNEL_TOTAL = 10
CHANNEL_REACHABLE = 20
CHANNEL_UNREACHABLE = 30
CHANNEL_FAILURE = 40
CHANNEL_DURATION = 50

# PRTG's own lookup for a state channel, as used by the other sensors here.
ALARM_LOOKUP = "prtg.standardlookups.yesno.stateyesok"
LOOKUP_YES = 1
LOOKUP_NO = 0

# How long the whole run may take. The scanning interval it is called from
# is typically a minute, and a sensor that outlives its own interval gets
# overtaken by the next run.
MAX_RUN_SECONDS = 50
NATS_TIMEOUT_SECONDS = 10

MIN_TIMEOUT_MS = 200
MAX_TIMEOUT_MS = 10000
MIN_PACKETS = 1
MAX_PACKETS = 4


class ConfigError(Exception):
    """A parameter or a configuration file this sensor cannot work with."""


class BusError(Exception):
    """The NATS server could not be reached, or did not answer."""


class ReportingParser(argparse.ArgumentParser):
    """Let argparse carry its own error message.

    The parameters are typed into a text field in PRTG and checked by
    nothing there; the first sensor run is the only place a typo can show
    up. argparse knows exactly what is wrong, but writes it to stderr and
    terminates the process, so the message is carried as an exception
    instead and ends up in the output.
    """

    def error(self, message) -> NoReturn:
        raise ConfigError(message)


# --- The probe's own NATS configuration -------------------------------------


def read_mpp_config(path: str) -> dict[str, str]:
    """URL, account, password and CA out of the MPP configuration.

    Parsed by hand rather than with PyYAML, which is not on every probe.
    The file is rendered from config/mpprobe-config.yaml.template in this
    repository, so its shape is known - and anything unexpected is reported
    as a configuration problem instead of being guessed at.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        raise ConfigError(
            "No MPP configuration at %s - is this a probe?" % path
        ) from None
    except PermissionError:
        raise ConfigError(
            "Not allowed to read %s. The sensor runs as the probe's service "
            "user, which has to be able to read its own configuration." % path
        ) from None

    values: dict[str, str] = {}
    section: list[str] = []
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, separator, value = raw.strip().partition(":")
        if not separator:
            continue
        # Two spaces per level in a file this repository renders itself.
        depth = indent // 2
        section = section[:depth]
        value = value.strip().strip('"').strip("'")
        if value:
            values[".".join(section + [key.strip()])] = value
        else:
            section = section + [key.strip()]

    url = values.get("nats.url", "")
    if not url:
        raise ConfigError("%s names no NATS server." % path)
    return {
        "url": url,
        "user": values.get("nats.authentication.user", ""),
        "password": values.get("nats.authentication.password", ""),
        "ca": values.get("nats.server_ca", ""),
    }


def split_url(url: str) -> tuple[str, int, bool]:
    """``tls://host:4222`` as host, port and whether it is TLS."""
    scheme, _, rest = url.partition("://")
    if not rest:
        scheme, rest = "nats", url
    host, _, port = rest.rpartition(":")
    if not host:
        raise ConfigError("The NATS address %r names no port." % url)
    try:
        return host, int(port), scheme in ("tls", "nats+tls")
    except ValueError:
        raise ConfigError("The NATS address %r names no usable port." % url) from None


# --- A NATS client small enough to live in a sensor -------------------------
#
# The wire protocol is text: CONNECT, PUB, SUB and a MSG coming back. That
# is all this sensor needs, and implementing it here keeps the sensor a
# single file with no dependency to install on a probe - which matters most
# on the probes that have no route to a package index in the first place.


class NatsConnection:
    def __init__(self, host: str, port: int, use_tls: bool, ca_path: str) -> None:
        self._host = host
        self._deadline = time.time() + NATS_TIMEOUT_SECONDS
        try:
            plain = socket.create_connection((host, port), NATS_TIMEOUT_SECONDS)
        except OSError as problem:
            raise BusError("Cannot reach the NATS server: %s" % problem) from None

        if use_tls:
            try:
                context = ssl.create_default_context(cafile=ca_path or None)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                self._socket: socket.socket = context.wrap_socket(
                    plain, server_hostname=host
                )
            except (ssl.SSLError, OSError) as problem:
                plain.close()
                raise BusError("TLS to the NATS server failed: %s" % problem) from None
        else:
            self._socket = plain

        self._socket.settimeout(NATS_TIMEOUT_SECONDS)
        self._buffer = bytearray()

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass

    def _readline(self) -> bytes:
        while b"\r\n" not in self._buffer:
            if time.time() > self._deadline:
                raise BusError("The NATS server stopped answering.")
            try:
                chunk = self._socket.recv(65536)
            except socket.timeout:
                raise BusError("The NATS server did not answer in time.") from None
            except OSError as problem:
                raise BusError("The NATS connection broke: %s" % problem) from None
            if not chunk:
                raise BusError("The NATS server closed the connection.")
            self._buffer.extend(chunk)
        line, _, rest = bytes(self._buffer).partition(b"\r\n")
        self._buffer = bytearray(rest)
        return line

    def _read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            try:
                chunk = self._socket.recv(65536)
            except socket.timeout:
                raise BusError("The NATS server did not answer in time.") from None
            except OSError as problem:
                raise BusError("The NATS connection broke: %s" % problem) from None
            if not chunk:
                raise BusError("The NATS server closed the connection.")
            self._buffer.extend(chunk)
        data = bytes(self._buffer[:count])
        self._buffer = bytearray(self._buffer[count:])
        return data

    def _send(self, line: str, payload: bytes = b"") -> None:
        try:
            self._socket.sendall(line.encode("utf-8") + payload)
        except OSError as problem:
            raise BusError("Could not send to NATS: %s" % problem) from None

    def handshake(self, user: str, password: str) -> None:
        info = self._readline()
        if not info.startswith(b"INFO"):
            raise BusError("The server did not introduce itself as NATS.")
        connect = {
            "verbose": False,
            "pedantic": False,
            "tls_required": False,
            "name": "prtg-nats-device-watch",
            "lang": "python",
            "version": str(PROTOCOL_VERSION),
            "protocol": 1,
            "user": user,
            "pass": password,
        }
        self._send("CONNECT %s\r\n" % json.dumps(connect))
        # PING/PONG rather than trusting the send: with verbose off, a
        # rejected CONNECT is otherwise only noticed much later, as a
        # connection that closes while waiting for a reply.
        self._send("PING\r\n")
        while True:
            line = self._readline()
            if line.startswith(b"PONG"):
                return
            if line.startswith(b"-ERR"):
                # The server's reason can name the account; the password is
                # never in it, but the account is quite enough to keep out
                # of a message that ends up in PRTG.
                raise BusError("NATS refused the connection.")
            if line.startswith(b"PING"):
                self._send("PONG\r\n")

    def request(self, subject: str, payload: bytes) -> bytes:
        """Publish and wait for exactly one answer."""
        inbox = "_INBOX.%s" % uuid.uuid4().hex
        self._send("SUB %s 1\r\n" % inbox)
        self.publish(subject, payload, reply_to=inbox)

        while True:
            line = self._readline()
            if line.startswith(b"MSG "):
                parts = line.decode("utf-8", "replace").split()
                try:
                    size = int(parts[-1])
                except (ValueError, IndexError):
                    raise BusError("The answer had no usable length.") from None
                body = self._read(size + 2)[:size]
                self._send("UNSUB 1\r\n")
                return body
            if line.startswith(b"PING"):
                self._send("PONG\r\n")
            elif line.startswith(b"-ERR"):
                raise BusError("NATS refused the request.")

    def publish(self, subject: str, payload: bytes, reply_to: str = "") -> None:
        header = "PUB %s %s%d\r\n" % (
            subject,
            reply_to + " " if reply_to else "",
            len(payload),
        )
        self._send(header, payload + b"\r\n")

    def flush(self) -> None:
        """Wait until the server has taken everything sent so far.

        Without this the process can exit with the report still in a socket
        buffer, which loses a run for no reason anybody could later see.
        """
        self._send("PING\r\n")
        while True:
            line = self._readline()
            if line.startswith(b"PONG"):
                return
            if line.startswith(b"PING"):
                self._send("PONG\r\n")
            elif line.startswith(b"-ERR"):
                raise BusError("NATS refused the report.")


# --- Measuring ---------------------------------------------------------------


def call_helper(job: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Task the privileged service and read back its result."""
    payload = json.dumps(job).encode("utf-8")
    answer = bytearray()

    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        fail("Could not create a local socket.", "internal-error")
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
        fail("The privileged helper did not answer in time.", "no-answer")
    except FileNotFoundError:
        fail(
            "The privileged helper is not installed on this probe (%s is "
            "missing)." % HELPER_SOCKET,
            "no-privileges",
        )
    except PermissionError:
        fail(
            "Not allowed to reach the privileged helper at %s." % HELPER_SOCKET,
            "no-privileges",
        )
    except ConnectionRefusedError:
        fail(
            "The privileged helper is not running. Check "
            "prtg-sensor-device-watch.socket on this probe.",
            "no-privileges",
        )
    except OSError as problem:
        fail("Could not reach the privileged helper: %s" % problem, "internal-error")
    finally:
        connection.close()

    try:
        return json.loads(bytes(answer).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        fail("The privileged helper answered with something unreadable.",
             "internal-error")


def check_tcp(address: str, port: int, timeout_ms: int) -> dict[str, Any]:
    """Connect and hang up.

    For everything that answers ICMP badly or not at all - and it needs no
    privileges, which is why it does not go through the helper.
    """
    started = time.time()
    try:
        connection = socket.create_connection((address, port), timeout_ms / 1000.0)
    except socket.timeout:
        return {"reachable": False, "code": "timeout", "rtt_ms": None, "address": None}
    except socket.gaierror:
        return {
            "reachable": False,
            "code": "unresolved",
            "rtt_ms": None,
            "address": None,
        }
    except OSError:
        # Refused counts as unreachable for this sensor's question: the
        # device answered, but not on the port somebody said to ask on, and
        # only the person who configured it can say which is meant.
        return {
            "reachable": False,
            "code": "refused",
            "rtt_ms": None,
            "address": None,
        }
    peer = connection.getpeername()[0]
    connection.close()
    return {
        "reachable": True,
        "code": "ok",
        "rtt_ms": round((time.time() - started) * 1000.0, 3),
        "address": peer,
    }


def measure(targets: list[dict[str, Any]], timeout_ms: int, packets: int):
    """Every target once, ICMP through the helper, TCP from here."""
    icmp = [target for target in targets if target.get("method") != "tcp"]
    results: dict[str, dict[str, Any]] = {}

    if icmp:
        answer = call_helper(
            {
                "action": "measure",
                "targets": [
                    {"id": target["device_id"], "host": target["address"]}
                    for target in icmp
                ],
                "timeout_ms": timeout_ms,
                "packets": packets,
            },
            budget_seconds(timeout_ms, packets),
        )
        if answer.get("result") != "ok":
            fail(
                answer.get("message") or "The measurement did not run.",
                answer.get("code", "internal-error"),
            )
        for entry in answer.get("targets") or []:
            results[entry.get("id", "")] = entry

    for target in targets:
        if target.get("method") != "tcp":
            continue
        port = target.get("port")
        if not port:
            results[target["device_id"]] = {
                "reachable": False,
                "code": "no-port",
                "rtt_ms": None,
                "address": None,
            }
            continue
        results[target["device_id"]] = check_tcp(
            target["address"], int(port), timeout_ms
        )

    return results


def budget_seconds(timeout_ms: int, packets: int) -> int:
    """How long to wait for the helper.

    Its own run is bounded; this is that bound plus a little, so a helper
    that hangs is noticed here rather than by PRTG killing the sensor.
    """
    return min(MAX_RUN_SECONDS, int((timeout_ms * packets) / 1000.0) + 10)


# --- Talking to the platform -------------------------------------------------


def fetch_targets(connection: NatsConnection, account: str) -> dict[str, Any]:
    request = json.dumps({"version": PROTOCOL_VERSION, "revision": ""})
    answer = connection.request(
        "%s.targets.%s" % (SUBJECT_PREFIX, account), request.encode("utf-8")
    )
    try:
        document = json.loads(answer.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BusError("The platform answered with something unreadable.") from None
    if not isinstance(document, dict):
        raise BusError("The platform answered with something unreadable.")
    if document.get("version") != PROTOCOL_VERSION:
        raise BusError(
            "The platform speaks version %s of this protocol, this sensor "
            "speaks %d. Update the sensor."
            % (document.get("version"), PROTOCOL_VERSION)
        )
    return document


def send_report(
    connection: NatsConnection,
    account: str,
    revision: str,
    targets: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc)
    entries = []
    for target in targets:
        result = results.get(target["device_id"])
        if result is None:
            continue
        entries.append(
            {
                "device_id": target["device_id"],
                "at": now.isoformat(),
                "ok": bool(result.get("reachable")),
                "rtt_ms": result.get("rtt_ms"),
                "address": result.get("address"),
                "error": None if result.get("reachable") else result.get("code"),
            }
        )

    payload = {
        "version": PROTOCOL_VERSION,
        "account": account,
        "sent_at": now.isoformat(),
        "revision": revision,
        "results": entries,
    }
    connection.publish(
        "%s.report.%s" % (SUBJECT_PREFIX, account),
        json.dumps(payload).encode("utf-8"),
    )
    connection.flush()


# --- The sensor --------------------------------------------------------------


def setup():
    argparser = ReportingParser(
        description="The script measures whether the devices this probe "
        "watches are reachable.",
    )

    argparser.add_argument(
        "--timeout-ms",
        type=int,
        default=1500,
        help="Milliseconds to wait for a single answer.",
    )
    argparser.add_argument(
        "--packets",
        type=int,
        default=2,
        help="Attempts per device before it counts as unreachable in this run.",
    )
    argparser.add_argument(
        "--config",
        default=MPP_CONFIG,
        help="Where the probe's own configuration is, if not the usual place.",
    )
    argparser.add_argument(
        "--self-check",
        action="store_true",
        help="Only verify that the sensor is able to run.",
    )

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
        fail(str(problem), "bad-request")
    except ValueError:
        # shlex.split fails on an unpaired quotation mark.
        fail(
            "Could not read the parameters: an unmatched quote. Check the "
            "configured parameters.",
            "bad-request",
        )
    except SystemExit as termination:
        # Help (code 0) stays untouched so the terminal invocation keeps
        # working.
        if termination.code == 0:
            raise
        fail("Could not read the parameters. Check the configured parameters.",
             "bad-request")
    return vars(args)


def validate(args: dict[str, Any]) -> None:
    if not MIN_TIMEOUT_MS <= args["timeout_ms"] <= MAX_TIMEOUT_MS:
        raise ConfigError(
            "--timeout-ms has to be between %d and %d."
            % (MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
        )
    if not MIN_PACKETS <= args["packets"] <= MAX_PACKETS:
        raise ConfigError(
            "--packets has to be between %d and %d." % (MIN_PACKETS, MAX_PACKETS)
        )


def channel(identifier: int, name: str, value, **extra) -> dict[str, Any]:
    result = {"id": identifier, "name": name, "type": "integer", "value": value}
    result.update(extra)
    return result


def failure_result(message: str, code: str) -> dict[str, Any]:
    """A run that did not happen, said in channels rather than only in text.

    The measurement channels stay at zero and the failure code carries the
    cause, so an alert can fire on "the sensor cannot work" without anybody
    matching on message text.
    """
    return {
        "version": 2,
        "status": "ok",
        "message": message[:2000],
        "channels": [
            channel(CHANNEL_TOTAL, "Devices", 0),
            channel(CHANNEL_REACHABLE, "Reachable", 0),
            channel(CHANNEL_UNREACHABLE, "Unreachable", 0, limit_max_error=0),
            channel(
                CHANNEL_FAILURE,
                "Failure Code",
                FAILURE_CODES.get(code, UNKNOWN_FAILURE),
            ),
        ],
    }


def present(
    targets: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    duration_ms: int,
) -> dict[str, Any]:
    unreachable = [
        target
        for target in targets
        if not (results.get(target["device_id"]) or {}).get("reachable")
    ]
    reachable = len(targets) - len(unreachable)

    if not targets:
        message = (
            "No devices assigned to this probe. Add them in the management "
            "interface under Availability."
        )
    elif unreachable:
        # What is gone comes first: whoever reads this in a notification
        # wants the names, not the total.
        message = "%d of %d devices unreachable: %s" % (
            len(unreachable),
            len(targets),
            ", ".join(target["address"] for target in unreachable[:5]),
        )
        if len(unreachable) > 5:
            message += ", and %d more" % (len(unreachable) - 5)
    else:
        message = "All %d devices reachable" % len(targets)

    return {
        "version": 2,
        "status": "ok",
        "message": message[:2000],
        "channels": [
            channel(CHANNEL_TOTAL, "Devices", len(targets)),
            channel(CHANNEL_REACHABLE, "Reachable", reachable),
            # The channel an alert is set on. The threshold stays with
            # whoever operates PRTG - one printer off overnight is normal in
            # some shops and a call-out in others.
            channel(CHANNEL_UNREACHABLE, "Unreachable", len(unreachable)),
            channel(CHANNEL_FAILURE, "Failure Code", FAILURE_CODES["ok"]),
            channel(
                CHANNEL_DURATION,
                "Duration",
                duration_ms,
                unit="TimeResponse",
            ),
        ],
    }


def self_check(args: dict[str, Any]) -> dict[str, Any]:
    """Everything the sensor needs, without measuring or reporting anything.

    Run by the probe helper right after an activation, so what it verifies
    decides what a broken rollout gets rolled back for: the parameters, the
    probe's own configuration, the privileged helper, and that the platform
    answers over NATS.
    """
    problems = []
    try:
        validate(args)
    except ConfigError as problem:
        problems.append(str(problem))

    account = ""
    try:
        config = read_mpp_config(args["config"])
        account = config["user"]
        host, port, use_tls = split_url(config["url"])
    except ConfigError as problem:
        problems.append(str(problem))
    else:
        try:
            connection = NatsConnection(host, port, use_tls, config["ca"])
            try:
                connection.handshake(config["user"], config["password"])
                fetch_targets(connection, account)
            finally:
                connection.close()
        except BusError as problem:
            problems.append(str(problem))

    answer = call_helper({"action": "self-check"}, 10)
    if answer.get("result") != "ok":
        problems.append(
            answer.get("message") or "The privileged helper is not working."
        )

    if problems:
        return {
            "version": 2,
            "status": "ok",
            "message": ("Self-check failed: " + " ".join(problems))[:2000],
            "channels": [
                channel(
                    CHANNEL_TOTAL,
                    "Test Result",
                    LOOKUP_NO,
                    type="lookup",
                    lookup_name=ALARM_LOOKUP,
                )
            ],
        }

    return {
        "version": 2,
        "status": "ok",
        "message": "The sensor can measure and reach the platform as %s."
        % account,
        "channels": [
            channel(
                CHANNEL_TOTAL,
                "Test Result",
                LOOKUP_YES,
                type="lookup",
                lookup_name=ALARM_LOOKUP,
            )
        ],
    }


def work(args: dict[str, Any]) -> dict[str, Any]:
    if args["self_check"]:
        return self_check(args)

    try:
        validate(args)
        config = read_mpp_config(args["config"])
        host, port, use_tls = split_url(config["url"])
    except ConfigError as problem:
        return failure_result(str(problem), "no-config")

    started = time.time()
    try:
        connection = NatsConnection(host, port, use_tls, config["ca"])
    except BusError as problem:
        return failure_result(str(problem), "no-connection")

    try:
        connection.handshake(config["user"], config["password"])
        listed = fetch_targets(connection, config["user"])
        targets = [
            target
            for target in (listed.get("targets") or [])
            if isinstance(target, dict) and target.get("device_id")
        ]

        results = measure(targets, args["timeout_ms"], args["packets"]) if targets else {}
        if targets:
            send_report(
                connection,
                config["user"],
                str(listed.get("revision", "")),
                targets,
                results,
            )
    except BusError as problem:
        return failure_result(str(problem), "no-connection")
    finally:
        connection.close()

    return present(targets, results, int((time.time() - started) * 1000))


def fail(message: str, code: str = "internal-error") -> NoReturn:
    print(
        json.dumps(
            {
                "version": 2,
                "status": "error",
                "message": ("%s (%s)" % (message, code))[:2000],
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
