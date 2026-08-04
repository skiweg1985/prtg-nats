#!/usr/bin/env python3

"""Script v2 sensor: uplink state of an Aruba gateway.

The existing sensors of this repository describe how well a line carries -
loss, latency, throughput. None of them says *which* line that is. At a site
with an LTE backup this is the expensive gap: the gateway switches over
quietly, every other sensor keeps reporting green because the line does
carry, and the site runs on mobile data until the bill arrives. The reverse
is just as quiet - the backup deregisters, nobody notices, and the next real
outage finds nothing to fail over to.

The gateway knows all of this itself. The sensor asks it over the AOS
management API instead of deducing it from the outside:

    show uplink                     state and reachability per uplink
    show uplink stats               byte rates per uplink
    show uplink cellular details    RSRP, SINR, data usage
    show uplink debug               the gateway's own path measurement

Which uplink is the primary one comes from the sensor configuration, not
from the device: a gateway that load-balances reports no uplink as "backup"
at all (g_numBkpUplinks: 0), so only --primary and --backup can say what the
main path is meant to be.

Deliberately over HTTPS and not over SSH: Python ships no SSH client, so
that route would mean paramiko in a virtual environment on every probe, or
sshpass with the password in the process list. The API needs nothing but the
standard library and answers in JSON.

Structured after the bundled examples under
/opt/paessler/share/doc/examples/scripts/python.
"""

# std-lib
import argparse
import hashlib
import html
import http.client
import json
import os
import re
import shlex
import signal
import socket
import ssl
import sys
import time
import urllib.parse
from typing import Any, NoReturn

CONFIG_ROOT = "/etc/prtg-nats/sensors/aruba-uplink"
PROFILE_DIR = "%s/profiles" % CONFIG_ROOT

# The state of the previous run. Only what a comparison needs, never a
# measured value: whoever wants the history has PRTG for it.
CACHE_PATH = "/tmp/prtg-sensor-aruba-uplink-%d.json" % os.getuid()

PROFILE_HOST = "ARUBA_HOST"
PROFILE_USER = "ARUBA_USER"
PROFILE_PASSWORD = "ARUBA_PASSWORD"
PROFILE_FINGERPRINT = "ARUBA_CERT_SHA256"

# Stable numbers for the "Failure Code" channel. They allow targeted
# alerting on a specific cause without parsing the message text.
FAILURE_CODES = {
    "ok": 0,
    "bad-request": 1,
    "no-profile": 2,
    "cert-mismatch": 3,
    "login-failed": 4,
    "gateway-unreachable": 5,
    "bad-answer": 6,
    "no-quality-data": 7,
    "internal-error": 8,
}
UNKNOWN_FAILURE = 99

# These causes say nothing about the site, only about the sensor and how it
# was set up. Everything else - a gateway that stopped answering above all -
# is exactly the incident this sensor exists for and belongs in the
# measurement channels.
SENSOR_FAILURES = ("bad-request", "no-profile", "cert-mismatch")

# The path measurement is a secondary one. Losing it leaves every uplink
# channel intact and saying what it says, so it must not pull the result
# channel down with it.
MEASUREMENT_OK = ("ok", "no-quality-data")

# Separate channel ranges per role, so an additional value in one does not
# shift the channels of the other. Same reasoning as the target classes of
# the link-quality sensor.
PRIMARY_BASE = 30
BACKUP_BASE = 40

KIND_WIRED = "wired"
KIND_CELLULAR = "cellular"
KIND_NONE = "none"

# How the device spells the two kinds in "Uplink Type" and in the type
# marker of "show uplink debug".
DEVICE_KINDS = {"wired": KIND_WIRED, "cellular": KIND_CELLULAR}

COMMAND_UPLINK = "show uplink"
COMMAND_STATS = "show uplink stats"
COMMAND_CELLULAR = "show uplink cellular details"
COMMAND_DEBUG = "show uplink debug"

MIN_TIMEOUT_MS = 2000
MAX_TIMEOUT_MS = 60000
MIN_SHARE = 1
MAX_SHARE = 99

# A run talks to the gateway five times. The budget covers all of them plus
# the handshake; beyond it something is wrong that no answer will fix.
BUDGET_EXTRA_SECONDS = 30

