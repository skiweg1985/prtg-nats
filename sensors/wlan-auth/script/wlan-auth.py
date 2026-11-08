#!/usr/bin/env python3

"""Script v2 sensor: test Wi-Fi sign-in on a Multi-Platform Probe.

The sensor checks whether the probe can sign in to a Wi-Fi network - via
WPA2-PSK, PEAP/MSCHAPv2 or EAP-TLS - and reports the duration of the
individual stages to PRTG. It runs as the probe's service user and hands
everything privileged to a dedicated service it reaches over the Unix socket
/run/prtg-sensor-wlan-auth.sock.

No sudo: prtg.mpprobe.service runs with NoNewPrivileges=yes, which makes the
kernel ignore the setuid bit of sudo. The socket is the path that needs no
intervention in the MPP service's hardening.

Credentials can be passed as parameters or - recommended - fetched via
--profile from a protected file on the probe. The PRTG configuration then
holds only the profile name.

Structured after the bundled examples under
/opt/paessler/share/doc/examples/scripts/python.
"""

# std-lib
import argparse
import difflib
import json
import re
import shlex
import socket
import sys
from typing import Any, NoReturn

HELPER_SOCKET = "/run/prtg-sensor-wlan-auth.sock"

# Patience for collecting a result. The helper answers out of memory, so
# this covers a service under load, not a measurement.
COLLECT_TIMEOUT = 30

# Stable numbers for the "Failure Code" channel. They allow targeted
# alerting on a specific cause without parsing the message text.
FAILURE_CODES = {
    "ok": 0,
    "assoc-timeout": 1,
    "auth-failed-psk": 2,
    "auth-failed-eap": 3,
    "server-cert-rejected": 4,
    "auth-rejected": 5,
    "assoc-rejected": 6,
    "ssid-not-found": 7,
    "dhcp-timeout": 8,
    "auth-timeout": 9,
    "assoc-lost": 10,
    "not-measured": 11,
    "result-stale": 12,
    "max-age-too-low": 13,
    "gateway-unreachable": 14,
}


# A parameter line typed on a phone, or pasted out of a document, tends
# to carry a typographic dash where two hyphens belong. The characters
# are nearly indistinguishable on screen, so the sensor stays red while
# the line looks right - naming the character is the whole difference.
TYPOGRAPHIC_DASHES = {
    "–": "an en dash", "—": "an em dash", "−": "a minus sign",
    "‑": "a non-breaking hyphen",
}


# What argparse puts in front of its complaint. The parameter name is the
# one part of it the sensor repeats; see parse_failure for why.
BAD_CHOICE = re.compile(r"argument (--[\w-]+)[^:]*: invalid choice")
BAD_VALUE = re.compile(r"argument (--[\w-]+)[^:]*: invalid (\w+) value")
NO_VALUE = re.compile(r"argument (--[\w-]+)[^:]*: expected one argument")
UNKNOWN_NAME = re.compile(r"unrecognized arguments: (--[\w-]+)")
# The words argparse uses for the types this sensor converts.
VALUE_KINDS = {"int": "a whole number"}


