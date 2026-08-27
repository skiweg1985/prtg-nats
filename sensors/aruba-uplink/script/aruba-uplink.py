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

Gateway and credentials are sensor parameters, as in the bundled example
remote_ssh_linux_system_load.py. In PRTG they are written as placeholders,
so nothing site-specific has to be typed anywhere:

    --host %host --user %scriptplaceholder1 --password %scriptplaceholder2

%host is the address of the device the sensor sits on, the two placeholders
come from the credentials for script sensors in the device settings and are
inherited from the group. PRTG keeps their values out of the sensor log and
the settings - which is why redact() keeps them out of our messages too.

Structured after the bundled examples under
/opt/paessler/share/doc/examples/scripts/python.
"""

# std-lib
import argparse
import calendar
import datetime
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

# The state of the previous run. Only what a comparison needs, never a
# measured value: whoever wants the history has PRTG for it.
CACHE_PATH = "/tmp/prtg-sensor-aruba-uplink-%d.json" % os.getuid()

# Stable numbers for the "Failure Code" channel. They allow targeted
# alerting on a specific cause without parsing the message text.
FAILURE_CODES = {
    "ok": 0,
    "bad-request": 1,
    "login-failed": 2,
    "gateway-unreachable": 3,
    "bad-answer": 4,
    "no-quality-data": 5,
    "internal-error": 6,
}
UNKNOWN_FAILURE = 99

# This cause says nothing about the site, only about how the sensor was set
# up. Everything else - a gateway that stopped answering above all - is
# exactly the incident this sensor exists for and belongs in the
# measurement channels.
SENSOR_FAILURES = ("bad-request",)

# Parameters whose value must never appear in a message to PRTG. The
# credentials arrive over stdin, so they never reach the process list - but
# argparse quotes the offending value back at you, and a typo next to the
# password would carry it into the sensor message.
#
# The user name is deliberately not here: it is no secret, it stands in the
# sensor configuration anyway, and blanking a short one would shred every
# message containing those letters.
SECRET_PARAMETERS = ("--password",)

# Below this, a value cannot be masked without mangling the message beyond
# reading - "a" would blank every a. Such a message is dropped entirely
# instead.
SHORTEST_MASKABLE = 4

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
# 0 means "never reset". A site without a billing period wants a plain
# counter, and asking it to invent a day it does not have would be worse.
MIN_BILLING_DAY = 0
MAX_BILLING_DAY = 31

# The gateway counts its own data usage in these units, so the channel keeps
# them: two numbers for the same thing that differ by 5 % would send whoever
# compares them looking for a leak that is not there.
BYTES_PER_MB = 1048576

# A run talks to the gateway five times. The budget covers all of them plus
# the handshake; beyond it something is wrong that no answer will fix.
BUDGET_EXTRA_SECONDS = 30

NO_CREDENTIALS_MESSAGE = (
    "No %s configured. The sensor signs in to the gateway with a read-only "
    'account, so it needs "--user NAME --password SECRET" next to --host.'
)
NO_HOST_MESSAGE = (
    "No gateway configured. Add --host with the address of the Aruba "
    'gateway of this site, for example "--host 192.0.2.1".'
)
BAD_HOST_MESSAGE = (
    '--host takes a bare address or host name, not "%s". Neither a scheme '
    "nor a port belongs there; the sensor always talks to port 443."
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


def redact(message: str, tokens: list[str]) -> str:
    """Remove values of protected parameters from an error message.

    argparse names the value it stumbled over. With the password sitting in
    the same parameter string, a single typo would otherwise carry it into
    the sensor message - and from there into every notification PRTG sends.

    The PRTG manual is explicit about this for script sensors: a credential
    placeholder must not appear in anything the script prints, and PRTG
    itself keeps the value out of the sensor log and the settings. This
    function is what upholds that on our side.
    """
    values = []
    for index, token in enumerate(tokens):
        for name in SECRET_PARAMETERS:
            if token == name and index + 1 < len(tokens):
                values.append(tokens[index + 1])
            elif token.startswith("%s=" % name):
                values.append(token[len(name) + 1:])
    for value in values:
        if not value:
            continue
        if len(value) >= SHORTEST_MASKABLE:
            message = message.replace(value, "...")
        elif value in message:
            # Blanking a two-letter secret would leave a message nobody can
            # read - and one that still hints at the secret. Dropping it is
            # the only honest option.
            return ("Could not read the parameters. Check the configured "
                    "parameters.")
    return message


def setup():
    argparser = ReportingParser(
        description="The script reads the uplink state of an Aruba gateway.",
    )

    argparser.add_argument("--host", default="",
                           help="Address of the Aruba gateway of this site.")
    argparser.add_argument("--user", default="",
                           help="A read-only account on the gateway.")
    argparser.add_argument("--password", default="",
                           help="Its password.")
    argparser.add_argument("--primary", default=KIND_WIRED,
                           help="Which uplink kind is the main path: wired or "
                                "cellular.")
    argparser.add_argument("--backup", default=KIND_CELLULAR,
                           help="The alternative path: cellular, wired or "
                                "none.")
    argparser.add_argument("--backup-share", type=int, default=25,
                           help="Percentage of traffic on the backup from "
                                "which the traffic counts as moved over.")
    argparser.add_argument("--billing-day", type=int, default=1,
                           help="Day of the month the mobile data volume "
                                "starts over on, 0 for a counter that never "
                                "resets.")
    argparser.add_argument("--timeout-ms", type=int, default=10000,
                           help="Milliseconds to wait for a single answer.")
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


def validate(args: dict[str, Any]) -> None:
    """Check the parameters before a single byte flows.

    A pure function without network access: that way --self-check can run
    the same validation on the probe before anyone even enters the
    parameters in PRTG. Every message ends with the line that has to stand
    there instead - it is the only place an administrator can learn what was
    wrong.
    """
    if not args["host"]:
        raise ConfigError(NO_HOST_MESSAGE)
    # A scheme or a port here would silently end up in the Host header and
    # produce a connection error nobody can trace back to the parameter.
    if re.search(r"[/\s:@]", args["host"]):
        raise ConfigError(BAD_HOST_MESSAGE % args["host"])

    for name, value in (("--user", args["user"]),
                        ("--password", args["password"])):
        if not value:
            raise ConfigError(NO_CREDENTIALS_MESSAGE % name)

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
        ("--billing-day", args["billing_day"], MIN_BILLING_DAY,
         MAX_BILLING_DAY),
        ("--timeout-ms", args["timeout_ms"], MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    ):
        if not lowest <= value <= highest:
            raise ConfigError("%s must be between %d and %d, not %d."
                              % (name, lowest, highest, value))


class Gateway:
    """One connection to the gateway, shared by all requests.

    Cheaper than five handshakes, and the session token stays with the
    connection that carries it.
    """

    def __init__(self, host: str, timeout: float) -> None:
        self.host = host
        self.timeout = timeout
        self.token = ""
        # The gateway presents a self-signed certificate, so there is no
        # authority to verify against and no name to match; the connection
        # is encrypted but the far end is not authenticated. A deliberate
        # trade against having to carry a fingerprint per site - see the
        # limits section of the README.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.connection = http.client.HTTPSConnection(
            host, 443, timeout=timeout, context=context)

    def connect(self) -> None:
        try:
            self.connection.connect()
        except (OSError, socket.timeout, ssl.SSLError):
            raise Failed("gateway-unreachable",
                         "The gateway %s did not accept a connection."
                         % self.host) from None

    def request(self, method: str, path: str, body=None, headers=None):
        try:
            self.connection.request(method, path, body, headers or {})
            response = self.connection.getresponse()
            payload = response.read()
        except (OSError, socket.timeout, http.client.HTTPException):
            raise Failed("gateway-unreachable",
                              "The gateway %s stopped answering during the "
                              "measurement." % self.host) from None
        # A 401 can only mean the credentials were rejected - at the login,
        # and later when a session expired mid-run. Reporting that as an
        # unusable answer would send whoever reads the message looking for a
        # broken gateway instead of a wrong password.
        if response.status in (401, 403):
            raise Failed("login-failed",
                         "The gateway %s refused the credentials. Check "
                         "--user and --password of this sensor." % self.host)
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
                         "The gateway %s refused the credentials. Check "
                         "--user and --password of this sensor." % self.host)
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


def parse_uplink_totals(lines: list[str]) -> dict[str, int]:
    """Bytes carried per uplink kind since the gateway last started.

    The same sections as parse_uplink_stats, but the cumulative counters
    instead of the rates. "Intf" is the interface itself and therefore holds
    everything that crossed the uplink; the "VPN" line below it counts a
    subset and would understate a site whose traffic does not all go through
    the tunnel.

    These counters are wide enough to be trusted, which the gateway's own
    "Data usage" field is not - see the data volume section of the README.
    """
    totals: dict[str, int] = {}
    current = ""
    for line in lines:
        heading = re.match(r"\s*(Wired|Cellular)\s+VLAN\s*:", line, re.I)
        if heading:
            current = DEVICE_KINDS[heading.group(1).lower()]
            totals.setdefault(current, 0)
            continue
        if not current:
            continue
        counted = re.search(
            r"Intf Rx Bytes\s*:\s*(\d+)\s+Intf Tx Bytes\s*:\s*(\d+)", line)
        if counted:
            totals[current] += int(counted.group(1)) + int(counted.group(2))
    return totals


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


def day_in_month(day: int, year: int, month: int) -> int:
    """The billing day as it falls in one particular month.

    A day of 29 to 31 does not exist in every month. Moving it to the last
    day that month has keeps the period monthly - skipping it would let a
    February run on for two.
    """
    return min(day, calendar.monthrange(year, month)[1])


def billing_period_start(day: int, today: datetime.date) -> str:
    """The day the current volume period began, as an ISO date.

    Empty for a billing day of 0: that is the plain counter, which has no
    period to begin.
    """
    if day <= 0:
        return ""
    if today.day >= day_in_month(day, today.year, today.month):
        return today.replace(
            day=day_in_month(day, today.year, today.month)).isoformat()
    year = today.year - 1 if today.month == 1 else today.year
    month = 12 if today.month == 1 else today.month - 1
    return datetime.date(year, month,
                         day_in_month(day, year, month)).isoformat()


def usage_since(previous, counter: int, billing_day: int,
                today: datetime.date):
    """The mobile data volume of the current period, and the state to keep.

    Deliberately not the sum of the deltas between runs: a probe that was
    down for an hour would lose that hour for good. What is stored is the
    counter reading the period started at, so the volume stays a single
    difference no matter how often the sensor ran in between.

    "carry" holds what was counted before the gateway last restarted. Its
    interface counters begin at zero again after a restart, and without
    carry the volume would start over with them.

    Returns None as the volume while nothing can be said yet - the first run
    has no reading to measure against, and reporting the counter itself
    would claim months of traffic as this period's volume.
    """
    start = billing_period_start(billing_day, today)
    fresh = {"period_start": start, "baseline": counter, "carry": 0,
             "last": counter}
    if not isinstance(previous, dict) or "last" not in previous:
        return None, fresh

    baseline = int(previous.get("baseline", 0))
    carry = int(previous.get("carry", 0))
    last = int(previous.get("last", 0))
    # A counter that went backwards means the gateway restarted: it counts
    # from zero again, so what it had carried in this period moves into
    # carry. Only what is above the baseline - the rest was already there
    # when the period began and never belonged to this volume.
    if counter < last:
        carry += last - baseline
        baseline = 0
    # The traffic between the billing day and the first run after it is not
    # in any counter this sensor can reach. At a few minutes per run that is
    # a rounding error; claiming the whole month instead would not be.
    if previous.get("period_start") != start:
        return 0, fresh
    return (carry + counter - baseline,
            {"period_start": start, "baseline": baseline, "carry": carry,
             "last": counter})


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


def track_state(summary: dict[str, Any], counter: int, billing_day: int,
                now: float):
    """The changeovers of the last 24 hours and the mobile data volume.

    A single changeover is an event and needs no alarm; twenty of them are a
    defect that nobody would spot in the individual channels. What counts as
    a change is the triple of the three yes/no statements - not a byte rate,
    which fluctuates by nature.

    Both answers come from the same file, and therefore from one read and
    one write. Two functions each doing their own would have the second drop
    what the first had just stored.
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
    usage, usage_state = usage_since(stored.get("usage"), counter,
                                     billing_day,
                                     datetime.date.fromtimestamp(now))
    write_state(CACHE_PATH, {"marker": marker, "history": history[-100:],
                             "usage": usage_state})
    return len(history), usage


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
        # Filled in by work() once the state of the previous run is read.
        # Zero until then, and marked as not yet known: a volume of 0 and a
        # volume nobody has measured look the same in a channel.
        "data_usage_mb": 0.0,
        "data_usage_known": False,
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
                               summary["data_usage_mb"],
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
        "message": describe(args, summary, cellular, quality, code,
                            message)[:2000],
        "channels": channels,
    }