NO_PROFILE_MESSAGE = (
    "No profile configured. Add --profile with the name of the credential "
    'profile deployed to this probe, for example "--profile site-north". '
    "It is put in place with \"./prtg-nats sensor profile aruba-uplink USER "
    'site-north --from-file FILE".'
)
PROFILE_MISSING_MESSAGE = (
    'No profile "%s" on this probe. Expected %s. Deploy it with '
    '"./prtg-nats sensor profile aruba-uplink USER %s --from-file FILE".'
)
PROFILE_INCOMPLETE_MESSAGE = (
    'The profile "%s" has no %s. The template next to the sensor lists '
    "every key."
)
FINGERPRINT_MISSING_MESSAGE = (
    "The profile %s carries no %s, so the gateway cannot be told apart from "
    "anything else answering on that address. Its certificate currently has "
    "the fingerprint %s - put that into the profile after checking it."
)
FINGERPRINT_MISMATCH_MESSAGE = (
    "The gateway presents a different certificate than the profile pins. "
    "Expected %s, received %s. Either the certificate was renewed - then "
    "update the profile - or this is not the gateway."
)
SAME_ROLE_MESSAGE = (
    "--primary and --backup cannot both be %s. A site has one main path; "
    'use "--backup none" if there is no alternative one.'
)
UNKNOWN_KIND_MESSAGE = (
    '%s must be one of %s, not "%s".'
)
KIND_ABSENT_MESSAGE = (
    "The gateway reports no %s uplink, but --%s names one. It knows %s. "
    "Either the configuration does not match this site, or the uplink is "
    "gone from the device entirely."
)

# prtg.standardlookups.yesno.stateyesok only knows 1 = Yes (Ok) and
# 2 = No (Error) - the counting comes from SNMP, where 1 means true and 2
# false. The lookup does not know a 0: PRTG then shows "undefined lookup
# value" and turns it into a mere warning where an error should stand.
LOOKUP_YES = 1
LOOKUP_NO = 2
ALARM_LOOKUP = "prtg.standardlookups.yesno.stateyesok"


class ConfigError(Exception):
    """The parameters do not add up to a runnable measurement."""


class Failed(Exception):
    """The measurement did not come about, and why.

    Carries the failure code with it so the caller never has to guess the
    cause back out of a message text.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Timeout(BaseException):
    """The run exceeded its wall clock.

    Deliberately not an Exception: the stages below catch broad exception
    classes to keep a single bad answer from killing the whole run, and they
    must not swallow this one.
    """


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


def setup():
    argparser = ReportingParser(
        description="The script reads the uplink state of an Aruba gateway.",
    )

    argparser.add_argument("--profile", default="",
                           help="Name of the credential profile on this probe.")
    argparser.add_argument("--primary", default=KIND_WIRED,
                           help="Which uplink kind is the main path: wired or "
                                "cellular.")
    argparser.add_argument("--backup", default=KIND_CELLULAR,
                           help="The alternative path: cellular, wired or "
                                "none.")
    argparser.add_argument("--backup-share", type=int, default=25,
                           help="Percentage of traffic on the backup from "
                                "which the traffic counts as moved over.")
    argparser.add_argument("--timeout-ms", type=int, default=10000,
                           help="Milliseconds to wait for a single answer.")
    argparser.add_argument("--self-check", action="store_true",
                           help="Only verify that the sensor is able to run.")

    try:
        # Is a terminal?
        if sys.stdin.isatty():
            args = argparser.parse_args()
        else:
            pipestring = sys.stdin.read().rstrip()
            args = argparser.parse_args(shlex.split(pipestring))

    except ConfigError as problem:
        fail(str(problem))
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


def validate(args: dict[str, Any]) -> None:
    """Check the parameters before a single byte flows.

    A pure function without network access: that way --self-check can run
    the same validation on the probe before anyone even enters the
    parameters in PRTG. Every message ends with the line that has to stand
    there instead - it is the only place an administrator can learn what was
    wrong.
    """
    if not args["profile"]:
        raise ConfigError(NO_PROFILE_MESSAGE)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args["profile"]):
        raise ConfigError(
            'The profile name "%s" is not a plain name. Allowed are letters, '
            "digits, dot, dash and underscore." % args["profile"])

    if args["primary"] not in (KIND_WIRED, KIND_CELLULAR):
        raise ConfigError(UNKNOWN_KIND_MESSAGE
                          % ("--primary", "wired or cellular", args["primary"]))
    if args["backup"] not in (KIND_WIRED, KIND_CELLULAR, KIND_NONE):
        raise ConfigError(UNKNOWN_KIND_MESSAGE
                          % ("--backup", "wired, cellular or none",
                             args["backup"]))
    if args["primary"] == args["backup"]:
        raise ConfigError(SAME_ROLE_MESSAGE % args["primary"])

    for name, value, lowest, highest in (
        ("--backup-share", args["backup_share"], MIN_SHARE, MAX_SHARE),
        ("--timeout-ms", args["timeout_ms"], MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    ):
        if not lowest <= value <= highest:
            raise ConfigError("%s must be between %d and %d, not %d."
                              % (name, lowest, highest, value))


def profile_path(name: str) -> str:
    return "%s/%s.env" % (PROFILE_DIR, name)


def read_profile(path: str):
    """Read the deployed profile.

    It reaches the probe through "./prtg-nats sensor profile", in the same
    protected area as the other credentials. The format is KEY=VALUE lines;
    the probe rejects everything else already at write time.

    The permission check costs nothing and makes sure a world-readable
    password is not used unnoticed - a file everyone may read is not a
    secret, and the sensor should not silently accept that.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        status = os.fstat(descriptor)
        if status.st_mode & 0o007:
            return None
        payload = os.read(descriptor, 65536).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(descriptor)

    values = {}
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip()
    return values or None


