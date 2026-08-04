#!/usr/bin/env python3

"""Script v2 sensor: check a site's uplink against a self-operated
measurement endpoint.

The sensor answers whether a site has the path to its own services at the
required quality - not how fast it reaches "the internet". It measures with
iperf3 against a self-operated endpoint, usually placed where the site VPNs
terminate.

Why not against speedtest.net: its server selection decides which path is
measured, and it changes. Your own endpoint always sits in the same place,
is always reachable and measures exactly the path VPN, telephony and
terminal sessions travel.

The pacing is done by iperf3 itself (--bitrate), not by the script. That is
the essential difference to the internet-speed sensor: there the pacing had
to be built by hand, because a third-party test server cannot be steered.

Structured after the bundled examples under
/opt/paessler/share/doc/examples/scripts/python.
"""

# std-lib
import argparse
import base64
import fcntl
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from typing import Any, NoReturn

IPERF = "/usr/bin/iperf3"

# The sensor runs as the service user; prtg.mpprobe.service brings its own
# /tmp via PrivateTmp. The id in the name keeps the file separate even if a
# future release drops that.
CACHE_PATH = "/tmp/prtg-sensor-iperf-throughput-%d.json" % os.getuid()

# The regular case: the credentials live on the probe, not in the sensor
# parameters. All of your own probes share the same ones; a change is then a
# deployment, not an edit on every single PRTG sensor. And a password in the
# parameter field would sit readable for everyone in the sensor
# configuration.
#
# The exception is still provided for: on a probe managed by someone else no
# file can be placed, and several endpoints can have different passwords.
# That is what --password and --public-key are for. What the sensor does not
# do is guess: without --username it measures unauthenticated, and that is a
# decision somebody makes, not the side effect of a missing file.
CONFIG_ROOT = "/etc/prtg-nats/sensors/iperf-throughput"
PROFILE_DIR = "%s/profiles" % CONFIG_ROOT
PUBLIC_KEY_PATH = "%s/public.pem" % CONFIG_ROOT
# Keys expected in the profile. The endpoint's key is not a secret, but it
# travels in the same envelope: otherwise one manual step would remain after
# deployment, and that step gets forgotten.
PROFILE_PASSWORD = "IPERF3_PASSWORD"
PROFILE_PUBLIC_KEY = "IPERF3_PUBLIC_KEY_B64"
# Which endpoint the profile belongs to. With these the profile describes a
# measurement path and not only the secret to walk it, so a second endpoint is
# a second profile rather than a second set of parameters in PRTG.
PROFILE_HOST = "IPERF3_HOST"
PROFILE_PORT = "IPERF3_PORT"
PROFILE_USERNAME = "IPERF3_USERNAME"

DEFAULT_PORT = 5201

# The lock spans all sensors that measure throughput - not just the runs of
# this one. The reason is measured: an internet-speed run in mode maximum
# saturates the line for about twenty seconds. If an assurance measurement
# falls into that, it misses its target rate and the alarm fires over a
# perfectly healthy line. With separate lock files the two sensors knew
# nothing of each other - exactly the deployment both READMEs recommend.
#
# The path is spelled out identically in every sensor. There is no shared
# library and there should not be one: sensors are deployed individually and
# have to stay runnable on their own. tests/check-static.sh keeps the
# spellings aligned.
THROUGHPUT_LOCK_PATH = "/tmp/prtg-sensor-throughput-%d.lock" % os.getuid()

# Stable numbers for the "Failure Code" channel. They allow targeted
# alerting on a specific cause without parsing the message text.
FAILURE_CODES = {
    "ok": 0,
    "tool-missing": 1,
    "credentials-unreadable": 2,
    "server-unreachable": 3,
    "auth-failed": 4,
    "busy": 5,
    "timeout": 6,
    "test-failed": 7,
}
UNKNOWN_FAILURE = 99

# These causes say nothing about the line, only about the sensor itself.
# They do not belong in the measurement channels.
SENSOR_FAILURES = ("tool-missing", "credentials-unreadable")

TOOL_MISSING_MESSAGE = (
    "iperf3 is not installed on this probe (tool-missing). Install it with "
    '"apt-get install iperf3" on the probe; the sensor needs no other package.'
)

# A lock file could not be created. Then the measurement runs without a
# lock: a sensor that would rather report nothing at all would be the worse
# answer.
LOCK_UNAVAILABLE = -1