class RecordingParser(argparse.ArgumentParser):
    """An argument parser that keeps its complaint instead of printing it.

    argparse writes to stderr and exits, which leaves PRTG with no output
    at all - it then only reports that it cannot read the result. Holding
    the text lets the sensor turn it into a channel message.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.complaint = ""

    def error(self, message: str) -> NoReturn:
        self.complaint = message
        raise SystemExit(2)


def spoken_list(values: list[str]) -> str:
    """The values as a reader expects them: a, b or c."""
    if len(values) < 2:
        return "".join(values)
    return "%s or %s" % (", ".join(values[:-1]), values[-1])


def parameter_choices(parser: argparse.ArgumentParser, name: str) -> list[str]:
    """The values one parameter accepts, as the parser itself knows them."""
    # _actions is the only way to the parser's own definitions. Reading
    # them beats a second table of choices that has to be kept in step.
    for action in parser._actions:
        if name in action.option_strings and action.choices:
            return [str(choice) for choice in action.choices]
    return []


def parameter_names(parser: argparse.ArgumentParser) -> list[str]:
    """Every parameter name the parser accepts."""
    names = []
    for action in parser._actions:
        names.extend(action.option_strings)
    return names


def parse_failure(text: str, complaint: str = "",
                  parser: argparse.ArgumentParser | None = None) -> str:
    """Why the parameters could not be read, without quoting them.

    argparse names the offending value in its own complaint, and a
    parameter line carries passphrases, so nothing from the input is
    repeated: the parameter name comes out of the complaint, the values
    and the suggestion out of the parser.
    """
    general = "Could not parse input parameter. Check configured parameters."

    named = [name for dash, name in TYPOGRAPHIC_DASHES.items() if dash in text]
    if named:
        # The more precise cause, and one that also explains the unknown
        # name argparse would otherwise complain about.
        return (general + " The parameters contain %s where a parameter "
                "name needs two plain hyphens." % named[0])
    if not complaint or parser is None:
        return general

    found = BAD_CHOICE.search(complaint)
    if found:
        choices = parameter_choices(parser, found.group(1))
        if choices:
            return "%s accepts only %s." % (found.group(1),
                                            spoken_list(choices))
    found = BAD_VALUE.search(complaint)
    if found:
        kind = VALUE_KINDS.get(found.group(2), "another kind of value")
        return "%s needs %s." % (found.group(1), kind)
    found = NO_VALUE.search(complaint)
    if found:
        return "%s was given without a value." % found.group(1)
    found = UNKNOWN_NAME.search(complaint)
    if found:
        # The name itself stays unspoken - it is input, and a passphrase
        # typed in place of a name would be quoted with it. A near miss
        # among the known names says enough on its own.
        close = difflib.get_close_matches(found.group(1),
                                          parameter_names(parser), 1)
        if close:
            return ("This sensor has no parameter by one of the names "
                    "given. Did you mean %s?" % close[0])
        return "This sensor has no parameter by one of the names given."
    return general


def setup():
    argparser = RecordingParser(
        description="The script tests the WLAN authentication of this probe.",
        exit_on_error=False,
    )

    argparser.add_argument("--interface", default="wlan0",
                           help="The reserved wireless test interface.")
    argparser.add_argument("--ssid", help="The SSID to authenticate against.")
    argparser.add_argument("--auth", choices=("psk", "peap", "eap-tls"),
                           help="The authentication method.")
    argparser.add_argument("--profile",
                           help="Credential profile stored on the probe.")
    argparser.add_argument("--psk", help="The WPA2 passphrase.")
    argparser.add_argument("--identity", help="The enterprise user name.")
    argparser.add_argument("--password", help="The enterprise password.")
    argparser.add_argument("--anonymous-identity",
                           help="The outer identity for the tunnel.")
    argparser.add_argument("--ca-cert",
                           help="CA certificate file that signed the RADIUS server.")
    argparser.add_argument("--domain-suffix-match",
                           help="Required suffix of the RADIUS server certificate.")
    argparser.add_argument("--no-verify-server", action="store_true",
                           help="Skip the RADIUS server certificate check.")
    argparser.add_argument("--client-cert",
                           help="Client certificate file for EAP-TLS.")
    argparser.add_argument("--private-key",
                           help="Private key file for EAP-TLS.")
    argparser.add_argument("--private-key-passwd",
                           help="Passphrase of the private key.")
    argparser.add_argument("--bssid", help="Restrict the test to one access point.")
    argparser.add_argument("--hidden", action="store_true",
                           help="The SSID is not broadcast.")
    argparser.add_argument("--stage", choices=("assoc", "dhcp"), default="dhcp",
                           help="Stop after authentication or after DHCP.")
    argparser.add_argument("--gateway", default="",
                           help="Address to ping after DHCP, or none to skip the check.")
    argparser.add_argument("--mac", default="auto",
                           help="The client address: auto for one per network, permanent for the radio's own, or a fixed address.")
    argparser.add_argument("--timeout", type=int, default=45,
                           help="The time budget for the whole test in seconds.")
    argparser.add_argument("--max-age", type=int, default=300,
                           help="The oldest measurement this sensor accepts, in seconds.")
    argparser.add_argument("--measure-now", action="store_true",
                           help="Measure on the spot instead of collecting the scheduled result.")
    argparser.add_argument("--self-check", action="store_true",
                           help="Only verify that the privileged helper is reachable.")
    pipestring = ""
    try:
        # Is a terminal?
        if sys.stdin.isatty():
            args = argparser.parse_args()
        else:
            pipestring = sys.stdin.read().rstrip()
            args = argparser.parse_args(shlex.split(pipestring))

    except argparse.ArgumentError as problem:
        # Use helpful message, free of sensitive data.
        fail(parse_failure(pipestring, str(problem), argparser))
    except ValueError:
        # shlex.split fails on an unpaired quotation mark.
        fail(parse_failure(pipestring))
    except SystemExit as termination:
        # exit_on_error only covers conversion failures. On an unknown
        # parameter or a value outside the choices, argparse goes through
        # error() - RecordingParser holds that text so the cause survives
        # into the channel message. Help (code 0) stays untouched so the
        # terminal invocation keeps working.
        if termination.code == 0:
            raise
        fail(parse_failure(pipestring, argparser.complaint, argparser))
    return vars(args)


def call_helper(job: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Task the privileged service and read back its result.

    The task travels over the socket, not the command line, so passwords and
    passphrases never appear in the process list.
    """
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
             "prtg-sensor-wlan-auth.socket on this probe.")
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