def load_credentials(args: dict[str, Any]) -> dict[str, str]:
    """The profile, checked for completeness.

    Everything raised here is a "no-profile", never a measurement: without
    credentials the sensor cannot even try, and reporting that as an outage
    of the site would point at the wrong thing.
    """
    path = profile_path(args["profile"])
    profile = read_profile(path)
    if profile is None:
        raise ConfigError(PROFILE_MISSING_MESSAGE
                          % (args["profile"], path, args["profile"]))
    for key in (PROFILE_HOST, PROFILE_USER, PROFILE_PASSWORD):
        if not profile.get(key):
            raise ConfigError(PROFILE_INCOMPLETE_MESSAGE
                              % (args["profile"], key))
    return profile


def normalise_fingerprint(value: str) -> str:
    """Accept a fingerprint the way a human copies it."""
    return re.sub(r"[^0-9a-f]", "", value.strip().lower())


class Gateway:
    """One connection to the gateway, with the certificate pinned.

    All five requests share it. That is not only cheaper than five
    handshakes - it also means the certificate is checked once, on the
    connection that then carries everything.
    """

    def __init__(self, host: str, timeout: float) -> None:
        self.host = host
        self.timeout = timeout
        self.token = ""
        # The gateway presents a self-signed certificate, so there is no
        # authority to verify against and no name to match. The check
        # happens against the pinned fingerprint instead, one step further
        # down - which is a stricter statement than any CA could make here.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.connection = http.client.HTTPSConnection(
            host, 443, timeout=timeout, context=context)

    def connect(self) -> str:
        """Open the connection and return the certificate fingerprint."""
        try:
            self.connection.connect()
            certificate = self.connection.sock.getpeercert(binary_form=True)
        except (OSError, socket.timeout, ssl.SSLError):
            raise Failed("gateway-unreachable",
                              "The gateway %s did not accept a connection."
                              % self.host) from None
        if not certificate:
            raise Failed("gateway-unreachable",
                              "The gateway %s presented no certificate."
                              % self.host)
        return hashlib.sha256(certificate).hexdigest()

    def request(self, method: str, path: str, body=None, headers=None):
        try:
            self.connection.request(method, path, body, headers or {})
            response = self.connection.getresponse()
            payload = response.read()
        except (OSError, socket.timeout, http.client.HTTPException):
            raise Failed("gateway-unreachable",
                              "The gateway %s stopped answering during the "
                              "measurement." % self.host) from None
        if response.status != 200:
            raise Failed("bad-answer",
                              "The gateway answered %d where 200 was expected."
                              % response.status)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise Failed("bad-answer",
                              "The gateway returned an answer that is not "
                              "readable as JSON.") from None
        if not isinstance(document, dict):
            raise Failed("bad-answer",
                              "The gateway returned an unexpected answer.")
        return document

    def login(self, user: str, password: str) -> None:
        body = urllib.parse.urlencode({"username": user, "password": password})
        document = self.request(
            "POST", "/v1/api/login", body,
            {"Content-Type": "application/x-www-form-urlencoded"})
        result = document.get("_global_result")
        if not isinstance(result, dict) or not result.get("UIDARUBA"):
            # The gateway's own wording is not repeated: it is written for a
            # browser and would only distract in a PRTG message.
            raise Failed("login-failed",
                              "The gateway %s refused the credentials of the "
                              "profile." % self.host)
        self.token = str(result["UIDARUBA"])

    def show(self, command: str) -> dict[str, Any]:
        path = "/v1/configuration/showcommand?command=%s&UIDARUBA=%s" % (
            urllib.parse.quote_plus(command),
            urllib.parse.quote_plus(self.token))
        return self.request("GET", path, None,
                            {"Cookie": "SESSION=%s" % self.token})

    def close(self) -> None:
        """Log out and hang up - in every case.

        A gateway holds only a limited number of sessions. A sensor running
        every few minutes and leaving them behind locks everyone out of the
        device within a day, including the people who would have to fix it.
        Failure here is therefore ignored, but skipping it is not an option.
        """
        try:
            if self.token:
                self.request("GET", "/v1/api/logout?UIDARUBA=%s"
                             % urllib.parse.quote_plus(self.token))
        except Exception:
            pass
        try:
            self.connection.close()
        except Exception:
            pass