# How long each direction transfers. Five seconds, and that is measured
# rather than adopted. Checked pairwise alternating against ten seconds, so
# congestion events hit both variants alike: 828.8 / 830.7 / 778.9 / 785.4
# Mbit/s versus 564.9 / 689.6 / 653.7 / 870.2. The short measurement reads
# higher and scatters six times less.
#
# The per-second trace explains it: after TCP slow start in the first
# second comes the peak, and from about second six the throughput drops - a
# burst allowance or building congestion. Whoever measures longer averages
# that drop in.
#
# With a target rate it does not matter anyway: there iperf3 does the
# pacing, and five seconds gave 30.19 Mbit/s three times against 30.09 over
# ten.
#
# The price is in the README: a line that only throttles after several
# seconds goes unnoticed. Short runs also help because an endpoint accepts
# only one client at a time and has to serve several sites within the same
# hour.
HOLD_SECONDS = 5
# Below two seconds the measurement becomes unusable: the first second
# belongs to TCP slow start, and with UDP the accounting tips over - on a
# probe, a three-second run reported 1000 Mbit/s sent, 17.7 received and
# still 0.00 % loss. The time budget bounds the upper end anyway; the limit
# here only catches the transposed digit.
MIN_HOLD_SECONDS = 2
MAX_HOLD_SECONDS = 60
# How far the achieved rate may sit below the target rate and still count
# as held. Same tolerance as in the internet-speed sensor, so both sensors
# speak the same language.
SLIP_TOLERANCE = 0.05
# From this share of lost packets on, a direction counts as missed.
# Deliberately not zero: a UDP measurement shows occasional single losses
# even on a healthy line - measured on a probe, three runs with 0.00 / 0.28
# / 0.00 percent over the same path.
MAX_LOSS_PERCENT = 1.0
# Catches the most common mix-up: the channels report kbit/s, the parameter
# expects Mbit/s. Whoever enters 30000 means 30.
MIN_TARGET_MBIT = 1
MAX_TARGET_MBIT = 10000

# Time budget for the whole run. Two directions of transfer plus setup;
# anything beyond that is a fault, not a slow line.
DEFAULT_TIMEOUT_SECONDS = 60
# A single iperf3 invocation gets its own deadline. Without it the sensor
# would hang on an endpoint that accepts the connection and then goes
# silent.
RUN_MARGIN_SECONDS = 15

DOCUMENTATION_HINT = (
    "All parameters are listed by putting \"--help\" in the sensor's parameter "
    "field; the full documentation is sensors/iperf-throughput/README.md in "
    "the prtg-nats repository."
)
NO_SERVER_MESSAGE = (
    "No measurement endpoint given. --server is required. Use "
    '"--server iperf.example.com --download-mbit 30 --upload-mbit 10" to check '
    'that the path holds those rates, or "--server iperf.example.com" alone to '
    "measure what it carries. " + DOCUMENTATION_HINT
)
# argparse writes its help as plain text to stdout and exits. In a
# terminal that is right; through PRTG the sensor would produce output that
# is not JSON, and PRTG would show a parse error instead of the parameters.
# But whoever types "--help" into the parameter field wants exactly this
# list - it is the only place to find it without access to the probe.
HELP_MESSAGE = (
    "Parameters of this sensor:\n"
    "  --server HOST              required, the iperf3 endpoint\n"
    "  --download-mbit MBIT       download target in megabit per second\n"
    "  --upload-mbit MBIT         upload target in megabit per second\n"
    "                             (a target selects its direction and turns "
    "the run into an assurance; without any, both directions are measured for "
    "what the path carries)\n"
    "  --port PORT                default 5201\n"
    "  --seconds N                how long to transfer per direction, "
    "default 5. Longer runs read lower on a line that shapes after a burst\n"
    "  --udp                      measure with UDP: reports packet loss and "
    "jitter instead of retransmits and latency. Needs a target rate\n"
    "  --username NAME            authenticate as NAME; without it the run is "
    "unauthenticated\n"
    "  --password SECRET          password here instead of on the probe, for "
    "probes managed by someone else\n"
    "  --public-key PATH          the endpoint's public key, if it is not in "
    "the deployed profile\n"
    "  --profile NAME             deployed credential profile, default "
    "\"default\". One per endpoint when passwords differ\n"
    "  --measure-every-minutes N  default 60, 0 measures on every scan\n"
    "  --timeout-seconds N        default 60\n"
    "  --self-check               check that the sensor can run, without "
    "measuring\n"
    "Example: --server iperf.example.com --username prtg-probe "
    "--download-mbit 30 --upload-mbit 10\n"
    "     or: --server iperf.example.com --username prtg-probe   (capacity, "
    "no target)\n"
    "Full documentation: sensors/iperf-throughput/README.md in the prtg-nats "
    "repository."
)
HELP_TOKENS = ("--help", "-h")
# Without a target rate the sensor measures the path's capacity. With UDP
# that would not work: iperf3 then sends at its default of one megabit per
# second - measured on a probe - and dutifully reported "no loss". That
# would look like a healthy line and say nothing about it.
UDP_WITHOUT_TARGET_MESSAGE = (
    "--udp needs a target rate. UDP does not adapt to the line, so without "
    "--download-mbit or --upload-mbit iperf3 would send at its own default of "
    "1 Mbit/s and report no loss - which says nothing about the line. Add a "
    "target rate, or drop --udp to measure how much the path carries."
)
IMPLAUSIBLE_TARGET_MESSAGE = (
    "A target rate of %d Mbit/s is not plausible. The value is in megabit per "
    "second, not kilobit per second - the channels report kbit/s, the "
    'parameter does not. For a 30 Mbit/s target write "%s 30".'
)
IMPLAUSIBLE_SECONDS_MESSAGE = (
    "--seconds %d is outside the usable range of %d to %d. Below that the "
    "first second of TCP slow start dominates the result; above it the run "
    "loads the line longer than any measurement needs."
)
TIMEOUT_TOO_SHORT_MESSAGE = (
    "--timeout-seconds %d is too small for --seconds %d: two directions plus "
    "connection setup need at least %d seconds. Raise the timeout or lower "
    "--seconds."
)
NEGATIVE_INTERVAL_MESSAGE = (
    "--measure-every-minutes must not be negative. Use "
    '"--measure-every-minutes 60" for hourly measurements, or 0 to measure '
    "on every scan."
)
CREDENTIALS_MISSING_MESSAGE = (
    "--username was given but no password was found: %s is missing, "
    "unreadable, or world-readable (credentials-unreadable). Deploy it with "
    '"./prtg-nats sensor profile iperf-throughput PROBE default '
    '--from-file FILE", or pass the password with "--password" if this probe '
    "is managed by someone else, or drop --username if the endpoint accepts "
    "unauthenticated clients."
)
KEY_UNREADABLE_MESSAGE = (
    "The public key in the deployed profile is not readable "
    "(credentials-unreadable). %s must hold the endpoint's public.pem, "
    "base64 encoded on a single line." % PROFILE_PUBLIC_KEY
)
KEY_MISSING_MESSAGE = (
    "--username was given but the endpoint's public key is not at %s "
    "(credentials-unreadable). iperf3 encrypts the credentials with it and "
    'refuses to authenticate without it. Use "--public-key PATH" if it lives '
    "elsewhere on this probe."
)
PASSWORD_WITHOUT_USER_MESSAGE = (
    "--password only applies together with --username. Add the user name the "
    "endpoint expects, or drop --password to measure unauthenticated."
)

