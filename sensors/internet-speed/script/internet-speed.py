#!/usr/bin/env python3

"""Script v2 sensor: measure the internet throughput of a Multi-Platform
Probe.

The sensor measures the peak like an ordinary speed test and saturates the
line for about twenty seconds doing so. It answers whether the provider
delivers the contracted line - the number you hold up to a provider.

The other question - does the uplink hold what the site needs for its work -
is answered by the iperf-throughput sensor. It used to live here as mode
"minimum" and has moved out: against speedtest.net the server choice decides
which path is measured, and it changes between runs. Against a self-operated
endpoint it is fixed, and iperf3 does the pacing instead of four hundred
lines of custom code.

--mode remains required even though only one value is left: a sensor someone
creates without parameters would otherwise flood a site's line every hour,
and nobody would notice until the site calls.

Deployment creates a dedicated virtual environment on the probe and points
the installed copy's shebang at it; the script itself installs nothing.

Structured after the bundled examples under
/opt/paessler/share/doc/examples/scripts/python.
"""

# std-lib
import argparse
import fcntl
import json
import os
import shlex
import signal
import socket
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, NoReturn

# The sensor runs as the service user; prtg.mpprobe.service brings its own
# /tmp via PrivateTmp. The id in the name keeps the file separate even if a
# future release drops that.
CACHE_PATH = "/tmp/prtg-sensor-internet-speed-%d.json" % os.getuid()

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
    "module-missing": 1,
    "config-unreachable": 2,
    "no-servers": 3,
    "download-failed": 4,
    "upload-failed": 5,
    "timeout": 6,
    "busy": 7,
    "bind-failed": 8,
}
UNKNOWN_FAILURE = 99

# These causes say nothing about the line, only about the sensor itself.
# They do not belong in the measurement channels.
SENSOR_FAILURES = ("module-missing",)

MODULE_MISSING_MESSAGE = (
    "The speedtest module is not available (module-missing). Deploy the "
    "sensor again so the probe creates its virtual environment."
)

# A lock file could not be created. Then the measurement runs without a
# lock: a sensor that would rather report nothing at all would be the worse
# answer.
LOCK_UNAVAILABLE = -1

# From here on a latency is no longer a measurement but a placeholder:
# speedtest-cli enters six-digit millisecond values for a server its latency
# probe could not reach, and in doubt still picks it as the best one. Seen
# exactly like that while testing.
#
# Such a value is not reported as ping - it would distort the channel
# history for weeks. It is not an abort either: the transfer often succeeds
# anyway, and a reading is worth more than an error about a failed side
# measurement.
MAX_RTT_MS = 2000.0
# Without an agent string the test servers answer 500. The string is
# deliberately honest and does not pose as a browser - verified: a plain
# name is enough.
USER_AGENT = "prtg-nats-internet-speed/2"
# Latency samples run during the transfer. They feed the jitter; the file
# fetched is a few bytes in size.
JITTER_SAMPLES = 5

# Time budget of the run. 120 seconds are plenty for a measurement of
# about twenty seconds and leave room for a sluggish server selection.
DEFAULT_TIMEOUT_SECONDS = 120

# Deployment puts speedtest-cli into its own virtual environment; it is not
# on the probe's PATH. A message that recommends a command the shell cannot
# find teaches nothing - hence the full path.
LIST_SERVERS_COMMAND = (
    "/var/lib/prtg-nats-sensors/venv/internet-speed/bin/speedtest-cli --list"
)