def data_lines(document: dict[str, Any]) -> list[str]:
    """The free-text part of an answer, as single lines.

    Two properties of the API make this necessary. Its text comes HTML
    escaped, so an address in quotes arrives as &#39;…&#39;, and one entry
    of "_data" often holds several lines at once. Both only show up against
    the real device.
    """
    lines: list[str] = []
    for entry in document.get("_data") or []:
        lines.extend(html.unescape(str(entry)).split("\n"))
    return lines


def parse_key_values(lines: list[str]) -> dict[str, str]:
    """Read a block of "Key : Value" lines.

    Used for "show uplink cellular details". Lines without a colon are
    headings or separators and are skipped rather than guessed at.
    """
    values: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def parse_uplink_stats(lines: list[str]) -> dict[str, int]:
    """Byte rates per uplink kind from "show uplink stats".

    A line like "Wired VLAN: 4086 (dhcp_inet)" opens a section; the rates
    below belong to it until the next one. Both directions are added up: for
    the question of which path carries the traffic, the direction does not
    matter.
    """
    rates: dict[str, int] = {}
    current = ""
    for line in lines:
        heading = re.match(r"\s*(Wired|Cellular)\s+VLAN\s*:", line, re.I)
        if heading:
            current = DEVICE_KINDS[heading.group(1).lower()]
            rates.setdefault(current, 0)
            continue
        if not current:
            continue
        measured = re.search(
            r"rx_bytes/sec\s*:\s*(\d+)\s+tx_bytes/sec\s*:\s*(\d+)", line)
        if measured:
            rates[current] += int(measured.group(1)) + int(measured.group(2))
    return rates