# Parameters whose value does not belong in a message to PRTG. --password
# is the obvious case; --server carries the name of an internal endpoint.
SECRET_PARAMETERS = ("--server", "--password")


class Failed(Exception):
    """The measurement could not be carried out."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigError(Exception):
    """The parameters do not add up to a runnable measurement.

    Separate from Failed because it says nothing about the line: a
    configuration error becomes a sensor error and gets no measurement
    channels.
    """


class Timeout(BaseException):
    """The measurement's time budget is used up.

    Deliberately inherits from BaseException: the measurement stages catch
    iperf3 failures with "except Exception" to translate them into a
    channel value. An alarm signal would drown in that and come out as
    "test-failed" instead of ending the run.
    """


def raise_timeout(signum, frame) -> NoReturn:
    raise Timeout()


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

    argparse quotes the faulty input verbatim, and that is intended - the
    typo is the very hint this is about. Only --server carries the name of
    an internal endpoint, which has no business in a message.
    """
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
        description="The script measures the uplink of this probe against an "
                    "own iperf3 endpoint.",
    )
    argparser.add_argument("--server",
                           help="Host name or address of the iperf3 endpoint. "
                                "Without it the one from the deployed "
                                "profile.")
    argparser.add_argument("--port", type=int,
                           help="Port of the iperf3 endpoint. Without it the "
                                "one from the deployed profile, or 5201.")
    argparser.add_argument("--download-mbit", type=int,
                           help="Download target in megabit per second.")
    argparser.add_argument("--upload-mbit", type=int,
                           help="Upload target in megabit per second.")
    argparser.add_argument("--udp", action="store_true",
                           help="Measure with UDP: reports packet loss and "
                                "jitter instead of retransmits and latency.")
    argparser.add_argument("--username",
                           help="User name for the endpoint's authentication. "
                                "Without it the one from the deployed "
                                "profile, and without that the sensor measures "
                                "unauthenticated.")
    argparser.add_argument("--password",
                           help="Password, for probes where no credentials "
                                "file can be placed. Left out, the sensor "
                                "reads the one deployed on this probe.")
    argparser.add_argument("--public-key",
                           help="Path to the endpoint's public key, if it is "
                                "not in the deployed profile.")
    argparser.add_argument("--profile", default="default",
                           help="Name of the deployed credential profile. "
                                "Use one per endpoint when they have "
                                "different passwords.")
    argparser.add_argument("--seconds", type=int, default=HOLD_SECONDS,
                           help="How long to transfer per direction. The "
                                "default of %d was measured; longer runs read "
                                "lower on a line that shapes after a burst."
                                % HOLD_SECONDS)
    argparser.add_argument("--measure-every-minutes", type=int, default=60,
                           help="Minimum minutes between two real measurements.")
    argparser.add_argument("--timeout-seconds", type=int,
                           help="The time budget for the whole test in seconds.")
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
            # Intercept before parsing: argparse would have written its
            # help to stdout long before it could be wrapped in JSON.
            if any(token in HELP_TOKENS for token in tokens):
                fail(HELP_MESSAGE)
            args = argparser.parse_args(tokens)
    except ConfigError as problem:
        fail(redact(str(problem), tokens))
    except ValueError:
        # shlex.split fails on an unpaired quotation mark.
        fail("Could not read the parameters: an unmatched quote. Check the "
             "configured parameters.")
    except SystemExit as termination:
        # Help (code 0) stays untouched so the terminal invocation keeps
        # working.
        if termination.code == 0:
            raise
        fail("Could not read the parameters. Check the configured parameters.")
    return vars(args)