# prtg.standardlookups.yesno.stateyesok only knows 1 = Yes (Ok) and
# 2 = No (Error) - the counting comes from SNMP, where 1 means true and 2
# false. The lookup does not know a 0: PRTG then shows "undefined lookup
# value" and turns it into a mere warning where an error should stand.
# Seen on a real probe - the sensor stayed yellow although the measurement
# had failed.
LOOKUP_YES = 1
LOOKUP_NO = 2

# prtg.standardlookups.netapp.nichealth knows a third value: 3 = Not
# Available with state "None", which alarms on nothing. That is what
# "registered, not measured yet" and "older than --max-age" need - neither
# an alarm nor a silent success. Values 1 and 2 are the same as in the
# yes/no lookup above, so a channel that used to carry it keeps its
# history. aruba-uplink reports an unused uplink through the same lookup.
LOOKUP_NONE = 3
RESULT_LOOKUP = "prtg.standardlookups.netapp.nichealth"


def lookup_value(condition) -> int:
    """Translate a yes/no into the values PRTG understands."""
    return LOOKUP_YES if condition else LOOKUP_NO


# PRTG keeps a sensor message up to this length.
MESSAGE_LIMIT = 2000

# Where the platform shows one run of the sensor log. The probe does not know
# the platform's address - the return channel talks to a port of its own - so
# the message carries the path, and the operator puts it after the address
# they open the platform with.
RUN_PATH = "/sensor-logs/%s"
RECORD_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                       r"[0-9a-f]{12}")


def with_run_link(message: str, record_id: Any) -> str:
    """The message, cut to PRTG's limit, ending on the run in the log.

    The link goes last and is never the part that is cut: on a failed run
    it is the way to the raw supplicant and DHCP output.
    """
    if not isinstance(record_id, str) or not RECORD_ID.fullmatch(record_id):
        return message[:MESSAGE_LIMIT]
    link = "; log " + RUN_PATH % record_id
    return message[:MESSAGE_LIMIT - len(link)] + link


def age_part(age) -> list[str]:
    """How long ago this was measured, where there is an age at all."""
    return [] if age is None else ["age %d s" % int(age)]


def channel(identifier: int, name: str, value: int, **extra) -> dict[str, Any]:
    result = {"id": identifier, "name": name, "type": "integer", "value": value}
    result.update(extra)
    return result