def parse_link_quality(lines: list[str]) -> dict[str, dict[str, float]]:
    """Latency, jitter, loss and R value per uplink kind.

    From "show uplink debug", where the gateway keeps the result of its own
    health check:

        link: 0x… type: 1(Wired), link_id: 101
          probe ip: '…' latency: 14800 jitter: 192 pkt_loss: 0.000% Rvalue: …

    A "link:" line opens a block and names the kind, every "probe ip:" line
    below belongs to it. The gateway probes several targets per uplink;
    averaging over them is the honest summary - a single target says as much
    about the path as a single ping does.

    Latency and jitter arrive in microseconds. The channels report
    milliseconds, like every other sensor here.

    This is the one command of the four that is not documented API surface.
    If a future AOS release changes the wording, the regular expressions stop
    matching and the caller reports no-quality-data - a sensor that fails
    entirely over a secondary measurement would be the worse answer.
    """
    collected: dict[str, list[tuple[float, float, float, float]]] = {}
    current = ""
    for line in lines:
        opening = re.search(r"\blink:\s*\S+\s+type:\s*\d+\((\w+)\)", line)
        if opening:
            current = DEVICE_KINDS.get(opening.group(1).lower(), "")
            if current:
                collected.setdefault(current, [])
            continue
        if not current:
            continue
        probe = re.search(
            r"probe ip:.*?latency:\s*(\d+)\s+jitter:\s*(\d+)\s+"
            r"pkt_loss:\s*([\d.]+)%\s+Rvalue:\s*([\d.]+)", line)
        if probe:
            collected[current].append((
                int(probe.group(1)) / 1000.0,
                int(probe.group(2)) / 1000.0,
                float(probe.group(3)),
                float(probe.group(4)),
            ))

    quality: dict[str, dict[str, float]] = {}
    for kind, probes in collected.items():
        if not probes:
            continue
        count = float(len(probes))
        quality[kind] = {
            "latency_ms": sum(entry[0] for entry in probes) / count,
            "jitter_ms": sum(entry[1] for entry in probes) / count,
            "loss_percent": sum(entry[2] for entry in probes) / count,
            "quality": sum(entry[3] for entry in probes) / count,
        }
    return quality


def parse_percent(value: str) -> float:
    match = re.search(r"([\d.]+)", value or "")
    return float(match.group(1)) if match else 0.0


def parse_uplinks(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Condense the uplink table into one entry per kind.

    A gateway may carry several uplinks of the same kind - two WAN ports,
    say. The kind then counts as available as soon as one of them stands,
    and the utilisation is the worst of them: an uplink at its limit is the
    one that matters, not the average with its idle neighbour.
    """
    table = document.get("Uplink Management Table")
    if not isinstance(table, list):
        raise Failed("bad-answer",
                          "The gateway did not report an uplink table.")

    uplinks: dict[str, dict[str, Any]] = {}
    for row in table:
        if not isinstance(row, dict):
            continue
        kind = DEVICE_KINDS.get(str(row.get("Uplink Type", "")).lower())
        if not kind:
            continue
        up = (str(row.get("State", "")).lower() == "connected"
              and str(row.get("Reachability", "")).lower() == "reachable")
        utilisation = parse_percent(str(row.get("B/w utiln", "")))
        entry = uplinks.setdefault(kind, {"up": False, "count": 0,
                                          "utilisation": 0.0})
        entry["up"] = entry["up"] or up
        entry["count"] += 1
        entry["utilisation"] = max(entry["utilisation"], utilisation)
    return uplinks


def cellular_values(document: dict[str, Any]) -> dict[str, float]:
    """RSRP, SINR and data usage - and nothing else.

    The same block carries IMEI, IMSI, cell ID and GPS position. Those are
    device and location identifiers; they are not read, so they cannot end
    up in a channel, a message or a log.
    """
    values = parse_key_values(data_lines(document))
    result: dict[str, float] = {}
    for key, target in (("RSRP (LTE)", "rsrp"), ("SINR", "sinr"),
                        ("Data usage", "data_usage")):
        raw = values.get(key)
        if raw is None:
            continue
        match = re.search(r"(-?[\d.]+)", raw)
        if match:
            result[target] = float(match.group(1))
    return result


def read_state(path: str):
    """The state of the previous run.

    The ownership and mode check costs nothing and keeps the sensor safe
    even if /tmp should ever not be service-private.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        status = os.fstat(descriptor)
        if status.st_uid != os.getuid() or status.st_mode & 0o077:
            return None
        payload = os.read(descriptor, 65536).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(descriptor)

    try:
        stored = json.loads(payload)
    except ValueError:
        return None
    return stored if isinstance(stored, dict) else None


def write_state(path: str, state: dict[str, Any]) -> None:
    """Store the state atomically.

    A failure has no consequences beyond a missing comparison in the next
    run. That is unfortunate, but no reason to discard a valid measurement.
    """
    temporary = "%s.%d.tmp" % (path, os.getpid())
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600)
        try:
            os.write(descriptor, json.dumps(state).encode("utf-8"))
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def track_changes(summary: dict[str, Any], now: float) -> int:
    """Count the state changes of the last 24 hours.

    A single changeover is an event and needs no alarm; twenty of them are a
    defect that nobody would spot in the individual channels. What counts as
    a change is the triple of the three yes/no statements - not a byte rate,
    which fluctuates by nature.
    """
    marker = [bool(summary["primary_up"]), bool(summary["backup_up"]),
              bool(summary["on_primary"])]
    stored = read_state(CACHE_PATH) or {}
    history = [stamp for stamp in stored.get("history") or []
               if isinstance(stamp, (int, float))
               and not isinstance(stamp, bool)
               # A clock set back must not keep entries alive forever.
               and 0 <= now - stamp <= 86400]
    # The first run has nothing to compare against. Counting that as a
    # change would make every freshly deployed sensor report a changeover
    # that never happened.
    if "marker" in stored and stored["marker"] != marker:
        history.append(now)
    write_state(CACHE_PATH, {"marker": marker, "history": history[-100:]})
    return len(history)