DOCUMENTATION_HINT = (
    "All parameters are listed by putting \"--help\" in the sensor's parameter "
    "field; the full documentation is sensors/internet-speed/README.md in the "
    "prtg-nats repository."
)
NO_MODE_MESSAGE = (
    'No mode selected. --mode is required: "--mode maximum" measures the peak '
    "and saturates the line for about 20 seconds per run. It is required even "
    "though it is the only value, so that a sensor created without parameters "
    "cannot flood a site's line every hour unnoticed. " + DOCUMENTATION_HINT
)
# argparse writes its help as plain text to stdout and exits. In a
# terminal that is right; through PRTG the sensor would produce output that
# is not JSON, and PRTG would show a parse error instead of the parameters.
# But whoever types "--help" into the parameter field wants exactly this
# list - it is the only place to find it without access to the probe.
HELP_MESSAGE = (
    "Parameters of this sensor:\n"
    "  --mode maximum             required. Measures the peak and saturates "
    "the line for about 20 s. Required even as the only value, so a sensor "
    "created without parameters cannot flood a line unnoticed\n"
    "  --measure-every-minutes N  default 60, 0 measures on every scan\n"
    "  --timeout-seconds N        default 120\n"
    "  --server ID                pin the test to one speedtest.net server; "
    "take the ID from this sensor's own message\n"
    "  --source IP                source address, if the probe has several "
    "ways out\n"
    "  --no-secure                use HTTP instead of HTTPS towards "
    "speedtest.net\n"
    "  --self-check               check that the sensor can run, without "
    "measuring\n"
    "Example: --mode maximum\n"
    "Checking that a line holds a rate is the iperf-throughput sensor's job "
    "now; see its README.\n"
    "Full documentation: sensors/internet-speed/README.md in the prtg-nats "
    "repository."
)
HELP_TOKENS = ("--help", "-h")
# Mode minimum has moved out into the iperf-throughput sensor. The
# parameters stay in the parser anyway: an existing PRTG sensor should get
# this message, not argparse's "invalid choice" - which does not say where
# it went.
MODE_MINIMUM_GONE_MESSAGE = (
    'Mode "minimum" has moved to its own sensor. It checked that the line '
    "holds a rate, but measured against speedtest.net, whose server choice "
    "decides which path is measured and changes between runs. The "
    "iperf-throughput sensor asks the same question against an endpoint you "
    "run yourself, and lets iperf3 do the pacing. Create one with "
    '"--server ENDPOINT --username NAME --download-mbit 30 --upload-mbit 10" '
    'and remove this sensor, or switch this one to "--mode maximum" to keep '
    "measuring capacity against speedtest.net."
)
TARGET_MOVED_MESSAGE = (
    "%s has moved to the iperf-throughput sensor together with mode "
    '"minimum". Drop it here and measure the peak with "--mode maximum", or '
    "set up an iperf-throughput sensor for the target rate."
)
SERVER_NOT_A_NUMBER_MESSAGE = (
    'The server ID must be a number. "--server 12345" pins the test to one '
    "speedtest.net server; the IDs are listed by \"%s\" on the probe. Remove "
    "--server to let the sensor choose." % LIST_SERVERS_COMMAND
)
# Parameters whose value does not belong in a message to PRTG.
SECRET_PARAMETERS = ("--source",)