def work(args: dict[str, Any]):
    if args["self_check"]:
        answer = call_helper({"action": "ping"}, 30)
        if answer.get("result") != "ok":
            fail("Self-check failed: %s" % answer.get("message", "unknown"))
        return {
            "version": 2,
            "status": "ok",
            "message": "The privileged helper is reachable.",
            "channels": [
                channel(10, "Helper Reachable", LOOKUP_YES, type="lookup",
                        lookup_name="prtg.standardlookups.yesno.stateyesok"),
            ],
        }

    if not args["ssid"] and not args["profile"]:
        fail("Either --ssid or --profile is required.")

    job = {
        "action": "test",
        "interface": args["interface"],
        "ssid": args["ssid"],
        "auth": args["auth"],
        "profile": args["profile"],
        "psk": args["psk"],
        "identity": args["identity"],
        "password": args["password"],
        "anonymous_identity": args["anonymous_identity"],
        "ca_cert": args["ca_cert"],
        "domain_suffix_match": args["domain_suffix_match"],
        "verify_server": not args["no_verify_server"],
        "client_cert": args["client_cert"],
        "private_key": args["private_key"],
        "private_key_passwd": args["private_key_passwd"],
        "bssid": args["bssid"],
        "hidden": args["hidden"],
        "stage": args["stage"],
        "gateway": args["gateway"],
        "mac": args["mac"],
        "timeout": args["timeout"],
    }
    # Collecting a stored result is a memory read in the helper, so it
    # needs none of the patience a measurement needs. Measuring on the
    # spot still does: the helper then waits for the radio and runs the
    # whole test before it answers.
    if args["measure_now"]:
        job["action"] = "test"
        answer = call_helper(job, args["timeout"] + 20)
    else:
        job["action"] = "collect"
        job["max_age"] = args["max_age"]
        answer = call_helper(job, COLLECT_TIMEOUT)

    # "blocked" means the test never ran - missing interface, missing
    # permissions, wrong parameters. That is a sensor failure, not a
    # statement about the Wi-Fi.
    if answer.get("result") == "blocked":
        fail(with_run_link(
            "Test could not run (%s): %s"
            % (answer.get("code", "unknown"), answer.get("message", "")),
            answer.get("record_id")))

    outcome = answer.get("result")
    timings = answer.get("timings") or {}
    details = answer.get("details") or {}
    code = answer.get("code", "ok")
    # What the radio reported, kept apart from the code the channel ends
    # up carrying: a setting this sensor cannot meet replaces that code,
    # and the message still has to name the cause the radio saw.
    observed = code
    age = answer.get("age")

    # A measurement older than this sensor asked for is not a measurement
    # any more. Serving it anyway would let the sensor stay green on a
    # sign-in from hours ago - a failure that looks like nothing is wrong,
    # and therefore worse than one that shows.
    stale = age is not None and age > args["max_age"]

    # A round over every network on this radio takes as long as it takes,
    # and the helper measures one network at most once a minute. Asking for
    # a value fresher than both together is a setting no schedule can meet,
    # so the sensor would go red on its own parameter rather than on the
    # Wi-Fi - and periodically, which reads like an intermittent fault. The
    # helper reports the smallest max_age it can keep because only it sees
    # the other sensors; a helper from before the floor reports only the
    # round. This is where the setting is judged and named.
    budget = answer.get("min_max_age") or answer.get("round_budget")
    sharing = answer.get("job_count") or 1
    unreachable = (not args["measure_now"] and budget is not None
                   and budget > args["max_age"])

    if unreachable:
        code = "max-age-too-low"
    elif outcome == "pending":
        code = "not-measured"
    elif stale:
        code = "result-stale"

    # A wrong setting does not invalidate the measurement that did come
    # in. The result keeps its channel and its history, and the cause
    # travels in the message and the failure code instead.
    measured = outcome in ("ok", "failed") and not stale
    state = lookup_value(outcome == "ok") if measured else LOOKUP_NONE

    # The channel structure follows the parameters, never the result. A
    # channel that is missing while nothing has been measured and appears
    # afterwards tears its history apart in PRTG.
    channels = [
        channel(10, "Auth Result", state, type="lookup",
                lookup_name=RESULT_LOOKUP),
        channel(11, "Total Time", int(timings.get("total_ms", 0)),
                kind="time_milliseconds"),
        channel(12, "Association Time", int(timings.get("assoc_ms", 0)),
                kind="time_milliseconds"),
        channel(13, "Auth Time", int(timings.get("auth_ms", 0)),
                kind="time_milliseconds"),
        channel(15, "Signal", int(details.get("signal_dbm") or 0),
                kind="custom", display_unit="dBm"),
        channel(16, "Frequency", int(details.get("frequency_mhz") or 0),
                kind="custom", display_unit="MHz"),
        channel(17, "Measurement Age", int(age or 0),
                kind="custom", display_unit="s"),
        channel(18, "Failure Code", FAILURE_CODES.get(code, 99)),
    ]
    if args["stage"] == "dhcp":
        channels.append(channel(14, "DHCP Time",
                                int(timings.get("dhcp_ms", 0)),
                                kind="time_milliseconds"))
    # An address proves that a DHCP server answered, not that the network
    # carries traffic. A client put in the wrong network can hold a valid
    # lease and reach nothing at all, and only this channel tells the two
    # apart - which is why it is a stage of its own rather than an extra
    # reading on every run.
    if args["stage"] == "dhcp" and args["gateway"] != "none":
        reached = measured and outcome == "ok"
        channels.append(channel(
            19, "Gateway Reachable",
            lookup_value(True) if reached
            else lookup_value(False) if measured and code == "gateway-unreachable"
            else LOOKUP_NONE,
            type="lookup", lookup_name=RESULT_LOOKUP))
        channels.append(channel(20, "Gateway Time",
                                int(timings.get("gateway_ms", 0)),
                                kind="time_milliseconds"))
    # Ascending, so the channel list in PRTG has the same order no matter
    # which parameters are set.
    channels.sort(key=lambda entry: entry["id"])

    # One line, the network and its verdict first: in a list of sensors the
    # message is all that shows without opening one. The rest are short
    # parts in a fixed order, and a part without a value is left out rather
    # than shown empty.
    #
    # The SSID comes from the helper's reading of the link, so it names
    # the network actually joined. Only a run that never got that far
    # falls back to the parameter, and with --profile there is none: the
    # credentials, the SSID among them, stay on the probe.
    network = details.get("ssid") or args["ssid"] or "WLAN"

    def link_parts() -> list[str]:
        parts = []
        # The address the run signed in with is what to look for in the
        # gateway's user table and in Central.
        if details.get("mac"):
            parts.append("client %s%s" % (
                details["mac"],
                " (permanent)" if details.get("mac_kind") == "permanent"
                else ""))
        if details.get("bssid"):
            parts.append("AP %s" % details["bssid"])
        # The address alone does not say which network answered. Two SSIDs
        # that should sit in different VLANs are told apart by their DHCP
        # server, and without it a lease from the wrong network reads like
        # a healthy result.
        if details.get("ip_address"):
            parts.append("IP %s%s" % (
                details["ip_address"],
                " from %s" % details["dhcp_server"]
                if details.get("dhcp_server") else ""))
        return parts

    if outcome == "pending":
        parts = ["%s: waiting for first measurement" % network]
    elif stale:
        parts = ["%s: last measurement %d s old, limit %d s"
                 % (network, int(age), args["max_age"])] + link_parts()[:1]
    elif outcome == "ok":
        parts = ["%s: OK" % network] + link_parts()
        # The address says a server answered; this says the network
        # carries traffic. Named once when it is the DHCP server, since
        # the same address twice reads like two machines.
        if details.get("gateway"):
            took = int(timings.get("gateway_ms", 0))
            if details["gateway"] == details.get("dhcp_server"):
                parts.append("ping %d ms" % took)
            else:
                parts.append("%s %s ping %d ms" % (
                    details.get("gateway_kind") or "gateway",
                    details["gateway"], took))
        parts += age_part(age)
    else:
        parts = ["%s: %s" % (network, answer.get("message") or "failed")]
        parts += link_parts() + ["code %s" % observed] + age_part(age)

    # First in the line, because it is the one thing an operator has to
    # act on: nothing about the schedule improves until the setting fits
    # the radio.
    if unreachable:
        parts.insert(0, "Set --max-age >= %d s (%d %s on this radio)" % (
            budget, sharing, "network" if sharing == 1 else "networks"))
    message = "; ".join(parts)
    # Only a failed run carries the way to its record: a green sensor sends
    # nobody looking, and a stored result that is too old or not there yet
    # says nothing about a run.
    failed = outcome == "failed" and not stale
    message = with_run_link(message,
                            answer.get("record_id") if failed else None)

    # A failed sign-in remains a valid measurement: the sensor keeps
    # delivering values and the "Auth Result" channel carries the alarm.
    # That keeps the history of the timing channels readable.
    return {
        "version": 2,
        "status": "ok",
        "message": message,
        "channels": channels,
    }


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