def summarise(args: dict[str, Any], uplinks: dict[str, dict[str, Any]],
              rates: dict[str, int]) -> dict[str, Any]:
    """Turn the readings into the statements the channels make."""
    primary = uplinks.get(args["primary"]) or {}
    backup = (uplinks.get(args["backup"]) or {}
              if args["backup"] != KIND_NONE else {})

    primary_rate = rates.get(args["primary"], 0)
    backup_rate = (rates.get(args["backup"], 0)
                   if args["backup"] != KIND_NONE else 0)
    total = primary_rate + backup_rate
    backup_share = (backup_rate * 100.0 / total) if total else 0.0

    # Without a backup the question is simply whether the primary stands and
    # carries. With one it is a threshold: a gateway that load-balances puts
    # a little traffic on the backup permanently, and comparing against zero
    # would alarm forever at such a site.
    if args["backup"] == KIND_NONE:
        on_primary = bool(primary.get("up"))
    else:
        on_primary = (bool(primary.get("up"))
                      and backup_share < args["backup_share"])

    return {
        "primary_up": bool(primary.get("up")),
        "backup_up": bool(backup.get("up")) if backup else False,
        "on_primary": on_primary,
        "backup_share": backup_share,
        "utilisation": float(primary.get("utilisation", 0.0)),
        "connected": sum(1 for entry in uplinks.values() if entry.get("up")),
    }


def lookup_value(condition) -> int:
    """Translate a yes/no into the values PRTG understands."""
    return LOOKUP_YES if condition else LOOKUP_NO


def channel(identifier: int, name: str, value, **extra) -> dict[str, Any]:
    result = {"id": identifier, "name": name, "type": "integer", "value": value}
    result.update(extra)
    return result


def number(identifier: int, name: str, value, **extra) -> dict[str, Any]:
    """A channel with decimal places.

    Necessary for the path measurement: a wired uplink answers in fractions
    of a millisecond of jitter, and as an integer a permanent 0 would stand
    there - no statement at all exactly where the line is best.
    """
    return channel(identifier, name, round(float(value), 2), type="float",
                   **extra)


def quality_channels(base: int, label: str,
                     values: dict[str, float]) -> list[dict[str, Any]]:
    """The four path channels of one uplink.

    Present even without a measurement, with zeros. The channel structure
    follows the configuration, never the result: a channel that disappears
    during an outage and returns afterwards tears its history apart in PRTG.
    """
    return [
        number(base, "%s Latency" % label, values.get("latency_ms", 0.0),
               kind="time_milliseconds"),
        number(base + 1, "%s Jitter" % label, values.get("jitter_ms", 0.0),
               kind="time_milliseconds"),
        number(base + 2, "%s Packet Loss" % label,
               values.get("loss_percent", 0.0), kind="percent"),
        number(base + 3, "%s Quality" % label, values.get("quality", 0.0),
               kind="custom", display_unit="R"),
    ]