def describe(args: dict[str, Any], summary: dict[str, Any],
             cellular: dict[str, float],
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

    if KIND_CELLULAR in (args["primary"], args["backup"]):
        if not summary.get("data_usage_known"):
            parts.append("the mobile data volume is counted from this run on")
        # Whoever compares the channel against the gateway needs to know why
        # the two disagree - otherwise the sensor looks like the broken one.
        if cellular.get("data_usage", 0.0) < 0:
            parts.append("the gateway\'s own data counter reads %.0f MB and "
                         "is not used"
                         % cellular.get("data_usage", 0.0))
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
             "backup_share": 0.0, "utilisation": 0.0, "connected": 0,
             "data_usage_mb": 0.0, "data_usage_known": False}
    return present(args, empty, {}, {}, 0, 0, code, message)


def self_check(args: dict[str, Any]) -> dict[str, Any]:
    """Check the ability to run - and the parameters, if any came along.

    Deployment pipes a bare --self-check through the hardened service unit
    and requires an "ok" before it activates the sensor. At that point the
    gateway and its credentials are not known anywhere: they are entered in
    PRTG afterwards. So an invocation without parameters has to pass -
    otherwise every rollout of this sensor rolls itself back.

    If parameters do come along, they are checked. That is the only way to
    check a configuration before it is entered in PRTG - nobody checks it
    there any more. Reaching out to the gateway stays out of it either way,
    it would make the activation of a sensor depend on a device on the
    other side of the site.
    """
    configured = bool(args["host"] or args["user"] or args["password"])
    if configured:
        try:
            validate(args)
        except ConfigError as problem:
            fail(str(problem))

    message = ("The sensor runs and reads its parameters. The gateway and "
               "its credentials come from PRTG on the first real run.")
    if configured:
        message = ("The parameters are complete and %s is a usable gateway "
                   "address. Whether the credentials work shows on the first "
                   "real run." % args["host"])
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