NEGATIVE_INTERVAL_MESSAGE = (
    "--measure-every-minutes must not be negative. Use "
    '"--measure-every-minutes 60" for hourly measurements, or 0 to measure '
    "on every scan."
)


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

    Deliberately inherits from BaseException, not Exception: the
    measurement stages catch every speedtest-cli failure with "except
    Exception" to translate it into a channel value. An alarm signal would
    drown in that and come out as "download-failed" instead of ending the
    run.
    """


def raise_timeout(signum, frame) -> NoReturn:
    raise Timeout()


class ReportingParser(argparse.ArgumentParser):
    """Let argparse carry its own error message.

    The parameters are typed into a text field in PRTG and checked by
    nothing there; the first sensor run is the only place a typo can show
    up. argparse knows exactly what is wrong ("invalid choice: 'min'"),
    but writes it to stderr and terminates the process. Here the message
    is carried as an exception instead and ends up in the output.
    """

    def error(self, message) -> NoReturn:
        raise ConfigError(message)


def redact(message: str, tokens: list[str]) -> str:
    """Remove values of protected parameters from an error message.

    argparse quotes the faulty input verbatim, and that is intended - the
    typo is the very hint this is about. Only --source carries an internal
    address that has no business in a message to PRTG.

    Deliberately targeted, not blanket: a filter over all input values
    would dismantle the message itself, because a wrong input like "min"
    also occurs in the list of valid values. Should a parameter with a
    secret ever be added, it belongs in SECRET_PARAMETERS - wlan-auth with
    --psk and --private-key-passwd must not adopt this function
    unreviewed.
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
    # exit_on_error stays on: only then does argparse route value
    # conversion failures through error() too - and thereby into the
    # message PRTG gets to see. It still does not abort, because error()
    # is replaced.
    argparser = ReportingParser(
        description="The script measures the internet throughput of this probe.",
    )

    # "minimum" stays allowed as a value, so an existing sensor gets the
    # explanatory message from validate() instead of argparse's "invalid
    # choice" - which does not say where the mode went.
    argparser.add_argument("--mode", choices=("maximum", "minimum"),
                           help="Measure the peak (maximum). The value "
                                "\"minimum\" has moved to the "
                                "iperf-throughput sensor.")
    argparser.add_argument("--min-download-mbit", type=int,
                           help="Moved to the iperf-throughput sensor.")
    argparser.add_argument("--min-upload-mbit", type=int,
                           help="Moved to the iperf-throughput sensor.")
    argparser.add_argument("--measure-every-minutes", type=int, default=60,
                           help="Minimum minutes between two real measurements.")
    argparser.add_argument("--timeout-seconds", type=int,
                           help="The time budget for the whole test in seconds.")
    argparser.add_argument("--server",
                           help="Pin the test to one speedtest.net server ID.")
    argparser.add_argument("--source",
                           help="Source address the measurement binds to.")
    argparser.add_argument("--no-secure", action="store_true",
                           help="Use HTTP instead of HTTPS towards speedtest.net.")
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
    there instead - it is the only place an administrator can learn what
    was wrong.
    """
    targets = (("--min-download-mbit", args["min_download_mbit"]),
               ("--min-upload-mbit", args["min_upload_mbit"]))

    if args["mode"] is None:
        raise ConfigError(NO_MODE_MESSAGE)

    if args["mode"] == "minimum":
        raise ConfigError(MODE_MINIMUM_GONE_MESSAGE)
    for name, value in targets:
        if value is not None:
            raise ConfigError(TARGET_MOVED_MESSAGE % name)

    if args["server"]:
        try:
            int(args["server"])
        except (TypeError, ValueError):
            # Up to here this was a measurement failure ("no-servers").
            # It is none: the line has nothing to do with it, the entry is
            # wrong.
            raise ConfigError(SERVER_NOT_A_NUMBER_MESSAGE) from None

    if args["measure_every_minutes"] < 0:
        raise ConfigError(NEGATIVE_INTERVAL_MESSAGE)


def time_budget(args: dict[str, Any]) -> int:
    """Determine the time budget, with a mode-dependent default."""
    if args["timeout_seconds"] is not None:
        return max(10, args["timeout_seconds"])
    return DEFAULT_TIMEOUT_SECONDS




def load_speedtest():
    """Load the measurement tool only when needed.

    At module level the import would blow up the checks under tests/, which
    load the script as a module without a virtual environment. Besides, a
    missing package should come out as a clean sensor error, not as a
    traceback PRTG cannot even read.
    """
    # Python puts the running script's directory at the front of
    # sys.path. On the probe that is /opt/paessler/share/scripts - should a
    # sensor named speedtest.py ever live there, the next line would import
    # it instead of the package from the virtual environment.
    own_directory = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [entry for entry in sys.path
                   if entry not in ("", ".", own_directory)]
    try:
        import speedtest
    except Exception:
        # A damaged package too may only lead to a message, not to an
        # abort without output.
        return None
    return speedtest


def cache_path(args: dict[str, Any]) -> str:
    """Determine the storage location.

    The mode remains part of the name. While a fleet is being migrated, a
    probe holds results from both states for a while; without the marker a
    sensor would be slipped the result of a sensor that no longer exists in
    that form.
    """
    base, extension = os.path.splitext(CACHE_PATH)
    return "%s-%s%s" % (base, args["mode"], extension)


def read_cache(path: str, maximum_age_seconds):
    """Read the last result, as long as it is still valid.

    A maximum_age_seconds of None returns the result regardless of age; the
    case where another run is currently measuring needs that.

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