def present(args: dict[str, Any], summary: dict[str, Any],
            cellular: dict[str, float], quality: dict[str, dict[str, float]],
            changes: int, duration_ms: int, code: str,
            message: str) -> dict[str, Any]:
    """Bring the result into the shape PRTG expects."""
    succeeded = code in MEASUREMENT_OK
    has_backup = args["backup"] != KIND_NONE
    has_cellular = KIND_CELLULAR in (args["primary"], args["backup"])

    channels = [
        channel(10, "Test Result", lookup_value(succeeded), type="lookup",
                lookup_name=ALARM_LOOKUP),
        channel(11, "Uplinks Connected", summary["connected"]),
        channel(12, "Primary Uplink Up", lookup_value(summary["primary_up"]),
                type="lookup", lookup_name=ALARM_LOOKUP),
        number(15, "Primary Bandwidth Utilisation", summary["utilisation"],
               kind="percent"),
        channel(16, "Uplink Changes 24h", changes),
        channel(17, "Test Duration", int(duration_ms),
                kind="time_milliseconds"),
        channel(18, "Failure Code", FAILURE_CODES.get(code, UNKNOWN_FAILURE)),
        channel(20, "On Primary Uplink", lookup_value(summary["on_primary"]),
                type="lookup", lookup_name=ALARM_LOOKUP),
    ]
    if has_backup:
        channels.append(
            channel(13, "Backup Uplink Up", lookup_value(summary["backup_up"]),
                    type="lookup", lookup_name=ALARM_LOOKUP))
        channels.append(
            number(14, "Traffic on Backup", summary["backup_share"],
                   kind="percent"))
    if has_cellular:
        channels.append(number(19, "LTE RSRP", cellular.get("rsrp", 0.0),
                               kind="custom", display_unit="dBm"))
        channels.append(number(21, "LTE SINR", cellular.get("sinr", 0.0),
                               kind="custom", display_unit="dB"))
        channels.append(number(22, "LTE Data Usage",
                               cellular.get("data_usage", 0.0),
                               kind="custom", display_unit="MB"))

    channels.extend(quality_channels(PRIMARY_BASE, "Primary",
                                     quality.get(args["primary"], {})))
    if has_backup:
        channels.extend(quality_channels(BACKUP_BASE, "Backup",
                                         quality.get(args["backup"], {})))

    # Ascending, so the channel list in PRTG has the same order no matter
    # which optional values are present.
    channels.sort(key=lambda entry: entry["id"])

    return {
        "version": 2,
        "status": "ok",
        "message": describe(args, summary, quality, code, message)[:2000],
        "channels": channels,
    }


def describe(args: dict[str, Any], summary: dict[str, Any],
             quality: dict[str, dict[str, float]], code: str,
             message: str) -> str:
    if code not in MEASUREMENT_OK:
        return "%s (%s)" % (message or "The measurement did not run", code)

    parts = []
    if not summary["primary_up"]:
        parts.append("The %s uplink is down" % args["primary"])
    elif not summary["on_primary"]:
        parts.append("The traffic left the %s uplink: %.1f %% on the backup"
                     % (args["primary"], summary["backup_share"]))
    else:
        parts.append("%s carries the traffic" % args["primary"].capitalize())

    if args["backup"] != KIND_NONE and not summary["backup_up"]:
        # First place after the primary statement: a missing backup is what
        # nobody notices until the day it is needed.
        parts.append("the %s backup is not available" % args["backup"])

    measured = quality.get(args["primary"])
    if measured:
        parts.append("%.1f ms, %.1f ms jitter, %.1f %% loss, quality %.0f"
                     % (measured["latency_ms"], measured["jitter_ms"],
                        measured["loss_percent"], measured["quality"]))
    elif message:
        parts.append(message)
    return ", ".join(parts)


def failure_result(args: dict[str, Any], code: str,
                   message: str) -> dict[str, Any]:
    """Report a failure as a valid measurement.

    The alarm hangs on the "Test Result" channel, not on the sensor status.
    That keeps the history of the measurement channels readable across an
    outage. Only when the sensor itself cannot work does it become a sensor
    error - a gateway that stopped answering is not that case, it is the
    incident.
    """
    if code in SENSOR_FAILURES:
        fail("%s (%s)" % (message, code))
    empty = {"primary_up": False, "backup_up": False, "on_primary": False,
             "backup_share": 0.0, "utilisation": 0.0, "connected": 0}
    return present(args, empty, {}, {}, 0, 0, code, message)