def validate(args: dict[str, Any]) -> None:
    """Check the parameters before a single byte flows.

    A pure function without network access: that way --self-check can run
    the same validation on the probe before anyone even enters the
    parameters in PRTG. Every message ends with the line that has to stand
    there instead - it is the only place an administrator can learn what
    was wrong.
    """
    if not args["server"]:
        raise ConfigError(NO_SERVER_MESSAGE)

    targets = (("--download-mbit", args["download_mbit"]),
               ("--upload-mbit", args["upload_mbit"]))
    if args["udp"] and all(value is None for _, value in targets):
        raise ConfigError(UDP_WITHOUT_TARGET_MESSAGE)
    for name, value in targets:
        if value is None:
            continue
        if value < MIN_TARGET_MBIT or value > MAX_TARGET_MBIT:
            raise ConfigError(IMPLAUSIBLE_TARGET_MESSAGE % (value, name))

    if args["password"] and not args["username"]:
        # Without a user name the sensor switches authentication off, and
        # the password would vanish without effect. A silent fallback to an
        # unauthenticated measurement is exactly what nobody notices.
        raise ConfigError(PASSWORD_WITHOUT_USER_MESSAGE)

    if (args["seconds"] < MIN_HOLD_SECONDS
            or args["seconds"] > MAX_HOLD_SECONDS):
        raise ConfigError(IMPLAUSIBLE_SECONDS_MESSAGE
                          % (args["seconds"], MIN_HOLD_SECONDS,
                             MAX_HOLD_SECONDS))
    if (args["timeout_seconds"] is not None
            and args["timeout_seconds"] < needed_seconds(args)):
        # Otherwise the run aborts in its own alarm signal, and the
        # message would speak of a timeout instead of two parameters that
        # do not fit together.
        raise ConfigError(TIMEOUT_TOO_SHORT_MESSAGE
                          % (args["timeout_seconds"], args["seconds"],
                             needed_seconds(args)))

    if args["measure_every_minutes"] < 0:
        raise ConfigError(NEGATIVE_INTERVAL_MESSAGE)


def directions_of(args: dict[str, Any]) -> dict[str, Any]:
    """The directions to measure - with a target rate in bit/s or without.

    A given rate selects its direction at the same time; a separate
    direction parameter is therefore not needed.

    None means: without a cap, as fast as the path carries. Without any
    rate, both directions are measured that way and the sensor answers
    "how fast is the path" instead of "does it hold X". Both are
    legitimate - capacity is the trend curve, assurance is the alarm.
    """
    if args["download_mbit"] is None and args["upload_mbit"] is None:
        return {"download": None, "upload": None}
    wanted: dict[str, Any] = {}
    if args["download_mbit"]:
        wanted["download"] = args["download_mbit"] * 1000 * 1000
    if args["upload_mbit"]:
        wanted["upload"] = args["upload_mbit"] * 1000 * 1000
    return wanted


def needed_seconds(args: dict[str, Any]) -> int:
    """How long a run needs at minimum.

    Two directions at the hold duration, plus connection setup and
    authentication. The time budget derives from this when none is given -
    otherwise a high --seconds would run into its own abort.
    """
    return 2 * args["seconds"] + RUN_MARGIN_SECONDS


def time_budget(args: dict[str, Any]) -> int:
    if args["timeout_seconds"] is not None:
        return max(20, args["timeout_seconds"])
    return max(DEFAULT_TIMEOUT_SECONDS, needed_seconds(args) + 10)


def profile_path(args: dict[str, Any]) -> str:
    return "%s/%s.env" % (PROFILE_DIR, args["profile"])


def merge_profile(args: dict[str, Any]) -> dict[str, Any]:
    """Let the profile say which endpoint this is, if the parameters do not.

    Direct parameters win, the same rule that already applies to the password:
    whoever enters a value means it, and a profile sitting nearby must not
    override it. What this adds is the other direction - a profile that names
    its endpoint turns "--profile berlin" into a complete configuration, and a
    second endpoint becomes a second sensor with one parameter instead of four.

    --port is filled in here rather than by argparse: with a default already in
    place there would be no way to tell "not given" from "deliberately 5201",
    and the profile could never contribute a port at all.
    """
    profile = read_profile(profile_path(args)) or {}
    if not args.get("server"):
        args["server"] = profile.get(PROFILE_HOST) or None
    if not args.get("username"):
        args["username"] = profile.get(PROFILE_USERNAME) or None
    if not args.get("port"):
        try:
            args["port"] = int(profile.get(PROFILE_PORT, "") or DEFAULT_PORT)
        except ValueError:
            args["port"] = DEFAULT_PORT
    return args


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


def materialise_key(encoded: str, args: dict[str, Any]):
    """Store the public key from the profile as a file.

    iperf3 only takes it as a path. It goes next to the cache, in the
    service-private /tmp and readable only by the own user; it is not a
    secret, but not an invitation either.
    """
    try:
        material = base64.b64decode(encoded, validate=True)
    except Exception:
        raise Failed("credentials-unreadable", KEY_UNREADABLE_MESSAGE) from None
    if not material.startswith(b"-----BEGIN"):
        raise Failed("credentials-unreadable", KEY_UNREADABLE_MESSAGE)
    path = "%s-key.pem" % os.path.splitext(cache_path(args))[0]
    temporary = "%s.%d.tmp" % (path, os.getpid())
    try:
        descriptor = os.open(temporary,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600)
        try:
            os.write(descriptor, material)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise Failed("credentials-unreadable", KEY_UNREADABLE_MESSAGE) from None
    return path


