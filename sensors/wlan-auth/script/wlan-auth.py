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
import json
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
}


# A parameter line typed on a phone, or pasted out of a document, tends
# to carry a typographic dash where two hyphens belong. The characters
# are nearly indistinguishable on screen, so the sensor stays red while
# the line looks right - naming the character is the whole difference.
TYPOGRAPHIC_DASHES = {
    "–": "an en dash", "—": "an em dash", "−": "a minus sign",
    "‑": "a non-breaking hyphen",
}


def parse_failure(text: str) -> str:
    """Why the parameters could not be read, without quoting them."""
    message = "Could not parse input parameter. Check configured parameters."
    named = [name for dash, name in TYPOGRAPHIC_DASHES.items() if dash in text]
    if named:
        message += (" The parameters contain %s where a parameter name "
                    "needs two plain hyphens." % named[0])
    return message


def setup():
    argparser = argparse.ArgumentParser(
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

    except argparse.ArgumentError:
        # Use helpful message, free of sensitive data.
        fail(parse_failure(pipestring))
    except ValueError:
        # shlex.split fails on an unpaired quotation mark.
        fail(parse_failure(pipestring))
    except SystemExit as termination:
        # exit_on_error only covers conversion failures. On an unknown
        # parameter, argparse terminates the process itself with code 2 -
        # PRTG would then get no output at all and only report that it
        # cannot read the result. Help (code 0) stays untouched so the
        # terminal invocation keeps working.
        if termination.code == 0:
            raise
        fail(parse_failure(pipestring))
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


def age_suffix(age) -> str:
    """How long ago this was measured, where there is an age at all."""
    if age is None:
        return ""
    return ", measured %d s ago" % int(age)


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
        fail("Test could not run (%s): %s"
             % (answer.get("code", "unknown"), answer.get("message", "")))

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

    # A round over every network on this radio takes as long as it takes.
    # Asking for a value fresher than that is a setting no schedule can
    # meet, so the sensor would go red on its own parameter rather than
    # on the Wi-Fi - and periodically, which reads like an intermittent
    # fault. The helper reports the round because only it sees the other
    # sensors; this is where the setting is judged and named.
    budget = answer.get("round_budget")
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
    # Ascending, so the channel list in PRTG has the same order no matter
    # which parameters are set.
    channels.sort(key=lambda entry: entry["id"])

    # Which network, on which access point, did it get an address, and how
    # old is all of that - in a list of sensors the message line is all
    # that is visible without opening one.
    #
    # The SSID comes from the helper's reading of the link, so it names
    # the network actually joined. Only a run that never got that far
    # falls back to the parameter, and with --profile there is none: the
    # credentials, the SSID among them, stay on the probe.
    network = details.get("ssid") or args["ssid"] or ""
    quoted = ' on "%s"' % network if network else ""
    if outcome == "pending":
        message = ("Registered on this probe%s, waiting for the first "
                   "measurement" % quoted)
    elif stale:
        message = ("The last measurement%s is %d seconds old, past the %d "
                   "this sensor accepts"
                   % (quoted, int(age), args["max_age"]))
    elif outcome == "ok":
        message = "Authentication succeeded%s via %s" % (
            quoted, details.get("bssid") or args["interface"])
        if details.get("ip_address"):
            message += ", DHCP offered %s" % details["ip_address"]
            # The address alone does not say which network answered. Two
            # SSIDs that should sit in different VLANs are told apart by
            # their DHCP server, and without it a lease from the wrong
            # network reads like a healthy result.
            if details.get("dhcp_server"):
                message += " from %s" % details["dhcp_server"]
        message += age_suffix(age)
    else:
        message = "%s%s (%s)%s" % (
            answer.get("message", "Authentication failed"), quoted, observed,
            age_suffix(age))

    # First in the line, because it is the one thing an operator has to
    # act on: nothing about the schedule improves until the setting fits
    # the radio.
    if unreachable:
        message = ("Set --max-age to at least %d seconds: a round over the "
                   "%d %s on this radio takes that long, so %d can never be "
                   "met. %s"
                   % (budget, sharing,
                      "network" if sharing == 1 else "networks",
                      args["max_age"], message))

    # A failed sign-in remains a valid measurement: the sensor keeps
    # delivering values and the "Auth Result" channel carries the alarm.
    # That keeps the history of the timing channels readable.
    return {
        "version": 2,
        "status": "ok",
        "message": message[:2000],
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