def write_cache(path: str, measurement):
    """Store the result atomically.

    A failure has no consequences: without a cache the next run measures
    again. That is unfortunate, but no reason to discard a valid result.
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


def release_lock(descriptor):
    if descriptor is None or descriptor == LOCK_UNAVAILABLE:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def plausible_ping(value) -> bool:
    """Is this a measured latency or a placeholder?

    A six-digit value from speedtest-cli means "not reached" and does not
    belong in a channel PRTG charts for weeks.
    """
    return isinstance(value, (int, float)) and 0 < float(value) <= MAX_RTT_MS


















def measure_rtt(base: str, timeout: int):
    """Measure the idle latency ourselves.

    Necessary because speedtest-cli can no longer measure it: it fetches
    latency.txt with raw http.client and treats everything but 200 as a
    failure. The test servers meanwhile answer with a redirect to their own
    host name, which http.client does not follow - out come three
    placeholders and, from them, 1,800,000 ms. The throughput test notices
    nothing of this, because it runs over urllib and follows the redirect.

    The minimum is taken: it comes closest to the pure round-trip time,
    while the mean already contains waiting periods.
    """
    request = urllib.request.Request("%s/latency.txt" % base,
                                     headers={"User-Agent": USER_AGENT})
    samples = []
    for _ in range(JITTER_SAMPLES):
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.read(9) != b"test=test":
                    continue
        except Exception:
            continue
        samples.append((time.perf_counter() - started) * 1000.0)
    return min(samples) if samples else None


def jitter_of(samples: list[float]):
    """Latency variation as the mean difference of consecutive samples.

    Simplified per RFC 3550. Deliberately not derived from the transfer's
    own timing: on a healthy line that is regular by construction and
    would always yield a jitter near zero.
    """
    if len(samples) < 2:
        return None
    differences = [abs(samples[index] - samples[index - 1])
                   for index in range(1, len(samples))]
    return sum(differences) / len(differences)


def measure_jitter(server_url: str, samples: int, timeout: int):
    """Idle latency variation, for mode maximum."""
    latency_url = "%s/latency.txt" % os.path.dirname(server_url)
    # The address comes from speedtest.net's server list. Any scheme
    # other than HTTP would not come from there and is therefore not
    # opened.
    if not latency_url.startswith(("http://", "https://")):
        return None

    # Without an agent string the test servers answer 500 - the jitter
    # would then stay empty for good without anyone noticing.
    request = urllib.request.Request(latency_url,
                                     headers={"User-Agent": USER_AGENT})
    measurements = []
    for _ in range(max(0, samples)):
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read(64)
        except Exception:
            continue
        measurements.append((time.perf_counter() - started) * 1000.0)
    return jitter_of(measurements)












def bind_source(source: str) -> None:
    """Check the source address up front.

    Without this step a vanished address would come out as
    "config-unreachable" - a statement about speedtest.net although the own
    configuration is meant. That is the difference between "call the
    provider" and "look here".
    """
    for family in (socket.AF_INET, socket.AF_INET6):
        probe = socket.socket(family, socket.SOCK_STREAM)
        try:
            probe.bind((source, 0))
            return
        except OSError:
            continue
        finally:
            probe.close()
    raise Failed("bind-failed",
                 "Could not bind to the source address given in --source.")


def select_server(speedtest, args: dict[str, Any], socket_timeout: int):
    """Choose the test server.

    Kept in one place so every run measures against a server chosen the
    same way - otherwise the readings could not be laid side by side.
    """
    if args["source"]:
        bind_source(args["source"])

    secure = not args["no_secure"]
    tester = build_tester(speedtest, args, socket_timeout, secure)

    chosen = [int(args["server"])] if args["server"] else None
    # Caught separately because three very different things can go wrong
    # here and the way out differs each time. Lumped together, the message
    # would claim a cause it never checked - seen on a probe: the sensor
    # reported "no longer in the list" about a server the same probe had
    # listed a minute earlier.
    try:
        tester.get_servers(chosen)
    except Exception as problem:
        no_match = getattr(speedtest, "NoMatchedServers", None)
        if not (chosen and no_match is not None
                and isinstance(problem, no_match)):
            raise Failed("no-servers",
                         server_list_message(speedtest, problem,
                                             chosen)) from None
        # Over HTTP and over HTTPS speedtest.net serves two different
        # directories of ten servers each - measured: without a single
        # shared ID. Whoever reads an ID off "speedtest-cli --list" is
        # looking at the HTTP directory, because --secure is the exception
        # there; the sensor conversely asks over HTTPS by default. The
        # same valid ID then counts as unknown here.
        #
        # With a pinned ID the second directory is therefore consulted
        # before giving up. Without a fixed server it is not worth it:
        # any server from the first will do.
        tester = build_tester(speedtest, args, socket_timeout, not secure)
        try:
            tester.get_servers(chosen)
        except Exception as second:
            # Last resort: the address remembered from the last
            # successful run. The server stays measurable even while
            # speedtest.net does not currently offer it.
            entry = recalled_server(args, chosen)
            if entry is None:
                raise Failed("no-servers",
                             server_list_message(speedtest, second,
                                                 chosen)) from None
            tester.servers = {0.0: [entry]}
    try:
        tester.get_best_server()
    except Exception:
        if chosen:
            raise Failed("no-servers",
                         "The pinned server %d is listed but did not answer "
                         "the latency probe (no-servers). That is not a "
                         "verdict on the line: the server may be busy or out "
                         'of service. Pick another ID with "%s" on the probe, '
                         "or remove --server to let the sensor choose."
                         % (chosen[0], LIST_SERVERS_COMMAND)) from None
        raise Failed("no-servers",
                     "No test server answered the latency probe "
                     "(no-servers). Check whether the probe reaches "
                     "speedtest.net directly.") from None

    return tester


def recalled_server(args: dict[str, Any], chosen):
    """Recall the last remembered address of the pinned server.

    speedtest.net names only ten servers per fetch, and that set is
    exchanged completely within minutes - measured. An ID that was in the
    list yesterday can be in neither of the two today although the server
    is running. Without this memory --server would be useless: one pins a
    server to get comparable readings over weeks, and would get gaps
    instead.

    It reads from the same cache as the last measurement, so with the same
    ownership and permission check.
    """
    stored = read_cache(cache_path(args), None)
    if not stored or stored.get("server_id") != chosen[0]:
        return None
    url = stored.get("server_url") or ""
    if not url.startswith(("http://", "https://")):
        return None
    return {"id": str(chosen[0]), "url": url, "d": 0.0,
            "sponsor": stored.get("server") or "", "name": ""}


def build_tester(speedtest, args: dict[str, Any], socket_timeout: int,
                 secure: bool):
    """Build a speedtest-cli session.

    "from None" suppresses the original exception. It could carry the
    source address or a server address, which have no business in a
    message to PRTG.
    """
    try:
        return speedtest.Speedtest(
            secure=secure,
            source_address=args["source"] or None,
            timeout=socket_timeout,
        )
    except Exception:
        raise Failed("config-unreachable",
                     "Could not reach speedtest.net to fetch its "
                     "configuration (config-unreachable). The probe needs "
                     "direct HTTPS access; a forced proxy is not "
                     "supported.") from None


def server_list_message(speedtest, problem, chosen) -> str:
    """Say why the server list did not yield what was needed.

    The exception itself does not go out - it could carry a server
    address. Only its type is evaluated, and only if the package still
    knows it: a changed version may lead to a vaguer message, not to a
    crash.
    """
    no_match = getattr(speedtest, "NoMatchedServers", None)
    if chosen and no_match is not None and isinstance(problem, no_match):
        return ("The pinned server %d is not in the server list any more "
                "(no-servers). Remove --server, or pick a current ID with "
                '"%s" on the probe.' % (chosen[0], LIST_SERVERS_COMMAND))
    retrieval = getattr(speedtest, "ServersRetrievalError", None)
    if retrieval is not None and isinstance(problem, retrieval):
        return ("Could not retrieve the list of test servers from "
                "speedtest.net (no-servers). The probe needs direct HTTPS "
                "access; a forced proxy is not supported.")
    return "Could not determine a usable test server (no-servers)."


def measure(args: dict[str, Any]) -> dict[str, Any]:
    """Measure the throughput under a hard time limit.

    speedtest-cli works with threads and can get stuck in them; the main
    thread then waits in join(), and a check between the stages never gets
    a turn again. An alarm signal interrupts even that. The worker threads
    are daemons and do not hold the process up afterwards.
    """
    speedtest = load_speedtest()
    if speedtest is None:
        raise Failed("module-missing", MODULE_MISSING_MESSAGE)

    budget = time_budget(args)
    previous = signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(budget)
    try:
        return run_measurement(speedtest, args, budget)
    except Timeout:
        raise Failed(
            "timeout",
            "The measurement did not finish within %d seconds (timeout). "
            "Lower the target rate or raise --timeout-seconds; the PRTG "
            "sensor timeout must stay at least 20 seconds above it." % budget,
        ) from None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def socket_timeout_for(budget: int) -> int:
    """A timeout per single fetch, not an overall budget.

    Kept small because speedtest-cli makes up to fifteen latency fetches
    during server selection, and a dead server could otherwise use up the
    whole budget on its own.
    """
    return max(4, min(10, budget // 4))


def run_measurement(speedtest, args: dict[str, Any],
                    budget: int) -> dict[str, Any]:
    """Mode maximum: measure the peak the way speedtest-cli does."""
    started = time.monotonic()
    socket_timeout = socket_timeout_for(budget)

    def remaining():
        return budget - (time.monotonic() - started)

    tester = select_server(speedtest, args, socket_timeout)

    if remaining() <= 0:
        raise Failed("timeout", "The time budget ran out before the download.")
    try:
        tester.download()
    except Exception:
        raise Failed("download-failed",
                     "The download measurement did not finish.") from None

    if remaining() <= 0:
        raise Failed("timeout", "The time budget ran out before the upload.")
    try:
        # pre_allocate stays on. Without the pre-allocation,
        # speedtest-cli 2.1.3 gets stuck in its worker thread instead of
        # sending - reproducible on a probe.
        tester.upload(pre_allocate=True)
    except Exception:
        raise Failed("upload-failed",
                     "The upload measurement did not finish.") from None

    results = tester.results.dict()
    server = results.get("server") or {}

    jitter_ms = None
    ping_ms = results.get("ping")
    if remaining() > 0 and server.get("url"):
        window = max(2, int(min(socket_timeout, remaining())))
        jitter_ms = measure_jitter(server["url"], JITTER_SAMPLES, window)
        if not plausible_ping(ping_ms):
            # speedtest-cli fails on the test servers' redirect; our own
            # measurement follows it and delivers a value again.
            ping_ms = measure_rtt(os.path.dirname(server["url"]), window)

    measurement = base_measurement(server, started)
    measurement["download_kbit"] = int(results.get("download", 0) / 1000)
    measurement["upload_kbit"] = int(results.get("upload", 0) / 1000)
    if plausible_ping(ping_ms):
        measurement["ping_ms"] = int(round(ping_ms))
    if jitter_ms is not None:
        measurement["jitter_ms"] = int(round(jitter_ms))
    return measurement




def base_measurement(server: dict[str, Any], started: float) -> dict[str, Any]:
    """The fields every measurement carries."""
    measurement = {
        "measured_at": time.time(),
        "code": "ok",
        "duration_ms": int((time.monotonic() - started) * 1000),
        "server": describe_server(server),
    }
    try:
        measurement["server_id"] = int(server.get("id"))
    except (TypeError, ValueError):
        pass
    # The address travels into the result, so a pinned server stays
    # reachable even after it disappears from the server list.
    url = server.get("url") or ""
    if url.startswith(("http://", "https://")):
        measurement["server_url"] = url
    return measurement


def describe_server(server: dict[str, Any]) -> str:
    sponsor = str(server.get("sponsor") or "").strip()
    location = str(server.get("name") or "").strip()
    if sponsor and location:
        return "%s (%s)" % (sponsor, location)
    return sponsor or location or "unknown server"


# prtg.standardlookups.yesno.stateyesok only knows 1 = Yes (Ok) and
# 2 = No (Error) - the counting comes from SNMP, where 1 means true and 2
# false. The lookup does not know a 0: PRTG then shows "undefined lookup
# value" and turns it into a mere warning where an error should stand.
# Seen on a real probe - the sensor stayed yellow although the measurement
# had failed.
LOOKUP_YES = 1
LOOKUP_NO = 2

# The lookup for channels that carry an alarm: 1 = Yes (Ok), 2 = No (Error).
ALARM_LOOKUP = "prtg.standardlookups.yesno.stateyesok"
# And one where both values are Ok - for channels that explain rather
# than complain. It counts 0 and 1 instead of 1 and 2. The name is
# borrowed: "yesno.allstatesok" describes it, "exchangedag" is incidental.
# PRTG ships no more neutrally named one, checked across all 283 bundled
# lookups.
NEUTRAL_LOOKUP = "prtg.standardlookups.exchangedag.yesno.allstatesok"


def lookup_value(condition) -> int:
    """Translate a yes/no into the values PRTG understands."""
    return LOOKUP_YES if condition else LOOKUP_NO


def channel(identifier: int, name: str, value: int, **extra) -> dict[str, Any]:
    result = {"id": identifier, "name": name, "type": "integer", "value": value}
    result.update(extra)
    return result


def rate_text(kbit) -> str:
    return "%.1f Mbit/s" % (kbit / 1000.0)


def describe(measurement: dict[str, Any], age_seconds: int,
             args: dict[str, Any]) -> str:
    code = measurement.get("code", "ok")
    if code != "ok":
        return "%s (%s)" % (measurement.get("message", "Measurement failed"), code)

    summary = describe_maximum(measurement)

    summary += " via %s" % (measurement.get("server") or "unknown server")
    if measurement.get("server_id") is not None:
        # So the ID for --server can be read off without looking on the
        # probe.
        summary += ", server %d" % measurement["server_id"]
    if age_seconds >= 60:
        summary += " (measured %d min ago)" % (age_seconds // 60)
    if not args.get("measure_every_minutes"):
        # An allowed but expensive state. It appears as a test recipe in
        # the README and tends to stay behind afterwards.
        summary += " (cache disabled, measuring on every scan)"
    return summary


def describe_maximum(measurement: dict[str, Any]) -> str:
    parts = []
    if measurement.get("download_kbit") is not None:
        parts.append("%s down" % rate_text(measurement["download_kbit"]))
    if measurement.get("upload_kbit") is not None:
        parts.append("%s up" % rate_text(measurement["upload_kbit"]))
    if measurement.get("ping_ms") is not None:
        parts.append("%d ms" % measurement["ping_ms"])
    return ", ".join(parts) if parts else "no throughput measured"




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
    for identifier, name, key, extra in (
        (11, "Download", "download_kbit",
         {"kind": "custom", "display_unit": "kbit/s"}),
        (12, "Upload", "upload_kbit",
         {"kind": "custom", "display_unit": "kbit/s"}),
        (13, "Ping", "ping_ms", {"kind": "time_milliseconds"}),
        (14, "Jitter", "jitter_ms", {"kind": "time_milliseconds"}),
        # The server ID itself would be wrong as a channel: PRTG averages
        # historical values, and an averaged ID would look like a reading.
        # A server change, by contrast, averages meaningfully - as the
        # share of runs that switched - and explains jumps in the
        # throughput curve.
        #
        # Deliberately with the neutral lookup: the alarm lookup binds
        # "No" to "Error", and a server change is not a failure but
        # context - the sensor would otherwise go red because
        # speedtest.net offered a different server.
        (15, "Same Server", "same_server",
         {"type": "lookup", "lookup_name": NEUTRAL_LOOKUP}),
        (17, "Test Duration", "duration_ms", {"kind": "time_milliseconds"}),
    ):
        value = measurement.get(key)
        if value is None:
            continue
        if extra.get("lookup_name") == ALARM_LOOKUP:
            # Convert only at this boundary, and only for the alarm
            # lookup: the neutral one counts 0 and 1. Internally the
            # fields stay at 0 and 1, or every truth test on them would
            # be wrong - a 2 is truthy but means "no" here.
            value = lookup_value(value)
        channels.append(channel(identifier, name, int(value), **extra))

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

    Deliberately without network traffic: what is checked is that the
    virtual environment was created and the shebang points at it. A real
    measurement would delay every deployment and, on a disturbed line,
    trigger a rollback although the sensor is fine.

    If parameters come along, they are checked too. That is the only way
    to check a configuration before it is entered in PRTG - nobody checks
    it there any more.
    """
    speedtest = load_speedtest()
    if speedtest is None:
        fail(MODULE_MISSING_MESSAGE)

    configured = (args["mode"] is not None
                  or args["min_download_mbit"] is not None
                  or args["min_upload_mbit"] is not None
                  or args["server"] is not None)
    if configured:
        try:
            validate(args)
        except ConfigError as problem:
            fail(str(problem))

    message = ("speedtest-cli %s is ready."
               % getattr(speedtest, "__version__", "of unknown version"))
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
        # useful than none - the "Result Age" channel shows how old it is.
        stale = read_cache(path, None)
        if stale is not None:
            return present(stale, stale["age_seconds"], args)
        return failure_result("busy", "Another measurement is already running.",
                              args)

    try:
        # Between the first check and the lock another run may have
        # finished. The measurement is then already done.
        if minimum_age:
            cached = read_cache(path, minimum_age)
            if cached is not None:
                return present(cached, cached["age_seconds"], args)
        previous = read_cache(path, None)
        try:
            measurement = measure(args)
        except Failed as failure:
            return failure_result(failure.code, failure.message, args)
        measurement["same_server"] = same_server(previous, measurement)
        write_cache(path, measurement)
        return present(measurement, 0, args)
    finally:
        release_lock(lock)


def same_server(previous, measurement: dict[str, Any]) -> int:
    """Did this run hit the same test server as the previous one?

    A server change is the most common explanation for a jump in the
    throughput curve. On the first run there is nothing to compare; the
    server then counts as unchanged.
    """
    if previous is None or previous.get("server_id") is None:
        return 1
    return 1 if previous.get("server_id") == measurement.get("server_id") else 0


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