def credentials_for(args: dict[str, Any]):
    """Assemble credentials, if any are required.

    Three cases, and --username decides between them:

    - no --username: unauthenticated. Defensible for an endpoint on your
      own network behind a firewall, and a decision somebody makes - not
      the side effect of a missing file.
    - --username alone: the password deployed on this probe. The regular
      case for your own probes; a change is a deployment.
    - --username and --password: the given one. For probes managed by
      someone else, and for several endpoints with different passwords.
    """
    if not args["username"]:
        return None
    profile = read_profile(profile_path(args)) or {}
    # The given password takes precedence: whoever enters it means it,
    # and a profile that happens to sit nearby must not override it.
    secret = args["password"] or profile.get(PROFILE_PASSWORD)
    if not secret:
        raise Failed("credentials-unreadable",
                     CREDENTIALS_MISSING_MESSAGE % profile_path(args))

    key = args["public_key"]
    if key is None:
        if profile.get(PROFILE_PUBLIC_KEY):
            key = materialise_key(profile[PROFILE_PUBLIC_KEY], args)
        else:
            key = PUBLIC_KEY_PATH
    if not os.path.exists(key):
        raise Failed("credentials-unreadable", KEY_MISSING_MESSAGE % key)
    return {"username": args["username"], "password": secret,
            "public_key": key}


def build_command(args: dict[str, Any], direction: str, rate_bit_s,
                  credentials) -> list[str]:
    """Assemble the iperf3 invocation for one direction.

    --bitrate is the pacemaker. With TCP it caps the sender and the
    achieved rate falls to what the line carries; with UDP the rate is
    sent regardless, and what is missing shows up as loss. That is why UDP
    is the stricter check and TCP the more useful trend curve.

    Without a target rate the switch is dropped and iperf3 measures with
    TCP what the path yields. For UDP that route would not exist - there
    the default is one megabit per second instead of "as fast as
    possible" - which is why validate() rejects that combination up
    front.
    """
    command = [IPERF, "--client", args["server"],
               "--port", str(args["port"]),
               "--json",
               "--time", str(args["seconds"])]
    if rate_bit_s is not None:
        command += ["--bitrate", "%d" % rate_bit_s]
    if direction == "download":
        # In this direction the far end sends.
        command.append("--reverse")
    if args["udp"]:
        command.append("--udp")
    if credentials:
        command += ["--username", credentials["username"],
                    "--rsa-public-key-path", credentials["public_key"]]
    return command


def classify(error: str) -> str:
    """Map iperf3's error message to a channel value.

    The text is evaluated because iperf3 emits no error numbers. What is
    not recognised stays "test-failed" - better vague than wrongly
    attributed.
    """
    lowered = error.lower()
    if "authorization" in lowered or "authentication" in lowered:
        return "auth-failed"
    if "busy" in lowered:
        return "busy"
    if ("connect" in lowered or "unreachable" in lowered
            or "no route" in lowered or "refused" in lowered):
        return "server-unreachable"
    return "test-failed"