def self_check(args: dict[str, Any]) -> dict[str, Any]:
    """Check the ability to run - without touching the network.

    Deployment pipes this through the hardened service unit and requires an
    "ok" before it activates the sensor. Reaching out to the gateway here
    would make the activation of a sensor depend on a device on the other
    side of the site.
    """
    try:
        validate(args)
        credentials = load_credentials(args)
    except ConfigError as problem:
        fail(str(problem))

    pinned = normalise_fingerprint(credentials.get(PROFILE_FINGERPRINT, ""))
    message = ('The profile "%s" is readable and complete.'
               % args["profile"])
    if len(pinned) != 64:
        message += (" It carries no usable %s yet, so the first run will "
                    "report the fingerprint of the gateway instead of "
                    "measuring." % PROFILE_FINGERPRINT)
    return {
        "version": 2,
        "status": "ok",
        "message": message,
        "channels": [
            channel(10, "Test Result", LOOKUP_YES, type="lookup",
                    lookup_name=ALARM_LOOKUP),
        ],
    }


def raise_timeout(_signum, _frame) -> NoReturn:
    raise Timeout()


def measure(args: dict[str, Any], credentials: dict[str, str]):
    """One pass over the gateway. Always leaves without a session behind."""
    gateway = Gateway(credentials[PROFILE_HOST], args["timeout_ms"] / 1000.0)
    try:
        seen = gateway.connect()
        pinned = normalise_fingerprint(credentials.get(PROFILE_FINGERPRINT, ""))
        if len(pinned) != 64:
            raise Failed("cert-mismatch", FINGERPRINT_MISSING_MESSAGE
                         % (args["profile"], PROFILE_FINGERPRINT, seen))
        if pinned != seen:
            raise Failed("cert-mismatch",
                         FINGERPRINT_MISMATCH_MESSAGE % (pinned, seen))

        gateway.login(credentials[PROFILE_USER], credentials[PROFILE_PASSWORD])

        uplinks = parse_uplinks(gateway.show(COMMAND_UPLINK))
        for role in ("primary", "backup"):
            kind = args[role]
            if kind != KIND_NONE and kind not in uplinks:
                raise Failed("bad-request", KIND_ABSENT_MESSAGE
                             % (kind, role,
                                ", ".join(sorted(uplinks)) or "none"))

        rates = parse_uplink_stats(data_lines(gateway.show(COMMAND_STATS)))
        cellular = {}
        if KIND_CELLULAR in (args["primary"], args["backup"]):
            cellular = cellular_values(gateway.show(COMMAND_CELLULAR))
        quality = parse_link_quality(data_lines(gateway.show(COMMAND_DEBUG)))
    finally:
        gateway.close()

    return uplinks, rates, cellular, quality


def work(args: dict[str, Any]):
    if args["self_check"]:
        return self_check(args)

    try:
        validate(args)
        credentials = load_credentials(args)
    except ConfigError as problem:
        fail(str(problem))

    started = time.time()
    previous = signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(int(args["timeout_ms"] / 1000.0) + BUDGET_EXTRA_SECONDS)
    try:
        uplinks, rates, cellular, quality = measure(args, credentials)
    except Failed as problem:
        return failure_result(args, problem.code, problem.message)
    except Timeout:
        return failure_result(
            args, "gateway-unreachable",
            "The gateway did not finish answering within the budget.")
    except Exception:
        # The exception is never printed - it could carry a credential or an
        # internal address.
        return failure_result(args, "internal-error",
                              "The measurement failed unexpectedly")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)

    summary = summarise(args, uplinks, rates)
    changes = track_changes(summary, time.time())
    duration_ms = int((time.time() - started) * 1000)

    code, message = "ok", ""
    if not quality.get(args["primary"]):
        code = "no-quality-data"
        message = ("the gateway reported no path measurement, so the quality "
                   "channels stay at 0")
    return present(args, summary, cellular, quality, changes, duration_ms,
                   code, message)


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