def measure(args: dict[str, Any]):
    """One pass over the gateway. Always leaves without a session behind."""
    gateway = Gateway(args["host"], args["timeout_ms"] / 1000.0)
    try:
        gateway.connect()
        gateway.login(args["user"], args["password"])

        uplinks = parse_uplinks(gateway.show(COMMAND_UPLINK))
        for role in ("primary", "backup"):
            kind = args[role]
            if kind != KIND_NONE and kind not in uplinks:
                raise Failed("bad-request", KIND_ABSENT_MESSAGE
                             % (kind, role,
                                ", ".join(sorted(uplinks)) or "none"))

        # One answer, two readings: the rates say which uplink carries the
        # traffic right now, the counters below them how much it has carried.
        stats = data_lines(gateway.show(COMMAND_STATS))
        rates = parse_uplink_stats(stats)
        totals = parse_uplink_totals(stats)
        cellular = {}
        if KIND_CELLULAR in (args["primary"], args["backup"]):
            cellular = cellular_values(gateway.show(COMMAND_CELLULAR))
        quality = parse_link_quality(data_lines(gateway.show(COMMAND_DEBUG)))
    finally:
        gateway.close()

    return uplinks, rates, totals, cellular, quality


def work(args: dict[str, Any]):
    if args["self_check"]:
        return self_check(args)

    try:
        validate(args)
    except ConfigError as problem:
        fail(str(problem))

    started = time.time()
    previous = signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(int(args["timeout_ms"] / 1000.0) + BUDGET_EXTRA_SECONDS)
    try:
        uplinks, rates, totals, cellular, quality = measure(args)
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
    changes, usage = track_state(summary, totals.get(KIND_CELLULAR, 0),
                                 args["billing_day"], time.time())
    if usage is not None:
        summary["data_usage_mb"] = usage / BYTES_PER_MB
        summary["data_usage_known"] = True
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