def run_direction(args: dict[str, Any], direction: str, rate_bit_s: int,
                  credentials, seconds_left: int) -> dict[str, Any]:
    """Measure one direction and evaluate iperf3's output."""
    command = build_command(args, direction, rate_bit_s, credentials)
    environment = dict(os.environ)
    # The password travels through the environment, not the command line:
    # otherwise it would sit in every user's process list on the probe.
    if credentials:
        environment["IPERF3_PASSWORD"] = credentials["password"]
    else:
        environment.pop("IPERF3_PASSWORD", None)

    try:
        finished = subprocess.run(
            command, capture_output=True, env=environment,
            timeout=max(5, min(seconds_left,
                               args["seconds"] + RUN_MARGIN_SECONDS)),
        )
    except FileNotFoundError:
        raise Failed("tool-missing", TOOL_MISSING_MESSAGE) from None
    except subprocess.TimeoutExpired:
        raise Failed("timeout",
                     "The %s measurement did not finish in time (timeout). "
                     "The endpoint accepted the connection but did not "
                     "complete the test." % direction) from None

    try:
        document = json.loads(finished.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise Failed("test-failed",
                     "iperf3 did not return a readable result "
                     "(test-failed).") from None

    if document.get("error"):
        message = str(document["error"])
        raise Failed(classify(message),
                     "The endpoint refused the %s measurement: %s"
                     % (direction, message))
    return summarise(document, args["udp"], rate_bit_s)


def summarise(document: dict[str, Any], udp: bool,
              rate_bit_s) -> dict[str, Any]:
    """Pull the values that belong in channels from the iperf3 report.

    The receiver's view is taken throughout: what arrived is the line's
    performance. The sender's view also contains what the kernel merely
    accepted.

    Without a target rate, "met" stays None: there is nothing to hold, and
    an invented finding would be worse than none.
    """
    end = document.get("end") or {}
    outcome: dict[str, Any] = {}

    if udp:
        summary = end.get("sum") or {}
        # With UDP, "sum" carries the loss figures but names the sent
        # rate. Measured on a probe: 899,999,293 bit/s in "sum",
        # 899,257,288 in "sum_received" - the difference is exactly what
        # was lost on the way. The channel gets what arrived, or the
        # sensor would keep reporting the full target rate at ten percent
        # loss.
        received = end.get("sum_received") or {}
        outcome["bit_s"] = int(received.get("bits_per_second")
                               or summary.get("bits_per_second") or 0)
        outcome["jitter_ms"] = float(summary.get("jitter_ms") or 0.0)
        outcome["loss_percent"] = float(summary.get("lost_percent") or 0.0)
        outcome["packets"] = int(summary.get("packets") or 0)
        # With UDP the loss decides, not the rate: the target rate is
        # sent regardless of whether the line carries it.
        outcome["met"] = (None if rate_bit_s is None
                          else outcome["loss_percent"] <= MAX_LOSS_PERCENT)
    else:
        received = end.get("sum_received") or {}
        sent = end.get("sum_sent") or {}
        outcome["bit_s"] = int(received.get("bits_per_second") or 0)
        outcome["retransmits"] = int(sent.get("retransmits") or 0)
        outcome["met"] = (None if rate_bit_s is None else
                          outcome["bit_s"] >= (1.0 - SLIP_TOLERANCE) * rate_bit_s)
        # Only the sending side knows the round-trip time. In the
        # download direction the far end sends, so the value stays empty
        # there.
        streams = end.get("streams") or []
        sender = (streams[0].get("sender") or {}) if streams else {}
        mean_rtt = sender.get("mean_rtt")
        if isinstance(mean_rtt, (int, float)) and mean_rtt > 0:
            outcome["rtt_ms"] = float(mean_rtt) / 1000.0
    return outcome


def measure(args: dict[str, Any]) -> dict[str, Any]:
    """Measure both directions under a hard time limit."""
    if not os.path.exists(IPERF):
        raise Failed("tool-missing", TOOL_MISSING_MESSAGE)

    credentials = credentials_for(args)
    budget = time_budget(args)
    started = time.monotonic()
    previous = signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(budget)
    try:
        measurement: dict[str, Any] = {
            "measured_at": time.time(),
            "code": "ok",
            "protocol": "udp" if args["udp"] else "tcp",
            "endpoint": "%s:%d" % (args["server"], args["port"]),
        }
        met = True
        graded = False
        measurement["hold_seconds"] = args["seconds"]
        for direction, rate_bit_s in sorted(directions_of(args).items()):
            left = int(budget - (time.monotonic() - started))
            if left <= 0:
                raise Failed("timeout",
                             "The time budget ran out before the %s "
                             "measurement." % direction)
            outcome = run_direction(args, direction, rate_bit_s, credentials,
                                    left)
            key = "download" if direction == "download" else "upload"
            measurement["%s_kbit" % key] = int(outcome["bit_s"] / 1000)
            if rate_bit_s is not None:
                graded = True
                measurement["%s_target_kbit" % key] = int(rate_bit_s / 1000)
                measurement["%s_met" % key] = 1 if outcome["met"] else 0
                met = met and outcome["met"]
            for field in ("jitter_ms", "loss_percent", "retransmits"):
                if field in outcome:
                    measurement["%s_%s" % (key, field)] = outcome[field]
            if "rtt_ms" in outcome:
                measurement["rtt_ms"] = outcome["rtt_ms"]
        # Without a target rate there is nothing to pass. The channel is
        # then omitted instead of reporting a "yes" that carries no
        # statement.
        if graded:
            measurement["target_met"] = 1 if met else 0
        measurement["duration_ms"] = int((time.monotonic() - started) * 1000)
        return measurement
    except Timeout:
        raise Failed(
            "timeout",
            "The measurement did not finish within %d seconds (timeout). "
            "Raise --timeout-seconds; the PRTG sensor timeout must stay at "
            "least 20 seconds above it." % budget,
        ) from None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def cache_path(args: dict[str, Any]) -> str:
    """Determine the storage location for this configuration.

    If several of these sensors run on one probe - say one with TCP for
    the trend and one with UDP for the alarm - they must not slip each
    other their results.

    The endpoint belongs in the distinction: since endpoints can be set up
    and deployed centrally, a probe easily measures against two of them,
    and with the same protocol and target rate both results would
    otherwise be the same stored one. It is hashed because a host name
    contains characters that have no business in a file name.
    """
    target = hashlib.sha256(
        ("%s:%d" % (args["server"] or "", args["port"])).encode("utf-8")
    ).hexdigest()[:12]
    marker = "%s-%s-%d-%d" % (target,
                              "udp" if args["udp"] else "tcp",
                              args["download_mbit"] or 0,
                              args["upload_mbit"] or 0)
    base, extension = os.path.splitext(CACHE_PATH)
    return "%s-%s%s" % (base, marker, extension)


def read_cache(path: str, maximum_age_seconds):
    """Read the last result, as long as it is still valid.

    A maximum_age_seconds of None returns the result regardless of age;
    the case where another run is currently measuring needs that.
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
    if not isinstance(stored, dict):
        return None
    measured_at = stored.get("measured_at")
    if not isinstance(measured_at, (int, float)) or isinstance(measured_at, bool):
        return None

    age = time.time() - measured_at
    # A clock set back must not preserve a result indefinitely.
    if age < 0:
        return None
    if maximum_age_seconds is not None and age > maximum_age_seconds:
        return None
    stored["age_seconds"] = int(age)
    return stored


def write_cache(path: str, measurement) -> None:
    """Store the result atomically.

    A failure has no consequences: without a cache the next run measures
    again.
    """
    temporary = "%s.%d.tmp" % (path, os.getpid())
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(descriptor, json.dumps(measurement).encode("utf-8"))
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def acquire_lock():
    """Prevent two throughput measurements from running at once.

    The lock is shared across all sensors that measure throughput: two
    parallel measurements of the same probe push each other below the
    target rate and produce a false alarm over a healthy line.
    """
    try:
        descriptor = os.open(
            THROUGHPUT_LOCK_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError:
        return LOCK_UNAVAILABLE
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def release_lock(descriptor) -> None:
    if descriptor is None or descriptor == LOCK_UNAVAILABLE:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


# prtg.standardlookups.yesno.stateyesok only knows 1 = Yes (Ok) and
# 2 = No (Error) - the counting comes from SNMP, where 1 means true and 2
# false. The lookup does not know a 0: PRTG then shows "undefined lookup
# value" and turns it into a mere warning where an error should stand.
LOOKUP_YES = 1
LOOKUP_NO = 2
ALARM_LOOKUP = "prtg.standardlookups.yesno.stateyesok"


def lookup_value(condition) -> int:
    return LOOKUP_YES if condition else LOOKUP_NO


def channel(identifier: int, name: str, value, **extra) -> dict[str, Any]:
    result = {"id": identifier, "name": name, "type": "integer", "value": value}
    result.update(extra)
    return result


def number(identifier: int, name: str, value, **extra) -> dict[str, Any]:
    """A channel with decimal places - for loss, jitter and latency.

    Without it, half a percent of packet loss would vanish in rounding,
    and that is exactly where a line starts becoming unusable.
    """
    return channel(identifier, name, round(float(value), 2), type="float",
                   **extra)


def rate_text(kbit) -> str:
    return "%.1f Mbit/s" % (kbit / 1000.0)


def describe(measurement: dict[str, Any], age_seconds: int,
             args: dict[str, Any]) -> str:
    code = measurement.get("code", "ok")
    if code != "ok":
        return "%s (%s)" % (measurement.get("message", "Measurement failed"),
                            code)

    # The message is also built from the cache, which holds no parameters
    # any more - the hold duration therefore travels into the result.
    hold = measurement.get("hold_seconds", HOLD_SECONDS)
    held, missed = [], []
    for key, label in (("download", "down"), ("upload", "up")):
        target = measurement.get("%s_target_kbit" % key)
        if target is None:
            continue
        if measurement.get("%s_met" % key):
            held.append("%s %s" % (rate_text(target), label))
        else:
            loss = measurement.get("%s_loss_percent" % key)
            if loss is not None:
                missed.append("%s %s lost %.2f %% of its packets"
                              % (rate_text(target), label, loss))
            else:
                missed.append("only %s of %s %s"
                              % (rate_text(measurement.get("%s_kbit" % key, 0)),
                                 rate_text(target), label))

    if missed:
        summary = "; ".join(missed + ["%s held" % item for item in held])
    elif held:
        summary = "Held %s for %d s each" % (" and ".join(held), hold)
    else:
        # Without a target rate there is nothing to hold. The measured
        # rate itself is then the statement.
        measured = ["%s %s" % (rate_text(measurement["%s_kbit" % key]), label)
                    for key, label in (("download", "down"), ("upload", "up"))
                    if measurement.get("%s_kbit" % key) is not None]
        summary = ("Measured %s over %d s each"
                   % (" and ".join(measured), hold)
                   if measured else "Nothing measured")

    summary += " via %s over %s" % (measurement.get("endpoint", "the endpoint"),
                                    measurement.get("protocol", "tcp").upper())
    if measurement.get("rtt_ms") is not None:
        summary += ", %.1f ms round trip" % measurement["rtt_ms"]
    if age_seconds >= 60:
        summary += " (measured %d min ago)" % (age_seconds // 60)
    if not args.get("measure_every_minutes"):
        # An allowed but expensive state. It appears as a test recipe in
        # the README and tends to stay behind afterwards.
        summary += " (cache disabled, measuring on every scan)"
    return summary


def present(measurement: dict[str, Any], age_seconds: int,
            args: dict[str, Any]) -> dict[str, Any]:
    code = measurement.get("code", "ok")
    succeeded = code == "ok"

    channels = [
        channel(10, "Test Result", lookup_value(succeeded), type="lookup",
                lookup_name=ALARM_LOOKUP),
        channel(16, "Result Age", int(age_seconds), kind="custom",
                display_unit="s"),
        channel(18, "Failure Code", FAILURE_CODES.get(code, UNKNOWN_FAILURE)),
    ]

    for identifier, name, key in ((11, "Download", "download_kbit"),
                                  (12, "Upload", "upload_kbit")):
        value = measurement.get(key)
        if value is not None:
            channels.append(channel(identifier, name, int(value),
                                    kind="custom", display_unit="kbit/s"))

    if measurement.get("rtt_ms") is not None:
        channels.append(number(13, "Ping", measurement["rtt_ms"],
                               kind="time_milliseconds"))
    for identifier, name, key in (
        (14, "Jitter Download", "download_jitter_ms"),
        (15, "Jitter Upload", "upload_jitter_ms"),
    ):
        if measurement.get(key) is not None:
            channels.append(number(identifier, name, measurement[key],
                                   kind="time_milliseconds"))
    for identifier, name, key in (
        (21, "Packet Loss Download", "download_loss_percent"),
        (22, "Packet Loss Upload", "upload_loss_percent"),
    ):
        if measurement.get(key) is not None:
            channels.append(number(identifier, name, measurement[key],
                                   kind="percent"))
    for identifier, name, key in (
        (23, "Retransmits Download", "download_retransmits"),
        (24, "Retransmits Upload", "upload_retransmits"),
    ):
        if measurement.get(key) is not None:
            channels.append(channel(identifier, name, int(measurement[key])))

    if measurement.get("duration_ms") is not None:
        channels.append(channel(17, "Test Duration",
                                int(measurement["duration_ms"]),
                                kind="time_milliseconds"))
    if measurement.get("target_met") is not None:
        channels.append(channel(20, "Target Met",
                                lookup_value(measurement["target_met"]),
                                type="lookup", lookup_name=ALARM_LOOKUP))

    # Ascending, so the channel list in PRTG has the same order no matter
    # which optional values are present.
    channels.sort(key=lambda entry: entry["id"])

    return {
        "version": 2,
        "status": "ok",
        "message": describe(measurement, age_seconds, args)[:2000],
        "channels": channels,
    }


def failure_result(code: str, message: str,
                   args: dict[str, Any]) -> dict[str, Any]:
    """Report a failure as a valid measurement.

    The alarm hangs on the "Test Result" channel, not on the sensor
    status. That keeps the history of the measurement channels readable
    across an outage. Only when the sensor itself cannot work does it
    become a sensor error.
    """
    if code in SENSOR_FAILURES:
        fail(message)
    return present({"code": code, "message": message}, 0, args)


def self_check(args: dict[str, Any]) -> dict[str, Any]:
    """Check the ability to run - and the parameters, if any came along.

    Deliberately without network traffic: what is checked is that iperf3
    is present and the credentials sit where they belong. A real
    measurement would delay every deployment and, on a disturbed line,
    trigger a rollback although the sensor is fine.
    """
    if not os.path.exists(IPERF):
        fail(TOOL_MISSING_MESSAGE)

    configured = (args["server"] is not None
                  or args["download_mbit"] is not None
                  or args["upload_mbit"] is not None)
    if configured:
        try:
            validate(args)
        except ConfigError as problem:
            fail(str(problem))
    if args["username"]:
        try:
            credentials_for(args)
        except Failed as problem:
            fail(problem.message)

    try:
        version = subprocess.run([IPERF, "--version"], capture_output=True,
                                 timeout=10).stdout.decode("utf-8", "replace")
        version = version.splitlines()[0] if version else "of unknown version"
    except Exception:
        version = "of unknown version"

    message = "%s is ready." % version
    if configured:
        message += " The configured parameters are valid."
    # Where the password comes from belongs in the answer: it is the
    # difference between "deployed" and "entered in the sensor", and
    # whoever runs the self-test wants to know exactly that.
    if args["username"] and args["password"]:
        message += " Authenticating as %s with the password from the sensor " \
                   "parameters." % args["username"]
    elif args["username"]:
        message += " Authenticating as %s with the password deployed on this " \
                   "probe." % args["username"]
    else:
        message += " Measuring unauthenticated; the endpoint must accept that."
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
    # Before anything reads the endpoint, and before the self-test: a variant
    # that names its endpoint has to be as good as parameters that do.
    args = merge_profile(args)

    if args["self_check"]:
        return self_check(args)

    try:
        validate(args)
    except ConfigError as problem:
        fail(str(problem))

    path = cache_path(args)
    minimum_age = args["measure_every_minutes"] * 60
    if minimum_age:
        cached = read_cache(path, minimum_age)
        if cached is not None:
            return present(cached, cached["age_seconds"], args)

    lock = acquire_lock()
    if lock is None:
        # Another run is measuring right now. An older result is more
        # useful than none - the "Result Age" channel shows how old it
        # is.
        stale = read_cache(path, None)
        if stale is not None:
            return present(stale, stale["age_seconds"], args)
        return failure_result("busy", "Another measurement is already running "
                                      "on this probe.", args)

    try:
        # Between the first check and the lock another run may have
        # finished. The measurement is then already done.
        if minimum_age:
            cached = read_cache(path, minimum_age)
            if cached is not None:
                return present(cached, cached["age_seconds"], args)
        try:
            measurement = measure(args)
        except Failed as failure:
            # A busy endpoint says nothing about the line. The last
            # result is then more honest than a false alarm.
            if failure.code == "busy":
                stale = read_cache(path, None)
                if stale is not None:
                    return present(stale, stale["age_seconds"], args)
            return failure_result(failure.code, failure.message, args)
        write_cache(path, measurement)
        return present(measurement, 0, args)
    finally:
        release_lock(lock)


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
