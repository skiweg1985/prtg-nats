#!/usr/bin/env python3

"""Checks for the sensor scripts under sensors/.

Runs without network, Docker or a probe. What is checked is the contract
between repository and probe (manifest, shebang, self-test), the output
format for PRTG, and the security-critical spot of the Wi-Fi sensor: the
generation of the wpa_supplicant configuration from foreign strings.

The throughput sensor never really measures: its measurement run is
replaced, so the checks work without network and burn no bandwidth.

Called by tests/check-static.sh; the exit code is 1 as soon as one check
fails.
"""

import argparse
import contextlib
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

# The checks load the sensors as modules. Without this line a __pycache__
# directory would be left next to every sensor.
sys.dont_write_bytecode = True

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENSOR_DIR = os.path.join(PROJECT_DIR, "sensors")
FAILURES = 0


def check(description, actual, expected):
    global FAILURES
    if actual == expected:
        print("  ok    %s" % description)
    else:
        FAILURES += 1
        print("  FAIL  %s" % description, file=sys.stderr)
        print("        expected: %r" % (expected,), file=sys.stderr)
        print("        received: %r" % (actual,), file=sys.stderr)


def check_true(description, condition):
    check(description, bool(condition), True)


def read_manifest(path):
    values = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def load_module(name, path):
    """Load a file as a module, even without a .py suffix.

    On the probe the privileged helper is named like a command and has no
    suffix; the loader then has to be named explicitly.
    """
    loader = importlib.machinery.SourceFileLoader(name, path)
    specification = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(specification)
    loader.exec_module(module)
    return module


def run_script(path, parameters):
    """Call a sensor script the way the probe does: over stdin."""
    return subprocess.run(
        [sys.executable, path],
        input=parameters,
        capture_output=True,
        text=True,
        timeout=60,
    )


def sensor_names():
    return sorted(
        entry for entry in os.listdir(SENSOR_DIR)
        if os.path.isfile(os.path.join(SENSOR_DIR, entry, "manifest.env"))
    )


def check_manifests():
    print("\n== Sensor manifests ==")
    names = sensor_names()
    check_true("at least one sensor present", names)

    for name in names:
        directory = os.path.join(SENSOR_DIR, name)
        manifest = read_manifest(os.path.join(directory, "manifest.env"))
        check("%s: name matches the directory" % name,
              manifest.get("SENSOR_NAME"), name)
        check_true("%s: version set" % name, manifest.get("SENSOR_VERSION"))
        check_true("%s: description set" % name,
                   manifest.get("SENSOR_DESCRIPTION"))

        for key in ("SENSOR_SCRIPT", "SENSOR_PRIVILEGED", "SENSOR_REQUIREMENTS"):
            relative = manifest.get(key, "")
            if not relative:
                continue
            path = os.path.join(directory, relative)
            check_true("%s: %s exists" % (name, relative),
                       os.path.isfile(path))
            if not path.endswith(".txt") and os.path.isfile(path):
                check_python_file(name, relative, path)

        script = manifest.get("SENSOR_SCRIPT", "")
        if script and os.path.isfile(os.path.join(directory, script)):
            with open(os.path.join(directory, script), encoding="utf-8") as file:
                source = file.read()
            # Without a self-test the probe cannot verify an activation
            # and therefore cannot roll it back either.
            check_true("%s: script knows --self-check" % name,
                       "--self-check" in source)
            check_true("%s: script always ends with exit code 0" % name,
                       "sys.exit(0)" in source and "sys.exit(1)" not in source)


def read_declaration(name):
    """The parameters.json of a sensor, or None if it ships none."""
    path = os.path.join(SENSOR_DIR, name, "parameters.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class CapturedParser(Exception):
    """Carries the parser out of setup() before the sensor starts working."""

    def __init__(self, parser):
        Exception.__init__(self)
        self.parser = parser


def sensor_parser(module):
    """The argparse definition of a sensor, without running it.

    setup() builds the parser and parses in one go, so the parser is taken
    where it is complete and before anything happens with it: at the
    parse_args() call, which is made never to return. stdin is emptied for
    the same reason - the sensors read their parameters from it and would
    otherwise block on the terminal.
    """
    original = argparse.ArgumentParser.parse_args

    def capture(self, *arguments, **keywords):
        raise CapturedParser(self)

    argparse.ArgumentParser.parse_args = capture
    stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        module.setup()
    except CapturedParser as captured:
        return captured.parser
    finally:
        argparse.ArgumentParser.parse_args = original
        sys.stdin = stdin
    return None


def argparse_type(action):
    if action.nargs == 0:
        return "boolean"
    if action.type is int:
        return "integer"
    if action.choices:
        return "choice"
    return "string"


def argparse_options(parser):
    """Every option of a sensor by its long form, help included."""
    options = {}
    for action in parser._actions:
        if action.dest == "help" or not action.option_strings:
            continue
        options[action.option_strings[-1]] = action
    return options


def locale_keys():
    """Every dotted key of both locale files.

    Read here rather than left to "npm run i18n:check", which compares the
    two files against each other and never learns that a sensor asked for a
    key neither of them has - that one renders as the raw key.
    """
    locales = {}
    for language in ("de", "en"):
        path = os.path.join(PROJECT_DIR, "web", "frontend", "src", "i18n",
                            "locales", "%s.json" % language)
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        keys = set()

        def walk(node, prefix):
            for key, value in node.items():
                full = "%s.%s" % (prefix, key) if prefix else key
                if isinstance(value, dict):
                    walk(value, full)
                else:
                    keys.add(full)

        walk(document, "")
        locales[language] = keys
    return locales


def template_keys(name):
    """The keys a sensor's profile template documents.

    Commented-out ones count: the template lists what a method needs, and
    an optional key belongs there struck through rather than absent.
    """
    directory = os.path.join(SENSOR_DIR, name, "profiles")
    if not os.path.isdir(directory):
        return None
    keys = set()
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".env.template"):
            continue
        with open(os.path.join(directory, entry), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip().lstrip("#").strip()
                if "=" in line:
                    keys.add(line.partition("=")[0].strip())
    return keys


def check_parameter_declarations():
    """parameters.json against the script it describes.

    The declaration is what the web interface renders as its parameter
    reference and as the form for a variant. A name that drifted from the
    script would send an operator to paste a parameter the sensor rejects -
    which is exactly the mistake the reference exists to prevent.

    The declaration may be *more* precise than argparse: a sensor that
    validates its own values, as aruba-uplink does for --primary, can name
    them as choices here. It may never contradict it.
    """
    print("\n== Parameter declarations ==")
    locales = locale_keys()
    declared = 0

    for name in sensor_names():
        document = read_declaration(name)
        check_true("%s: declares its parameters" % name, document is not None)
        if document is None:
            continue
        declared += 1

        manifest = read_manifest(os.path.join(SENSOR_DIR, name, "manifest.env"))
        script = os.path.join(SENSOR_DIR, name, manifest["SENSOR_SCRIPT"])
        module = load_module(name.replace("-", "_") + "_declaration", script)
        parser = sensor_parser(module)
        check_true("%s: the parser could be read" % name, parser is not None)
        if parser is None:
            continue

        options = argparse_options(parser)
        entries = {entry["name"]: entry for entry in document.get("parameters", [])}
        check("%s: declares every option of the script" % name,
              sorted(set(options) - set(entries)), [])
        check("%s: declares no option the script does not have" % name,
              sorted(set(entries) - set(options)), [])

        for option, action in sorted(options.items()):
            entry = entries.get(option)
            if entry is None:
                continue
            check_parameter_entry(name, option, entry, action)

        check_profile_declaration(name, document)
        check_translation_keys(name, document, locales)

    check_true("every sensor declares its parameters", declared == len(sensor_names()))


def check_parameter_entry(name, option, entry, action):
    """One declared parameter against the argparse action behind it."""
    label = "%s %s" % (name, option)
    expected_type = argparse_type(action)
    declared_type = entry.get("type", "string")
    # A choice is the one refinement allowed: the sensor validates the
    # values itself, and naming them turns a free text field into a list.
    refined = expected_type == "string" and declared_type == "choice"
    check("%s: type" % label,
          declared_type if not refined else expected_type, expected_type)
    if refined:
        check_true("%s: a refined choice names its values" % label,
                   entry.get("choices"))
    if action.choices:
        check("%s: choices" % label,
              list(entry.get("choices", [])), list(action.choices))

    # The help text is what the reference shows. Whitespace differs because
    # argparse strings are wrapped in the source.
    check("%s: description matches the script's help" % label,
          " ".join(entry.get("description", "").split()),
          " ".join((action.help or "").split()))

    repeatable = type(action).__name__ == "_AppendAction"
    check("%s: repeatable" % label, bool(entry.get("repeatable")), repeatable)

    # An empty string or list is argparse's way of saying "unset"; only a
    # real default belongs in the reference.
    default = action.default
    if repeatable or expected_type == "boolean" or default in (None, "", []):
        check("%s: declares no default" % label, "default" in entry, False)
    else:
        check("%s: default" % label, entry.get("default"), default)

    if action.required:
        check("%s: required, as argparse demands" % label,
              bool(entry.get("required")), True)


def check_profile_declaration(name, document):
    """Settings, credentials and files of one sensor.

    The keys end up in a file the probe helper accepts only as upper-case
    KEY=VALUE lines, and each of them has exactly one writer.
    """
    settings = document.get("settings", [])
    credentials = document.get("credentials", [])
    files = document.get("files", [])
    if not (settings or credentials or files):
        return

    keys = [entry["name"] for entry in settings + credentials + files]
    check("%s: no profile key is declared twice" % name,
          sorted(key for key in set(keys) if keys.count(key) > 1), [])
    for key in keys:
        check("%s: %s is a key the probe accepts" % (name, key),
              bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", key)), True)

    documented = template_keys(name)
    if documented is not None:
        check("%s: every profile key is in the template" % name,
              sorted(set(keys) - documented), [])

    for entry in files:
        extension = entry.get("extension", ".pem")
        # The extension is part of the path the helper builds on the probe.
        check("%s: %s has a usable extension" % (name, entry["name"]),
              extension.startswith(".") and "/" not in extension, True)


def check_translation_keys(name, document, locales):
    """A label_key that exists in neither locale renders as the raw key."""
    sections = ("parameters", "settings", "credentials", "files")
    wanted = set()
    for section in sections:
        for entry in document.get(section, []):
            for field in ("label_key", "description_key"):
                if entry.get(field):
                    wanted.add(entry[field])
    for language, keys in sorted(locales.items()):
        check("%s: every translation key exists in %s.json" % (name, language),
              sorted(wanted - keys), [])


def check_privilege_path():
    """The path to root privileges must not fall back to sudo.

    prtg.mpprobe.service runs with NoNewPrivileges=yes; the kernel then
    ignores the setuid bit of sudo. A sensor that tries anyway only fails
    in operation - and the self-test would not notice if it did not run in
    the service context.
    """
    print("\n== Privileges ==")
    # Deliberately across all sensors with a privileged part, not just
    # the first that had one: the rule applies to every future one as
    # well, and a check that names one sensor misses the next.
    privileged = 0
    for name in sensor_names():
        manifest = read_manifest(
            os.path.join(SENSOR_DIR, name, "manifest.env"))
        if not manifest.get("SENSOR_PRIVILEGED"):
            continue
        privileged += 1
        script = os.path.join(SENSOR_DIR, name, manifest["SENSOR_SCRIPT"])
        with open(script, encoding="utf-8") as handle:
            source = handle.read()
        # What is sought is the string literal of an invocation, not the
        # word in an explanatory comment.
        check("%s: the sensor script calls no sudo" % name,
              '"sudo"' in source or "'sudo'" in source, False)
        check("%s: it talks to the helper over a Unix socket" % name,
              "AF_UNIX" in source, True)
    check_true("at least one sensor with a privileged part", privileged)

    helper = os.path.join(PROJECT_DIR, "libexec", "prtg-nats-probe-helper")
    with open(helper, encoding="utf-8") as handle:
        source = handle.read()
    check("deployment creates no sudo rule for sensors",
          "NOPASSWD" in source, False)
    check("it sets up a socket service instead",
          "ListenStream=" in source, True)
    check("the socket belongs to the service user group",
          "SocketGroup=" in source, True)
    # The actual reason the first version slipped through.
    check("the self-test reproduces the MPP unit hardening",
          "NoNewPrivileges" in source and "systemd-run" in source, True)
    # The helper installs python3-venv on demand. A package name from
    # the management channel would be a way to bring arbitrary software
    # onto the probe - it is therefore spelled out in the script.
    check("the on-demand install names its package literally",
          "apt-get install -y python3-venv" in source, True)
    check("no package name from a variable",
          "apt-get install -y ${" in source, False)
    # The helper runs with umask 077, "python3 -m venv" inherits it.
    # Without these two lines the environment belongs to root alone, and
    # the self-test fails because the service user may not start the
    # interpreter the sensor's shebang points at.
    check("the virtual environment belongs to the service user group",
          'chown -R "root:${service_user}"' in source, True)
    check("and is readable and executable for it",
          "chmod -R go-w,g+rX" in source, True)


def check_python_file(name, relative, path):
    with open(path, "rb") as handle:
        source = handle.read()
    first_line = source.split(b"\n", 1)[0].decode("utf-8", "replace")
    check("%s: %s has a python3 shebang" % (name, relative),
          first_line in ("#!/usr/bin/env python3", "#!/usr/bin/python3"),
          True)
    try:
        compile(source, path, "exec")
        compiled = True
    except SyntaxError:
        compiled = False
    check("%s: %s is valid Python" % (name, relative), compiled, True)
    check("%s: %s is executable" % (name, relative),
          os.access(path, os.X_OK), True)


def check_wlan_auth_output():
    """The output format is the contract with PRTG."""
    print("\n== wlan-auth: output for PRTG ==")
    script = os.path.join(SENSOR_DIR, "wlan-auth", "script", "wlan-auth.py")
    module = load_module("wlan_auth", script)

    completed = run_script(script, "--ssid Test --auth psk --psk short\n")
    check("missing privileges still yield exit code 0",
          completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("the answer carries the schema version", document.get("version"), 2)
    check("an uncallable helper reports a sensor error",
          document.get("status"), "error")

    completed = run_script(script, "--totally-unknown\n")
    check("an unknown parameter yields exit code 0", completed.returncode, 0)
    check("an unknown parameter reports a sensor error",
          json.loads(completed.stdout).get("status"), "error")

    # Parameters typed on a phone or pasted from a document carry a
    # typographic dash instead of two hyphens, and on screen the line
    # looks right while the sensor stays red. Seen on a probe: a sensor
    # down with "could not parse" because the parameter began with an em
    # dash. The character is named, the parameters themselves are not -
    # they carry passwords.
    completed = run_script(
        script, "—max-age 60 --ssid Test --auth psk --psk short\n")
    message = json.loads(completed.stdout).get("message", "")
    check("a typographic dash in the parameters is named",
          "an em dash" in message, True)
    check("and so is what belongs there instead",
          "two plain hyphens" in message, True)
    completed = run_script(
        script, "--ssid Test --auth psk --psk short --totally-unknown\n")
    check("an ordinary mistake is not blamed on a dash",
          "dash" in json.loads(completed.stdout).get("message", ""), False)
    check("the parameters are never quoted back",
          "short" in message, False)

    # A value outside the choices is the other way a parameter line goes
    # wrong, and it happened here: --stage gateway, left over from a
    # draft where the ping was a stage of its own. PRTG then shows only
    # "could not parse", which says nothing about which parameter or
    # what it would accept. Both come out of the parser, so they stay
    # right when a choice is added.
    base = "--ssid Test --auth psk --psk short"
    completed = run_script(script, base + " --stage gateway\n")
    message = json.loads(completed.stdout).get("message", "")
    check("a value outside the choices names the parameter",
          "--stage" in message, True)
    check("and the values it does accept",
          "assoc or dhcp" in message, True)
    check("without repeating the one that was given",
          "gateway" in message, False)
    completed = run_script(script, base.replace("psk", "wpa3", 1) + "\n")
    check("three choices read as a list",
          "psk, peap or eap-tls" in json.loads(completed.stdout)["message"],
          True)
    completed = run_script(script, base + " --timeout abc\n")
    check("a number that is not one says so",
          "--timeout needs a whole number."
          in json.loads(completed.stdout)["message"], True)
    completed = run_script(script, base + " --max-age\n")
    check("a parameter without its value says so",
          "--max-age was given without a value."
          in json.loads(completed.stdout)["message"], True)
    completed = run_script(script, base + " --stagee dhcp\n")
    check("a misspelt name points at the one that was meant",
          "Did you mean --stage?"
          in json.loads(completed.stdout)["message"], True)
    # A passphrase typed where a name belongs is still a passphrase, so
    # the unknown name itself is never spoken - only a known one.
    completed = run_script(script, base + " --Sommer2024 1\n")
    message = json.loads(completed.stdout).get("message", "")
    check("an unknown name is not quoted back", "Sommer2024" in message, False)
    check("but the sensor says that is what it is",
          "no parameter by one of the names given" in message, True)

    # An invented helper answer, so the output format is checkable
    # without Wi-Fi. This is what a failed sign-in looks like.
    module.call_helper = lambda job, timeout: {
        "result": "failed",
        "code": "auth-failed-eap",
        "message": "The RADIUS server rejected the credentials",
        "timings": {"total_ms": 4200, "assoc_ms": 2700},
        "details": {"signal_dbm": -61, "frequency_mhz": 5320},
    }
    arguments = {
        "self_check": False, "ssid": "Corporate", "profile": None,
        "interface": "wlan0", "auth": "peap", "psk": None,
        "identity": "monitor", "password": "secret", "anonymous_identity": None,
        "ca_cert": None, "domain_suffix_match": None, "no_verify_server": True,
        "client_cert": None, "private_key": None, "private_key_passwd": None,
        "bssid": None, "hidden": False, "stage": "dhcp", "timeout": 45,
        "max_age": None, "measure_now": False, "gateway": "", "mac": "auto",
    }
    result = module.work(arguments)
    channels = {entry["id"]: entry for entry in result["channels"]}
    # A failure is a valid measurement: the sensor stays "ok", the alarm
    # hangs on the channel. Otherwise the timing channels' history would
    # break off.
    check("a rejected sign-in remains a valid result",
          result["status"], "ok")
    check("the success channel says no", channels[10]["value"], 2)
    check("the success channel uses a standard lookup",
          channels[10]["lookup_name"],
          "prtg.standardlookups.netapp.nichealth")
    check("the association time survives a failure",
          channels[12]["value"], 2700)
    check("the failure cause is machine-readable", channels[18]["value"], 3)
    # The channel structure follows the parameters, never the answer: with
    # --stage dhcp the channel is there even when this run never got an
    # address, because a channel that comes and goes tears its history
    # apart in PRTG.
    check("with a DHCP stage the DHCP channel is present", 14 in channels, True)
    check("and reads zero where nothing was measured",
          channels[14]["value"], 0)
    assoc_only = {entry["id"]: entry for entry
                  in module.work(dict(arguments, stage="assoc"))["channels"]}
    check("without a DHCP stage it is absent", 14 in assoc_only, False)
    check("channels are sorted ascending",
          [entry["id"] for entry in result["channels"]],
          sorted(entry["id"] for entry in result["channels"]))
    check("the message names the cause",
          "auth-failed-eap" in result["message"], True)

    # A lease says nothing about which network handed it out. Two SSIDs
    # that belong in different VLANs are told apart by their DHCP server,
    # and without it a lease from the wrong network reads like success.
    module.call_helper = lambda job, timeout: {
        "result": "ok",
        "code": "ok",
        "message": "Authentication succeeded",
        "timings": {"total_ms": 3100, "assoc_ms": 900, "auth_ms": 300,
                    "dhcp_ms": 700},
        "details": {"ssid": "Guest", "bssid": "00:00:5e:00:53:c2",
                    "ip_address": "198.51.100.19",
                    "dhcp_server": "198.51.100.1",
                    "signal_dbm": -58, "frequency_mhz": 5220},
    }
    result = module.work(dict(arguments, ssid="Guest", auth="psk",
                              psk="passphrase123", identity=None,
                              password=None))
    check("the message names the address", "198.51.100.19" in result["message"],
          True)
    check("and the server that handed it out",
          "from 198.51.100.1" in result["message"], True)

    # An address proves that a DHCP server answered, nothing more. Seen on
    # a probe: a guest network that handed out addresses of the internal
    # one and then dropped every packet - the sensor reported "ok" for
    # minutes while the client could not reach its own gateway.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {"total_ms": 3100, "assoc_ms": 900, "auth_ms": 300,
                    "dhcp_ms": 700, "gateway_ms": 4},
        "details": {"ssid": "Guest", "ip_address": "198.51.100.19",
                    "dhcp_server": "198.51.100.1",
                    "gateway": "198.51.100.254", "gateway_kind": "router"},
        "age": 3.0,
    }
    result = module.work(dict(arguments))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a reachable gateway is its own channel", channels[19]["value"], 1)
    check("and carries the standard lookup", channels[19]["lookup_name"],
          "prtg.standardlookups.netapp.nichealth")
    check("the answer time is reported", channels[20]["value"], 4)
    check("the message says the network carries traffic",
          "router 198.51.100.254 ping 4 ms" in result["message"], True)

    # Where the lease names no router, the server that offered it is the
    # target - and then the same address must not appear twice in one line.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {"total_ms": 3100, "dhcp_ms": 700, "gateway_ms": 6},
        "details": {"ssid": "Guest", "ip_address": "198.51.100.19",
                    "dhcp_server": "198.51.100.1",
                    "gateway": "198.51.100.1", "gateway_kind": "dhcp server"},
        "age": 3.0,
    }
    message = module.work(dict(arguments))["message"]
    check("one machine is named once",
          message.count("198.51.100.1;") + message.count("198.51.100.1 ping"),
          1)
    check("and the ping is still reported", "; ping 6 ms" in message, True)

    # The check runs without being asked for, because an address that
    # carries no traffic is the failure worth catching. A network that
    # drops ICMP has no answer to give, and there --gateway none takes
    # the channels away with the question.
    off = {entry["id"] for entry in
           module.work(dict(arguments, gateway="none"))["channels"]}
    check("--gateway none removes the channel", 19 in off, False)
    check("and its time with it", 20 in off, False)
    check("the other channels stay", 14 in off, True)

    # A fixed address overrides what the lease says, for a network whose
    # router does not answer or does not exist in the lease at all.
    asked = {}

    def remember_gateway(job, timeout):
        asked.update(job)
        return {"result": "ok", "code": "ok", "message": "ok",
                "timings": {}, "details": {}, "age": 1.0}

    keep = module.call_helper
    module.call_helper = remember_gateway
    module.work(dict(arguments, gateway="198.51.100.254"))
    check("a fixed gateway reaches the helper", asked.get("gateway"),
          "198.51.100.254")
    module.work(dict(arguments))
    check("and by default the lease decides", asked.get("gateway"), "")
    module.call_helper = keep

    # A lease without a reachable gateway is the case this stage exists
    # for: the sign-in worked, the address arrived, and nothing moves.
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "gateway-unreachable",
        "message": "gateway 198.51.100.1 no reply",
        "timings": {"total_ms": 9000, "assoc_ms": 900, "dhcp_ms": 700},
        "details": {"ssid": "Guest", "ip_address": "198.51.100.19",
                    "gateway": "198.51.100.1"},
        "age": 3.0,
    }
    result = module.work(dict(arguments))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("an unreachable gateway reads as a failure", channels[19]["value"],
          2)
    check("and has its own failure code", channels[18]["value"], 14)
    check("the message keeps the address apart from the reachability",
          "gateway 198.51.100.1 no reply" in result["message"], True)

    # A job the scheduler has not reached yet is not a failure. It gets the
    # third lookup state, which alarms on nothing, and says so in the
    # message - a sensor error here would blame the Wi-Fi for a radio that
    # is simply still busy.
    module.call_helper = lambda job, timeout: {
        "result": "pending",
        "code": "not-measured",
        "message": "Registered on this probe, waiting for the first "
                   "measurement",
        "timings": {}, "details": {}, "age": None,
    }
    result = module.work(dict(arguments))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a job without a measurement is neither green nor red",
          channels[10]["value"], 3)
    check("it has its own failure code", channels[18]["value"], 11)
    check("the age channel exists from the first scan", 17 in channels, True)
    check("and reads zero until something was measured",
          channels[17]["value"], 0)
    check("the message says what it is waiting for",
          "waiting for first measurement" in result["message"], True)

    # Nobody sets a freshness any more (#192): the helper says when a
    # result counts as stale, about two rounds, and the sensor follows it.
    # Serving an older one would keep the sensor green on a sign-in from
    # hours ago.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {"total_ms": 3100, "assoc_ms": 900, "auth_ms": 300},
        "details": {"ssid": "Corporate"}, "age": 540.0,
        "round_time": 240.0, "stale_after": 480.0, "job_count": 4,
        "min_max_age": 480.0,
    }
    result = module.work(dict(arguments))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a result past what the helper allows stops counting",
          channels[10]["value"], 3)
    check("a stale result has its own failure code",
          channels[18]["value"], 12)
    check("the age channel carries the age", channels[17]["value"], 540)
    # The message points at a real problem - a stuck helper, a radio that
    # is crowded - in words an operator reads without opening anything.
    check("the message names age, round and the jobs sharing the radio",
          "Corporate: measurement 9 min old, round currently 4 min, "
          "4 jobs on wlan0" in result["message"], True)

    # The schedule is visible instead of configured: every radio sensor
    # carries the round and how many jobs share it, from the first scan.
    check("the round is a channel", channels[21]["value"], 240)
    check("in seconds", channels[21].get("display_unit"), "s")
    check("and so is the number of jobs on the radio",
          channels[22]["value"], 4)

    # The same age within what the helper allows is a result like any
    # other.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {}, "details": {"ssid": "Corporate"}, "age": 420.0,
        "round_time": 240.0, "stale_after": 480.0, "job_count": 4,
        "min_max_age": 480.0,
    }
    result = module.work(dict(arguments))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("an age inside the helper's bound is a result",
          channels[10]["value"], 1)
    check("and says nothing about the schedule",
          "round currently" in result["message"], False)

    # An explicit --max-age is still honoured as an upper bound, so
    # sensors that set one keep working.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {"total_ms": 3100, "assoc_ms": 900, "auth_ms": 300},
        "details": {"ssid": "Corporate"}, "age": 901.0,
        "round_time": 60.0, "stale_after": 1800.0, "job_count": 1,
        "min_max_age": 120.0,
    }
    result = module.work(dict(arguments, max_age=900))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("an explicit max-age still marks a result stale",
          channels[18]["value"], 12)
    check("and the message names the limit",
          "901 s old, limit 900 s" in result["message"], True)

    # A --max-age the radio cannot keep used to be failure code 13, and a
    # sensor turned red on its own parameter whenever a network was added
    # on the same probe. Now it is a warning in the message: the
    # measurement keeps its own code, and the bound the sensor judges by
    # is what the radio can keep.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {"total_ms": 3100, "assoc_ms": 900, "auth_ms": 300},
        "details": {"ssid": "Corporate"}, "age": 150.0,
        "round_time": 90.0, "stale_after": 600.0, "job_count": 2,
        "min_max_age": 180.0,
    }
    result = module.work(dict(arguments, max_age=60))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a max-age the radio cannot keep is no failure code",
          channels[18]["value"], 0)
    check("the measurement still counts", channels[10]["value"], 1)
    check("it is not stale against a bound the radio cannot keep",
          "limit" in result["message"], False)
    check("the message warns first and names what the radio can keep",
          result["message"].startswith(
              "--max-age 60 s cannot be kept, the radio needs 180 s for "
              "2 jobs on wlan0"), True)

    # The warning does not swallow the cause the radio saw.
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "dhcp-timeout",
        "message": "No DHCP offer arrived",
        "timings": {"total_ms": 45000, "assoc_ms": 900},
        "details": {"ssid": "Guest"}, "age": 20.0,
        "round_time": 90.0, "stale_after": 600.0, "job_count": 2,
        "min_max_age": 180.0,
    }
    result = module.work(dict(arguments, max_age=60))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("the warning keeps the radio's failure code",
          channels[18]["value"], 8)
    check("and its cause in the message",
          "dhcp-timeout" in result["message"], True)

    # Without --max-age there is nothing to warn about.
    result = module.work(dict(arguments))
    check("an automatic schedule carries no warning",
          "--max-age" in result["message"], False)

    # A helper from before #192 sends no stale bound and no round. The
    # sensor then judges by twice the old default and does not invent a
    # round of zero seconds.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {}, "details": {"ssid": "Corporate"}, "age": 700.0,
        "round_budget": 120, "min_max_age": 180, "job_count": 2,
    }
    result = module.work(dict(arguments))
    check("an old helper's result is stale past twice the old default",
          {entry["id"]: entry for entry in result["channels"]}[18]["value"],
          12)
    check("and the message names the limit, not a round",
          "700 s old, limit 600 s" in result["message"], True)

    # A hand-run diagnosis measures on the spot, so no schedule applies
    # and there is nothing to judge the setting against.
    result = module.work(dict(arguments, max_age=60, measure_now=True))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("--measure-now is not judged against a round",
          "--max-age" in result["message"], False)
    check("and still carries the schedule channels", 22 in channels, True)

    # Within the freshness the age is a reading like any other, and the
    # message says how old what it reports is.
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "Authentication succeeded",
        "timings": {"total_ms": 3100, "assoc_ms": 900, "auth_ms": 300},
        "details": {"ssid": "Corporate", "bssid": "00:00:5e:00:53:c2"},
        "age": 42.4,
    }
    result = module.work(dict(arguments))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a fresh result is a result", channels[10]["value"], 1)
    check("the age is reported in seconds", channels[17]["value"], 42)
    check("the message says how old the reading is",
          "; age 42 s" in result["message"], True)

    # The message is a line of short parts, network and verdict first, so
    # it stays readable in a list of sensors. The client address is in it
    # because it is what to look for in the gateway's user table - two
    # networks on one radio sign in under different addresses (ADR 0012).
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "OK",
        "timings": {"total_ms": 3100, "dhcp_ms": 700, "gateway_ms": 7},
        "details": {"ssid": "Guest", "bssid": "00:00:5e:00:53:c2",
                    "ip_address": "198.51.100.19",
                    "dhcp_server": "198.51.100.1", "gateway": "198.51.100.1",
                    "gateway_kind": "dhcp server",
                    "mac": "02:3f:a1:9c:44:e7", "mac_kind": "auto"},
        "age": 12.0,
    }
    message = module.work(dict(arguments))["message"]
    check("a successful run reads as short parts", message,
          "Guest: OK; client 02:3f:a1:9c:44:e7; AP 00:00:5e:00:53:c2; "
          "IP 198.51.100.19 from 198.51.100.1; ping 7 ms; age 12 s")
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "OK", "timings": {},
        "details": {"ssid": "Guest", "mac": "00:00:5e:00:53:01",
                    "mac_kind": "permanent"}, "age": 5.0,
    }
    check("the radio's own address says so",
          "client 00:00:5e:00:53:01 (permanent)"
          in module.work(dict(arguments, mac="permanent"))["message"], True)
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "dhcp-timeout", "message": "no DHCP answer",
        "timings": {}, "details": {"ssid": "Guest",
                                   "mac": "02:3f:a1:9c:44:e7",
                                   "mac_kind": "auto"}, "age": 20.0,
    }
    check("a failure names the client too",
          module.work(dict(arguments))["message"],
          "Guest: no DHCP answer; client 02:3f:a1:9c:44:e7; "
          "code dhcp-timeout; age 20 s")

    # A failed run points at its record in the sensor log, where the raw
    # supplicant and DHCP output is. The platform's address is not known on
    # the probe, so the path is what the message carries - the part after
    # the address the operator opens the platform with.
    run = "3f0c3d1e-58a4-4c39-9a3b-7e0f2b6f4d10"
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "dhcp-timeout", "message": "no DHCP answer",
        "timings": {}, "details": {"ssid": "Guest"}, "age": 20.0,
        "record_id": run,
    }
    check("a failed run names its record in the sensor log",
          module.work(dict(arguments))["message"],
          "Guest: no DHCP answer; code dhcp-timeout; age 20 s; "
          "log /sensor-logs/%s" % run)
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "auth-failed-eap",
        "message": "x" * 3000, "timings": {}, "details": {"ssid": "Guest"},
        "record_id": run,
    }
    message = module.work(dict(arguments))["message"]
    check_true("a long message stays within what PRTG shows",
               len(message) <= 2000)
    check_true("and still ends on the link",
               message.endswith("; log /sensor-logs/%s" % run))
    link = "https://nats.example.test:9443/sensor-logs/%s" % run
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "dhcp-timeout", "message": "no DHCP answer",
        "timings": {}, "details": {"ssid": "Guest"}, "age": 20.0,
        "record_id": run, "record_link": link,
    }
    check("with the platform's address it is a full link",
          module.work(dict(arguments))["message"],
          "Guest: no DHCP answer; code dhcp-timeout; age 20 s; log %s" % link)
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "auth-failed-eap",
        "message": "x" * 3000, "timings": {}, "details": {"ssid": "Guest"},
        "record_id": run, "record_link": link,
    }
    message = module.work(dict(arguments))["message"]
    check_true("a full link keeps the message within what PRTG shows",
               len(message) <= 2000)
    check_true("and is never the part that is cut",
               message.endswith("; log %s" % link))
    module.call_helper = lambda job, timeout: {
        "result": "failed", "code": "dhcp-timeout", "message": "no DHCP answer",
        "timings": {}, "details": {"ssid": "Guest"}, "age": 20.0,
        "record_id": run,
        "record_link": "https://elsewhere.example.test/sensor-logs/%s"
                       % "00000000-0000-4000-8000-000000000000",
    }
    check("a link to another run falls back to the path",
          module.work(dict(arguments))["message"],
          "Guest: no DHCP answer; code dhcp-timeout; age 20 s; "
          "log /sensor-logs/%s" % run)
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "message": "OK", "timings": {},
        "details": {"ssid": "Guest"}, "age": 5.0, "record_id": run,
    }
    check("a successful run does not", "/sensor-logs/" in module.work(
        dict(arguments))["message"], False)
    module.call_helper = lambda job, timeout: {
        "result": "blocked", "code": "iface-down",
        "message": "Could not power up wlan0", "timings": {}, "details": {},
        "record_id": run,
    }
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        try:
            module.work(dict(arguments))
        except SystemExit:
            pass
    check("a run that could not happen names its record too",
          json.loads(printed.getvalue())["message"],
          "Test could not run (iface-down): Could not power up wlan0; "
          "log /sensor-logs/%s" % run)
    asked = {}

    def remember_mac(job, timeout):
        asked.update(job)
        return {"result": "ok", "code": "ok", "message": "OK",
                "timings": {}, "details": {}, "age": 1.0}

    keep = module.call_helper
    module.call_helper = remember_mac
    module.work(dict(arguments))
    check("the per-network address is the default", asked.get("mac"), "auto")
    module.work(dict(arguments, mac="02:00:00:00:00:01"))
    check("a fixed address reaches the helper", asked.get("mac"),
          "02:00:00:00:00:01")
    module.call_helper = keep

    # What the script asks for decides whether the radio is used. PRTG
    # collects; only a hand-run diagnosis measures on the spot, and only
    # that one needs the patience of a whole test.
    asked = {}

    def remember(job, timeout):
        asked.clear()
        asked.update({"action": job.get("action"),
                      "max_age": job.get("max_age"), "timeout": timeout})
        return {"result": "ok", "code": "ok", "message": "ok",
                "timings": {}, "details": {}, "age": 1.0}

    module.call_helper = remember
    module.work(dict(arguments))
    check("the sensor collects instead of measuring", asked["action"],
          "collect")
    check("it leaves the freshness to the helper", asked["max_age"], None)
    check("and waits only as long as a memory read takes",
          asked["timeout"], module.COLLECT_TIMEOUT)
    module.work(dict(arguments, measure_now=True))
    check("--measure-now measures on the spot", asked["action"], "test")
    check("and then waits for a whole test", asked["timeout"], 65)

    # The helper refuses a lease it collected after the link changed. That
    # has to reach PRTG as its own cause, not as a nameless failure.
    module.call_helper = lambda job, timeout: {
        "result": "failed",
        "code": "assoc-lost",
        "message": ("The link changed while DHCP was running; the address "
                    "would belong to another association"),
        "timings": {"total_ms": 5000, "assoc_ms": 800},
        "details": {"ssid": "Guest"},
    }
    result = module.work(dict(arguments, ssid="Guest"))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a lease from a changed link is its own failure code",
          channels[18]["value"], 10)
    check("and never reports an address",
          "DHCP offered" in result["message"], False)


def check_wlan_auth_scheduler():
    """The radio is shared out by the helper, so the sharing is checked."""
    print("\n== wlan-auth: the scheduler on the radio ==")
    wrapper = os.path.join(
        SENSOR_DIR, "wlan-auth", "privileged", "prtg-sensor-wlan-auth")
    module = load_module("prtg_sensor_wlan_auth", wrapper)

    job = {"interface": "wlan0", "ssid": "Corporate", "auth": "peap",
           "identity": "monitor", "password": "secret", "stage": "dhcp",
           "timeout": 45}
    check("a changed password is a different job",
          module.job_identity(job)
          != module.job_identity(dict(job, password="other")), True)
    check("a changed SSID is a different job",
          module.job_identity(job)
          != module.job_identity(dict(job, ssid="Guest")), True)
    # Otherwise a sensor whose interval somebody edits would lose the
    # measurement it already had, and start again at "not measured yet".
    check("a changed freshness is the same job",
          module.job_identity(dict(job, max_age=60)),
          module.job_identity(dict(job, max_age=600)))
    # The label leaves the probe in every sensor log record. A hash over
    # the password would let anyone who reads the logs test guesses.
    check("the label a record carries ignores the credentials",
          module.job_label(job),
          module.job_label(dict(job, password="other", psk="other")))
    check("the label still tells two networks apart",
          module.job_label(job) != module.job_label(dict(job, ssid="Guest")),
          True)

    # The gateway is an address or the word that switches the step off. A
    # name would have to be resolved, and a sensor that cannot reach its
    # network cannot resolve anything - so it is refused as a parameter
    # error rather than failing later as a broken network.
    def refused(value):
        try:
            module.validate_job({"interface": "wlan0", "ssid": "Corporate",
                                 "auth": "psk", "psk": "passphrase123",
                                 "stage": "dhcp", "gateway": value})
        except module.Blocked as blocked:
            return blocked.code
        return "accepted"

    check("a host name is not a gateway", refused("gateway.example.test"),
          "bad-request")
    check("an address is", refused("198.51.100.1"), "accepted")
    check("and so is none", refused("none"), "accepted")
    check("empty means the lease decides", refused(""), "accepted")

    # One address per network, derived from the radio's own. Seen on three
    # sites: a gateway that carries the VLAN of a station's last session
    # over to its next one on another SSID, so a probe checking an internal
    # and a guest network with one address measured the guest network in
    # the internal VLAN (ADR 0012). The address has to stay the same per
    # network - one lease, one entry in the gateway - and differ between
    # networks and between radios.
    radio = "00:00:5e:00:53:01"
    guest = module.network_mac(radio, "Guest")
    check("the per-network address is stable",
          module.network_mac(radio, "Guest"), guest)
    check("it differs between networks",
          guest != module.network_mac(radio, "Corporate"), True)
    check("and between radios",
          guest != module.network_mac("00:00:5e:00:53:02", "Guest"), True)
    check("it is locally administered and unicast", guest[:3], "02:")
    check("and a well-formed address", bool(module.MAC_ADDRESS.match(guest)),
          True)

    def mac_for(choice, permanent=radio):
        job = module.validate_job({"interface": "wlan0", "ssid": "Guest",
                                   "auth": "psk", "psk": "passphrase123",
                                   "mac": choice})
        try:
            return module.client_mac(job, permanent)
        except module.Blocked as blocked:
            return blocked.code

    check("auto signs in under the network's address", mac_for("auto"),
          (guest, "auto"))
    check("and is what an empty parameter means", mac_for(""),
          (guest, "auto"))
    check("permanent keeps the radio's own", mac_for("permanent"),
          (radio, "permanent"))
    check("a fixed address is taken as given, in one spelling",
          mac_for("02-00-00-00-00-0A"), ("02:00:00:00:00:0a", "fixed"))
    # Without the radio's own address there is nothing to derive from, and
    # quietly signing in under whatever the interface carries would bring
    # back the carry-over unnoticed.
    check("an unreadable radio address stops the run, not the sensor",
          mac_for("auto", permanent=""), "mac-not-settable")

    def mac_refused(value):
        try:
            module.validate_job({"interface": "wlan0", "ssid": "Guest",
                                 "auth": "psk", "psk": "passphrase123",
                                 "mac": value})
        except module.Blocked as blocked:
            return blocked.code
        return "accepted"

    check("a malformed address is refused", mac_refused("02:00:00:00:00"),
          "bad-request")
    check("so is a multicast one", mac_refused("01:00:5e:00:00:01"),
          "bad-request")
    check("the address is part of the job",
          module.job_identity(dict(job, mac="auto"))
          != module.job_identity(dict(job, mac="permanent")), True)

    # Whom to ask whether the network carries traffic: the router the
    # lease names, and where there is none - udhcpc reports no router on
    # its output line - the server that handed the lease out.
    check("the router of the lease is the target",
          module.gateway_target({"new_routers": "198.51.100.1 198.51.100.2",
                                 "new_dhcp_server_identifier": "198.51.100.9"}),
          ("198.51.100.1", "router"))
    check("without one the server stands in",
          module.gateway_target(
              {"new_dhcp_server_identifier": "198.51.100.9"}),
          ("198.51.100.9", "dhcp server"))
    check("a lease that names neither has no target",
          module.gateway_target({"new_ip_address": "198.51.100.19"}),
          ("", ""))

    # Three missed requests are not a deleted sensor. The floor is an hour
    # because the scanning interval is the setting now: a job must survive
    # the gap to its second request, and the first gap after somebody
    # raised the interval, before the helper has seen either.
    check("a job outlives a few missed requests", module.job_ttl(1800), 5400)
    check("and never expires faster than the floor", module.job_ttl(30),
          float(module.JOB_TTL_MINIMUM))
    check("which covers a scan every half hour",
          module.JOB_TTL_MINIMUM >= 1800, True)

    scheduler = module.Scheduler()
    now = time.monotonic()

    def entry(every, measured, asked=0.0, max_age=None, runs=(),
              timeout=None):
        """A job PRTG asks for every `every` seconds, measured `measured`
        seconds ago - or never - whose last runs took `runs` seconds."""
        asks = [now - asked - every * step for step in range(3, -1, -1)]
        return {"job": job if timeout is None else dict(job, timeout=timeout),
                "max_age": max_age, "asked": now - asked, "asks": asks,
                "durations": list(runs),
                "measured": None if measured is None else now - measured,
                "result": None if measured is None else {"result": "ok"}}

    # How often PRTG asks is the one setting there is (#192). The helper
    # reads it off the requests instead of being told a second number
    # that has to match it.
    check("the request rate is read off the requests",
          module.asked_interval(entry(300, 10)), 300.0)
    check("a job asked once has no rate yet",
          module.asked_interval(dict(entry(300, 10), asks=[now])), None)
    # A scan that overran, or a probe that restarted, leaves one odd gap.
    # The median keeps it from setting the pace.
    uneven = dict(entry(300, 10), asks=[now - 1500, now - 900, now - 600,
                                        now - 300, now])
    check("one odd gap does not set the pace", module.asked_interval(uneven),
          300.0)
    # A raised interval is believed at its first gap, so the network is not
    # measured at the old rate for another quarter of an hour. Lowering it
    # waits for the median, which errs towards measuring less.
    raised = dict(entry(60, 10), asks=[now - 1080, now - 1020, now - 960,
                                       now - 900, now])
    check("a raised interval counts from its first gap",
          module.asked_interval(raised), 900.0)
    # Asked every 15 minutes, the job survives to its next request.
    slow = dict(entry(900, 10), asks=[now - 900])
    check("a sensor polled every 15 minutes is not dropped before its "
          "second request", module.ask_ttl(slow) > 900, True)

    scheduler._jobs = {"fresh": entry(300, 10), "new": entry(300, None)}
    check("a job that was never measured goes first",
          scheduler._due()["measured"], None)

    # A sensor polled every five minutes gets a measurement about every
    # five minutes, and leaves the airtime in between to the others.
    alone = {"only": entry(300, 0, runs=[12.0])}
    check("a job is measured as often as it is asked for",
          module.planned_interval(alone["only"], alone), 300.0)
    scheduler._jobs = {"fresh": entry(300, 200, runs=[12.0])}
    check("and not before its interval has passed", scheduler._due(), None)
    scheduler._jobs = {"due": entry(300, 301, runs=[12.0])}
    check("once it has, it is measured again",
          scheduler._due()["measured"] is not None, True)

    # Measuring each age against the job's own interval is what shares the
    # radio out evenly: the one furthest past its share goes next, not the
    # one that waited longest in seconds.
    scheduler._jobs = {"patient": entry(300, 100, runs=[12.0]),
                       "demanding": entry(60, 100, runs=[12.0])}
    check("the job furthest past its own interval goes next",
          module.asked_interval(scheduler._due()), 60.0)

    # The floor stays: an access point answered a radio that signed in
    # back to back with status 16 until nothing got through. However often
    # a sensor asks, its network is measured at most once a minute.
    check("one network is measured at most once a minute",
          module.REFRESH_FLOOR_SECONDS, 60.0)
    eager = {"only": entry(20, 0, runs=[12.0])}
    check("however often PRTG asks",
          module.planned_interval(eager["only"], eager), 60.0)
    check("the radio rests between two sign-ins",
          module.RADIO_PAUSE_SECONDS >= 10, True)

    # A round is what the jobs on this radio really take, plus the rest
    # after each. On paper a sign-in may use its whole timeout; on a probe
    # a good one takes 8 to 15 seconds, and planning with the worst case
    # had two networks cost 120 seconds a round.
    measured_pair = {"a": entry(300, 0, runs=[11.0, 14.0, 12.0]),
                     "b": entry(300, 0, runs=[9.0])}
    check("a round is the measured runs plus the rest after each",
          module.round_time(measured_pair), 14.0 + 10.0 + 9.0 + 10.0)
    # A failing network costs its full timeout and must stay measured, so
    # its long run counts at once rather than averaged away.
    check("the slowest recent run is what a job is expected to take",
          module.expected_duration(entry(300, 0, runs=[8.0, 45.0, 9.0])),
          45.0)
    # Nothing measured yet - a new job, or a helper that just restarted -
    # falls back to the worst case and converges within a round.
    check("an unmeasured job is planned with its worst case",
          module.expected_duration(entry(300, None, timeout=45)), 60.0)
    check("which is its timeout plus the wait for the radio",
          module.round_time({"a": entry(300, None, timeout=15),
                             "b": entry(300, None, timeout=15)}),
          (15 + 7.5 + 10) * 2)

    # Where the radio cannot serve every job at its interval, all of them
    # are overdue and are measured in turn - they age together.
    crowded = {str(i): entry(60, 100, runs=[45.0]) for i in range(4)}
    scheduler._jobs = crowded
    check("every job is due once the round outlasts the interval",
          scheduler._due() is not None, True)

    # --max-age stays an upper bound for the sensors that set it. The
    # renewal then starts a round early, so the result arrives in time.
    bounded = {"a": entry(600, 0, max_age=300, runs=[20.0]),
               "b": entry(600, 0, runs=[20.0])}
    check("an explicit max-age caps the interval, a round early",
          module.planned_interval(bounded["a"], bounded), 300.0 - 60.0)
    check("the others keep their own pace",
          module.planned_interval(bounded["b"], bounded), 600.0)

    # A result counts as stale relative to the round, not to a number
    # somebody typed: about two of what it should be.
    check("stale means twice the interval",
          module.stale_after(alone["only"], alone), 600.0)
    check("or twice the round, where the round is longer",
          module.stale_after(crowded["0"], crowded),
          2 * module.round_time(crowded))

    # What the radio can keep for a sensor that still sets --max-age: one
    # round of waiting on top of the once-a-minute floor, or of the round
    # itself where that is longer.
    pair = {"a": entry(300, 0, runs=[20.0]), "b": entry(300, 0, runs=[20.0])}
    check("the smallest max-age it can keep is the floor plus a round",
          module.min_max_age(pair), 60.0 + 60.0)
    check("or two rounds where a round outlasts the floor",
          module.min_max_age(crowded), 2 * module.round_time(crowded))

    # Every answer carries what the sensor needs to judge and show the
    # schedule, because only this side sees the other jobs.
    scheduler._jobs = dict(pair)
    answer = scheduler._answer(pair["a"], now)
    check("an answer carries the round", answer.get("round_time"), 60.0)
    check("how many jobs share it", answer.get("job_count"), 2)
    check("when its result counts as stale", answer.get("stale_after"), 600.0)
    check("and what an explicit max-age would have to be",
          answer.get("min_max_age"), 120.0)

    # The snapshot is read by whoever debugs on the probe. It must name
    # the jobs without handing out what they sign in with.
    scheduler._jobs = {"only": entry(300, 12, runs=[12.0])}
    snapshot = scheduler.snapshot()
    serialised = json.dumps(snapshot)
    check("the snapshot names the network", snapshot["jobs"][0]["ssid"],
          "Corporate")
    check("and reports the age it holds", snapshot["jobs"][0]["age"], 12.0)
    check("how often it is asked for", snapshot["jobs"][0]["asked_every"],
          300.0)
    check("how often it is measured", snapshot["jobs"][0]["interval"], 300.0)
    check("what a run is expected to take",
          snapshot["jobs"][0]["expected"], 12.0)
    check("and the round over the radio", snapshot["round_time"], 22.0)

    # Which network answered is what a probe with several SSIDs on one
    # radio is debugged with. The address alone does not say it: a lease
    # from the wrong VLAN reads like a healthy result, and only the DHCP
    # server behind it tells the two apart.
    scheduler._jobs = {"only": entry(300, 12)}
    scheduler._jobs["only"]["result"] = {
        "result": "ok", "code": "ok",
        "details": {"ip_address": "198.51.100.19",
                    "dhcp_server": "198.51.100.1"}}
    lease = scheduler.snapshot()["jobs"][0]
    check("the snapshot names the address it was given", lease["ip"],
          "198.51.100.19")
    check("and the server that handed it out", lease["dhcp"],
          "198.51.100.1")
    scheduler._jobs = {"only": entry(300, None)}
    check("a job without a measurement reports no address",
          scheduler.snapshot()["jobs"][0]["ip"], "")
    check("the password never leaves the service",
          "secret" in serialised, False)
    check("nor does the identity", "monitor" in serialised, False)

    # Registering through collect: the requests are what the rate is read
    # from, and a measurement is what the durations are read from.
    sharing = module.Scheduler()
    sharing.collect(dict(job), None)
    answer = sharing.collect(dict(job, ssid="Guest"), None)
    check("two jobs share the radio", answer["job_count"], 2)
    check("planned with their worst case until measured",
          answer["round_time"], (45 + 15 + 10) * 2.0)
    identity = module.job_identity(dict(job, ssid="Guest"))
    sharing._measure = lambda job, log: {"result": "ok", "code": "ok",
                                         "timings": {}, "details": {}}
    keep_report = module.report_run
    module.report_run = lambda job, result, log: result
    try:
        sharing.measure(sharing._jobs[identity])
    finally:
        module.report_run = keep_report
    check("a measurement records how long it took",
          len(sharing._jobs[identity]["durations"]), 1)
    answer = sharing.collect(dict(job, ssid="Guest"), None)
    check("and the round shrinks to what it really took",
          answer["round_time"] < (45 + 15 + 10) * 2.0, True)
    check("a request is remembered for the rate",
          len(sharing._jobs[identity]["asks"]), 2)

    scheduler._jobs = {"abandoned": entry(300, 4000, asked=4000)}
    check("a job nobody asks for any more is dropped", scheduler._due(), None)
    check("and is gone from the scheduler",
          "abandoned" in scheduler._jobs, False)

    # The deployment writes a different systemd unit for a helper that
    # keeps running, and it asks the helper which one. Both halves of that
    # contract are in this repository, so they are checked together.
    completed = subprocess.run([sys.executable, wrapper, "--unit-mode"],
                               capture_output=True, text=True, check=False)
    check("the helper names the unit shape it needs",
          completed.stdout.strip(), "daemon")
    rollout = open(os.path.join(PROJECT_DIR, "libexec",
                                "prtg-nats-probe-helper"),
                   encoding="utf-8").read()
    check("and the rollout asks for exactly that word",
          '"${mode}" == "daemon"' in rollout, True)


@contextlib.contextmanager
def return_channel_stub():
    """The real return channel service on a socket of its own, in a thread.

    The wlan-auth helper talks to it exactly as it would on a probe, so what
    the checks see is what the buffer would hold - nothing the helper sends
    gets past the service's own validation unnoticed.
    """
    # A short path: AF_UNIX names are limited to about a hundred bytes.
    directory = tempfile.mkdtemp(prefix="rc.", dir="/tmp")
    try:
        channel = load_return_channel(directory)
        buffer = channel.Buffer(os.path.join(directory, "buffer"),
                                limit=channel.MINIMUM_LIMIT)
        path = os.path.join(directory, "s")
        listener = channel.listening_socket(path)
        thread = threading.Thread(target=channel.serve,
                                  args=(listener, buffer), daemon=True)
        thread.start()
        # The serving thread is left to die with the checks: closing the
        # listener under its accept() would only print a traceback.
        yield path, (lambda: buffer.batch(50))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def wlan_auth_helper():
    wrapper = os.path.join(
        SENSOR_DIR, "wlan-auth", "privileged", "prtg-sensor-wlan-auth")
    return load_module("prtg_sensor_wlan_auth_log", wrapper)


def check_wlan_auth_sensor_log():
    """Every run leaves one record in the sensor log, and no secret in it."""
    print("\n== wlan-auth: sensor log records ==")
    module = wlan_auth_helper()
    job = module.validate_job({
        "interface": "wlan0", "ssid": "Example-Guest", "auth": "psk",
        "psk": "correct-horse-battery", "stage": "dhcp", "timeout": 45})

    def measured(result, supplicant=(), dhcp="", events=()):
        def fake(job, log):
            for line in events:
                log.event(line)
            log.supplicant([(log.started + 0.5, line) for line in supplicant])
            if dhcp:
                log.dhcp(dhcp)
            # A fresh answer per run, as perform_test builds one.
            return json.loads(json.dumps(result))
        return fake

    success = {
        "result": "ok", "code": "ok", "message": "OK",
        "timings": {"assoc_ms": 900, "auth_ms": 300, "dhcp_ms": 700,
                    "gateway_ms": 4, "total_ms": 2100},
        "details": {"mac": "00:00:5e:00:53:10", "mac_kind": "auto",
                    "bssid": "00:00:5e:00:53:c2", "ssid": "Example-Guest",
                    "frequency_mhz": 5180, "signal_dbm": -58,
                    "ip_address": "198.51.100.19",
                    "dhcp_server": "198.51.100.1",
                    "gateway": "198.51.100.1",
                    "gateway_kind": "dhcp server"},
    }
    lock_directory = tempfile.mkdtemp(prefix="wlan-auth.")
    module.RUN_DIR = lock_directory
    with return_channel_stub() as (path, stored):
        module.RETURN_CHANNEL_SOCKET = path
        module.perform_test = measured(
            success,
            supplicant=["wlan0: Associated with 00:00:5e:00:53:c2",
                        "wlan0: CTRL-EVENT-CONNECTED - Connection to "
                        "00:00:5e:00:53:c2 completed"],
            dhcp="udhcpc: started, v1.36.1\nudhcpc: lease of 198.51.100.19 "
                 "obtained from 198.51.100.1, lease time 28800\n",
            events=["dhcp started"])
        scheduler = module.Scheduler()
        scheduler.collect(dict(job), 300)
        entry = scheduler._due()
        scheduler.measure(entry)
        records = stored()
        check("a successful run leaves one record", len(records), 1)
        record = records[0] if records else {}
        body = record.get("body") or {}
        check("it is the wlan-auth sensor's", record.get("sensor"),
              "wlan-auth")
        check("and an ok run is an ok record", record.get("result"), "ok")
        check("the job is the label without credentials", record.get("job"),
              module.job_label(job))
        check("the result points the sensor at its record",
              entry["result"].get("record_id"), record.get("id"))
        check("without an interface address it carries no full link",
              "record_link" in entry["result"], False)

        # The helper has written the interface address into channel.env,
        # which only root may read: the daemon builds the link, so the
        # unprivileged sensor script needs no access to the file.
        config = os.path.join(lock_directory, "channel.env")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("PLATFORM_URL=https://nats.example.test:8443\n"
                         "WEB_URL=https://nats.example.test:9443\n")
        module.RETURN_CHANNEL_CONFIG = config
        scheduler.measure(entry)
        linked = stored()[-1]
        check("with one the result carries the full link to the run",
              entry["result"].get("record_link"),
              "https://nats.example.test:9443/sensor-logs/%s" % linked["id"])
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("WEB_URL=javascript:alert(1)\n")
        scheduler.measure(entry)
        check("an address that is no https URL gives no link",
              "record_link" in entry["result"], False)
        module.RETURN_CHANNEL_CONFIG = os.path.join(lock_directory, "absent")
        check("the body names network, method and interface",
              (body.get("ssid"), body.get("auth"), body.get("interface")),
              ("Example-Guest", "psk", "wlan0"))
        check("and what the run saw",
              (body.get("bssid"), body.get("client_mac"),
               body.get("ip_address"), body.get("dhcp_server"),
               body.get("gateway"), body.get("frequency_mhz"),
               body.get("signal_dbm")),
              ("00:00:5e:00:53:c2", "00:00:5e:00:53:10", "198.51.100.19",
               "198.51.100.1", "198.51.100.1", 5180, -58))
        check("with the stage timings", body.get("timings"),
              success["timings"])
        check("the supplicant's own lines are kept",
              [line.split(" ", 1)[1] for line in body.get("supplicant", [])],
              ["wlan0: Associated with 00:00:5e:00:53:c2",
               "wlan0: CTRL-EVENT-CONNECTED - Connection to "
               "00:00:5e:00:53:c2 completed"])
        check("and so are the DHCP client's",
              body.get("dhcp"),
              ["udhcpc: started, v1.36.1",
               "udhcpc: lease of 198.51.100.19 obtained from 198.51.100.1, "
               "lease time 28800"])
        check_true("and the helper's own events for the run",
                   any("dhcp started" in line
                       for line in body.get("events", [])))

        # A failed run carries the most raw output, and raw output is where
        # a secret turns up: a supplicant that echoes its configuration, a
        # derived key in a debug line. None of it may reach the platform,
        # in whatever form the helper itself put it into the configuration.
        psk = job["psk"]
        derived = module.wpa_psk(psk, job["ssid"])
        failure = {"result": "failed", "code": "auth-failed-psk",
                   "message": "PSK rejected", "timings": {"assoc_ms": 800},
                   "details": {"mac": "00:00:5e:00:53:10"}}
        module.perform_test = measured(failure, supplicant=[
            "wlan0: psk=\"%s\"" % psk,
            "wlan0: PMK %s" % derived.upper(),
            "wlan0: ssid/psk hex %s" % module.hex_value(psk),
            "wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid=\"Example-Guest\" "
            "auth_failures=1 duration=10 reason=WRONG_KEY",
        ], dhcp="", events=["supplicant started"])
        before = len(stored())
        scheduler.measure(entry)
        record = stored()[-1]
        serialised = json.dumps(record)
        check("a failed sign-in leaves a record too", len(stored()),
              before + 1)
        check("and it is an error", (record["result"], record["code"]),
              ("error", "auth-failed-psk"))
        check("the passphrase is not in it", psk in serialised, False)
        check("nor the key derived from it, in any case",
              derived in serialised.lower(), False)
        check("nor the hex form the configuration carries",
              module.hex_value(psk) in serialised, False)
        check_true("the line that named the cause survives",
                   any("WRONG_KEY" in line
                       for line in record["body"]["supplicant"]))

        enterprise = module.validate_job({
            "interface": "wlan0", "ssid": "Example-Corp", "auth": "eap-tls",
            "identity": "probe@example.test", "password": "peap-Secret-9",
            "client_cert": "/etc/prtg-nats/sensors/wlan-auth/probe.pem",
            "private_key": "/etc/prtg-nats/sensors/wlan-auth/probe.key",
            "private_key_passwd": "key-Pass-7", "stage": "assoc"})
        log = module.RunLog()
        log.supplicant([(log.started, line) for line in (
            "EAP: identity probe@example.test password peap-Secret-9",
            "TLS: private key passphrase key-Pass-7",
            "CTRL-EVENT-EAP-PEER-CERT depth=0 subject='/CN=radius.example.test'",
        )])
        record = module.build_record(enterprise, failure, log)
        record = json.dumps(module.redact_record(record, enterprise))
        check("the enterprise password is not in the record",
              "peap-Secret-9" in record, False)
        check("nor the private key passphrase", "key-Pass-7" in record,
              False)
        check("the identity stays - it is what the RADIUS log is searched by",
              "probe@example.test" in record, True)
        check("and so does the certificate subject",
              "/CN=radius.example.test" in record, True)

        # Blanking a two-letter secret would shred every line with those
        # letters and still hint at the secret, so such a line goes whole -
        # the rule aruba-uplink applies to its messages.
        short = dict(enterprise, password="ab")
        log = module.RunLog()
        log.supplicant([(log.started, "EAP: answer ab sent"),
                        (log.started, "EAP: method 25")])
        lines = module.redact_record(
            module.build_record(short, failure, log), short)["body"][
                "supplicant"]
        check("a line holding a secret too short to mask is dropped",
              any("ab sent" in line for line in lines), False)
        check("and the others stay readable",
              any("method 25" in line for line in lines), True)

        # A run that could not happen is a run too - the log is where an
        # operator finds out why the radio was never used.
        def blocked(job, log):
            raise module.Blocked("iface-down", "Could not power up wlan0")
        module.perform_test = blocked
        scheduler.measure(entry)
        record = stored()[-1]
        check("a blocked run is logged as an error with its code",
              (record["result"], record["code"]), ("error", "iface-down"))

        # Busy means somebody tests by hand: nothing was measured, so
        # nothing is logged and the stored result stays as it was.
        def busy(job, log):
            raise module.Blocked("iface-busy", "Another test is running")
        module.perform_test = busy
        before = len(stored())
        scheduler.measure(entry)
        check("a run that did not get the radio leaves no record",
              len(stored()), before)

        # Measuring on the spot is the other way a run happens, and the
        # one somebody debugging on the probe looks for in the log.
        module.perform_test = measured(dict(success))
        answer = module.handle_request(json.dumps(dict(
            job, action="test")))
        check("a test run by hand is logged as well", len(stored()),
              before + 1)
        check("and its answer names the record",
              answer.get("record_id"), stored()[-1]["id"])

        # A supplicant stuck in a scan loop writes thousands of lines. The
        # service refuses a line above 64 KB, so the raw output is what
        # gives way - the oldest lines stay, they hold the start of the
        # trouble, and the newest, which hold its end.
        noise = ["wlan0: CTRL-EVENT-SCAN-RESULTS %04d %s" % (number, "x" * 200)
                 for number in range(3000)]
        module.perform_test = measured(dict(failure), supplicant=noise,
                                       dhcp="\n".join(noise[:500]))
        before = len(stored())
        scheduler.measure(entry)
        check("a run with endless output is still logged", len(stored()),
              before + 1)
        lines = stored()[-1]["body"]["supplicant"]
        check_true("as a record the service takes",
                   len(json.dumps(stored()[-1])) < 64 * 1024)
        check_true("keeping the first lines",
                   " 0000 " in lines[0])
        check_true("and the last", " 2999 " in lines[-1])
        check_true("and saying how many are left out",
                   any("lines left out" in line for line in lines))

    # Without the service the sensor works as it always did: the result is
    # stored, PRTG gets it, and only the link to a record is missing.
    module.perform_test = measured(dict(success))
    module.RETURN_CHANNEL_SOCKET = os.path.join(lock_directory, "absent")
    alone = module.Scheduler()
    alone.collect(dict(job), 300)
    entry = alone._due()
    alone.measure(entry)
    check("without the service the result is still stored",
          entry["result"].get("result"), "ok")
    check("and points at no record", "record_id" in entry["result"], False)

    # A service that accepts and never answers must not hold the radio
    # for longer than the short handover timeout.
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    module.RETURN_CHANNEL_SOCKET = os.path.join(lock_directory, "silent")
    silent.bind(module.RETURN_CHANNEL_SOCKET)
    silent.listen(1)
    module.RETURN_CHANNEL_TIMEOUT = 0.3
    started = time.monotonic()
    alone.measure(entry)
    check("a service that hangs costs the record, not the measurement",
          (entry["result"].get("result"), "record_id" in entry["result"]),
          ("ok", False))
    check_true("and only the short timeout",
               time.monotonic() - started < 2)
    silent.close()
    shutil.rmtree(lock_directory, ignore_errors=True)


def check_wlan_auth_configuration():
    """The wpa_supplicant configuration must not be subvertible."""
    print("\n== wlan-auth: configuration from foreign strings ==")
    wrapper = os.path.join(
        SENSOR_DIR, "wlan-auth", "privileged", "prtg-sensor-wlan-auth")
    module = load_module("prtg_sensor_wlan_auth", wrapper)

    # Test vector from IEEE 802.11i: the same value wpa_passphrase produces.
    check("the PSK derivation matches the standard",
          module.wpa_psk("password", "IEEE"),
          "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e")

    hostile = 'Guest"\nnetwork={\n  ssid="Attack'
    lines = module.build_network_block({
        "ssid": hostile, "auth": "psk", "psk": "passphrase123",
    })
    ssid_lines = [line for line in lines if line.strip().startswith("ssid=")]
    check("the SSID appears exactly once", len(ssid_lines), 1)
    check("the SSID is written as a hex string",
          ssid_lines[0].strip(), "ssid=%s" % hostile.encode("utf-8").hex())
    check("no quotation mark from foreign text",
          any('"' in line for line in ssid_lines), False)
    check("only one network block is produced",
          len([line for line in lines if line.startswith("network={")]), 1)
    check("the passphrase is not in the configuration in clear text",
          any("passphrase123" in line for line in lines), False)

    # An access point that turns a station away says why. Carrying the
    # number through is the difference between "rejected" and a cause
    # somebody can act on.
    check("the status code of a rejection reaches the message",
          module.with_status_code(
              "The access point rejected the association",
              "CTRL-EVENT-ASSOC-REJECT bssid=00:00:5e:00:53:c2 status_code=17"),
          "The access point rejected the association (status 17)")
    check("an event without one is left alone",
          module.with_status_code("Rejected", "CTRL-EVENT-ASSOC-REJECT"),
          "Rejected")

    # Two sensors share one radio. A lease collected after the link changed
    # belongs to the other run, and the fingerprint is what tells them apart.
    class FakeRun:
        def __init__(self, *answers):
            self.answers = list(answers)

        def control(self, *_arguments):
            return self.answers.pop(0)

    same = FakeRun("bssid=00:00:5e:00:53:c2\nwpa_state=COMPLETED\n",
                   "bssid=00:00:5e:00:53:c2\nwpa_state=COMPLETED\n")
    check("the same association yields the same fingerprint",
          module.link_fingerprint(same), module.link_fingerprint(same))
    moved = FakeRun("bssid=00:00:5e:00:53:c2\nwpa_state=COMPLETED\n",
                    "bssid=00:00:5e:00:53:c0\nwpa_state=COMPLETED\n")
    check("another cell yields another one",
          module.link_fingerprint(moved) != module.link_fingerprint(moved),
          True)
    gone = FakeRun("wpa_state=SCANNING\n")
    check("a link that no longer stands is not complete",
          module.link_fingerprint(gone)[1], "SCANNING")

    # Waiting for the radio is bounded by the run's own budget, not by a
    # number that happens to suit one timeout. Two sensors no longer meet
    # here - the scheduler measures for both - but a test somebody runs by
    # hand on the probe still meets the scheduler, and that overlap is
    # worth waiting out instead of reporting iface-busy.
    check("a short run waits only a short while",
          module.lock_budget(10), 5.0)
    check("a long one stops at the ceiling",
          module.lock_budget(300), float(module.LOCK_WAIT_CEILING))
    check("waiting never eats the whole budget",
          all(module.lock_budget(t) < t for t in (10, 45, 120, 300)), True)
    # The script gives the helper "--timeout plus 20 seconds" on the
    # socket, and the measurement itself needs all of --timeout. A longer
    # wait would time the script out while the helper still works.
    check("and never outlasts the script's patience",
          all(module.lock_budget(t) + t <= t + 20 - 5
              for t in (10, 45, 120, 300)), True)

    for description, job, expected in (
        ("a too-short passphrase", {"ssid": "A", "auth": "psk", "psk": "shrt"},
         "bad-request"),
        ("PEAP without a password",
         {"ssid": "A", "auth": "peap", "identity": "u", "verify_server": False},
         "bad-request"),
        ("enterprise without an identity",
         {"ssid": "A", "auth": "peap", "verify_server": False},
         "bad-request"),
        ("server verification without a CA",
         {"ssid": "A", "auth": "peap", "identity": "u", "password": "p"},
         "bad-request"),
        ("EAP-TLS without a certificate",
         {"ssid": "A", "auth": "eap-tls", "identity": "u",
          "verify_server": False},
         "bad-request"),
        ("an invalid BSSID",
         {"ssid": "A", "auth": "psk", "psk": "passphrase123",
          "bssid": "not-a-mac"},
         "bad-request"),
    ):
        try:
            module.build_network_block(job)
            code = "no failure"
        except module.Blocked as blocked:
            code = blocked.code
        check("%s is rejected" % description, code, expected)

    print("\n== wlan-auth: protection of the management path ==")
    check("an interface without radio is refused",
          blocked_code(module, "lo"), True)

    completed = subprocess.run(
        [sys.executable, wrapper], input="{}", capture_output=True, text=True,
        timeout=60)
    check("the helper always ends with exit code 0", completed.returncode, 0)
    answer = json.loads(completed.stdout)
    check("an incomplete task never leads to a test",
          answer.get("result"), "blocked")


def blocked_code(module, interface):
    """Report whether check_interface refuses the interface."""
    try:
        module.check_interface(interface)
        return False
    except module.Blocked:
        return True
    except Exception:
        # On systems without /sys (macOS, say) the path check already
        # trips.
        return True


def speedtest_module_present():
    """Is speedtest-cli present in the system Python?

    On the probe the sensor runs in its own virtual environment, here with
    the system Python. Both cases are admissible; the self-test just has
    to report the right thing in each.
    """
    try:
        import speedtest  # noqa: F401
    except ImportError:
        return False
    return True


def check_internet_speed_output():
    """Output format and load protection of the throughput sensor."""
    print("\n== internet-speed: output for PRTG ==")
    script = os.path.join(SENSOR_DIR, "internet-speed", "script",
                          "internet-speed.py")
    module = load_module("internet_speed", script)

    completed = run_script(script, "--self-check\n")
    check("the self-test yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("the answer carries the schema version", document.get("version"), 2)
    if speedtest_module_present():
        check("a present measurement tool passes the self-test",
              document.get("status"), "ok")
    else:
        check("a missing measurement tool reports a sensor error",
              document.get("status"), "error")
        check("the message names the cause",
              "module-missing" in document.get("message", ""), True)

    completed = run_script(script, "--totally-unknown\n")
    check("an unknown parameter yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("an unknown parameter reports a sensor failure",
          document.get("status"), "error")
    # The parameters are typed into a text field in PRTG and checked by
    # nothing there. The first sensor run is the only place a typo can
    # show up - so the message has to name it too.
    check("the message names the typo",
          "--totally-unknown" in document.get("message", ""), True)

    # argparse writes its help as plain text to stdout. In a terminal that
    # is right; through PRTG it would be no valid answer, and a parse
    # error would appear instead of the parameters.
    completed = run_script(script, "--help\n")
    check("--help yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("--help answers in JSON, not argparse text",
          document.get("version"), 2)
    for parameter in ("--mode", "--server", "--source",
                      "--no-secure", "--measure-every-minutes"):
        check("--help names %s" % parameter,
              parameter in document.get("message", ""), True)
    check("--help names the documentation",
          "README.md" in document.get("message", ""), True)
    # The message is cut off at 2000 characters; a help that ends there
    # conceals exactly the parameters at the end.
    check("--help fits into a sensor message",
          len(document.get("message", "")) < 2000, True)

    completed = run_script(script, "\n")
    document = json.loads(completed.stdout)
    check("a run without parameters is rejected",
          document.get("status"), "error")
    check("the missing setting is in the message",
          "--mode" in document.get("message", ""), True)
    check("and so is the way to the full list",
          "--help" in document.get("message", ""), True)

    directory = tempfile.mkdtemp()
    try:
        # In operation the cache lives in /tmp. For the check it is
        # redirected so nothing is left on the development machine. Each
        # block gets its own path: otherwise the load protection would
        # already count the previous check's result as measured.
        module.CACHE_PATH = os.path.join(directory, "measurement.json")
        check_internet_speed_measurement(module)
        module.CACHE_PATH = os.path.join(directory, "cache.json")
        check_internet_speed_cache(module)
    finally:
        shutil.rmtree(directory, ignore_errors=True)

    check_internet_speed_validation(module)
    check_internet_speed_server_choice(module)


def internet_speed_arguments(**overrides):
    arguments = {
        "self_check": False, "mode": "maximum", "min_download_mbit": None,
        "min_upload_mbit": None, "measure_every_minutes": 60,
        "timeout_seconds": 120, "server": None, "source": None,
        "no_secure": False,
    }
    arguments.update(overrides)
    return arguments


MEASUREMENT = {
    "code": "ok",
    "download_kbit": 142300,
    "upload_kbit": 38100,
    "ping_ms": 14,
    "jitter_ms": 3,
    "server_id": 12345,
    "duration_ms": 28400,
    "server": "Example Ltd (Frankfurt)",
}


def check_internet_speed_measurement(module):
    original_measure = module.measure
    calls = []

    def fake_measure(arguments):
        calls.append(arguments)
        return dict(MEASUREMENT, measured_at=time.time())

    module.measure = fake_measure

    result = module.work(internet_speed_arguments())
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a successful measurement is a valid result",
          result["status"], "ok")
    check("the success channel says yes", channels[10]["value"], module.LOOKUP_YES)
    check("the success channel uses a standard lookup",
          channels[10]["lookup_name"],
          "prtg.standardlookups.yesno.stateyesok")
    check("the download is reported in kbit/s", channels[11]["value"], 142300)
    check("the upload is reported in kbit/s", channels[12]["value"], 38100)
    check("the jitter has its own channel", channels[14]["value"], 3)
    check("on the first run the server counts as unchanged",
          channels[15]["value"], 1)
    # A server change is context, not a failure. The alarm lookup binds
    # "No" to Error - the sensor would go red because speedtest.net
    # offered a different server. The neutral lookup knows both as Ok.
    check("the server change hangs on the neutral lookup",
          channels[15]["lookup_name"], module.NEUTRAL_LOOKUP)
    check("and not on the alarm lookup",
          channels[15]["lookup_name"] == module.ALARM_LOOKUP, False)
    check("a fresh result has age 0", channels[16]["value"], 0)
    check("the failure code is 0", channels[18]["value"], 0)
    check("mode maximum has no channel for the minimum rate",
          20 in channels, False)
    check("channels are sorted ascending",
          [entry["id"] for entry in result["channels"]],
          sorted(entry["id"] for entry in result["channels"]))
    check("the message names the test server",
          "Example Ltd (Frankfurt)" in result["message"], True)
    # Whoever wants to compare readings over weeks pins the server. The
    # ID for that should be readable without signing in to the probe.
    check("the message names the server ID",
          "server 12345" in result["message"], True)

    # A failure remains a valid measurement: the sensor stays "ok", the
    # alarm hangs on the channel. Otherwise the measurement channels'
    # history would break off.
    def failing_measure(arguments):
        raise module.Failed("download-failed",
                            "The download measurement did not finish.")

    module.measure = failing_measure
    result = module.work(internet_speed_arguments(measure_every_minutes=0))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("an aborted measurement remains a valid result",
          result["status"], "ok")
    # prtg.standardlookups.yesno.stateyesok does not know a 0 - PRTG
    # showed "undefined lookup value" and turned it into a mere warning
    # instead of an error. Seen on a real probe.
    check("the success channel says no", channels[10]["value"], module.LOOKUP_NO)
    check("the failure cause is machine-readable", channels[18]["value"], 4)

    # speedtest-cli can get stuck in its worker threads. Without the
    # alarm signal the main thread then waits in join() forever, PRTG runs
    # into the sensor timeout and gets no output at all. Reproduced on a
    # probe: the run was still standing after six minutes.
    def hanging_measurement(*ignored):
        raise module.Timeout()

    # measure() only arms the alarm signal after it found the
    # measurement tool. Without speedtest-cli in the system Python - the
    # normal case here - "module-missing" would come out before the alarm
    # is armed.
    module.load_speedtest = lambda: object()
    module.run_measurement = hanging_measurement
    module.measure = original_measure
    code = "no failure"
    try:
        module.measure(internet_speed_arguments(timeout_seconds=30))
    except module.Failed as failure:
        code = failure.code
    except module.Timeout:
        code = "passed through instead of translated"
    check("a hang becomes a timeout", code, "timeout")

    # A missing measurement tool, by contrast, says nothing about the
    # line and does not belong in the measurement channels.
    def missing_measure(arguments):
        raise module.Failed("module-missing", module.MODULE_MISSING_MESSAGE)

    module.measure = missing_measure
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            module.work(internet_speed_arguments(measure_every_minutes=0))
        ended = False
    except SystemExit:
        ended = True
    check("a missing measurement tool is a sensor error", ended, True)
    check("and valid Script v2 JSON is produced",
          json.loads(output.getvalue()).get("status"), "error")


def check_internet_speed_cache(module):
    """The minimum interval is the protection against a saturated line."""
    print("\n== internet-speed: load protection ==")
    calls = []

    def fake_measure(arguments):
        calls.append(arguments)
        return dict(MEASUREMENT, measured_at=time.time())

    module.measure = fake_measure

    module.work(internet_speed_arguments())
    check("the first run measures", len(calls), 1)

    result = module.work(internet_speed_arguments())
    check("within the minimum interval no new measurement runs",
          len(calls), 1)
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("PRTG still gets values", channels[11]["value"], 142300)

    module.work(internet_speed_arguments(measure_every_minutes=0))
    check("without a minimum interval every run measures", len(calls), 2)

    # A planted age shows that the cache decides by time, not by run.
    age_cache(module, internet_speed_arguments(), 1800)
    result = module.work(internet_speed_arguments())
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a half-hour-old result still counts", len(calls), 2)
    check("its age is readable in the channel", channels[16]["value"] >= 1795, True)

    age_cache(module, internet_speed_arguments(), 7200)
    module.work(internet_speed_arguments())
    check("after the minimum interval a new measurement runs", len(calls), 3)

    # While a fleet is being migrated, results from both states sit on a
    # probe for a while. Without the mode in the name a sensor would be
    # slipped the result of a sensor that no longer exists in that form.
    check("the mode is part of the storage name",
          "maximum" in module.cache_path(internet_speed_arguments()), True)

    # The cache lives in /tmp. If it is too open, another user could
    # plant readings on the sensor.
    path = module.cache_path(internet_speed_arguments())
    os.chmod(path, 0o644)
    check("a too-open cache is discarded",
          module.read_cache(path, None), None)


def age_cache(module, arguments, seconds):
    """Artificially age the stored result."""
    path = module.cache_path(arguments)
    with open(path, encoding="utf-8") as handle:
        stored = json.load(handle)
    stored["measured_at"] = time.time() - seconds
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle)


def check_internet_speed_validation(module):
    """The parameter validation is operations' only place to learn.

    Nobody checks the entries in PRTG; a mistake only shows up at run
    time. Every message therefore has to contain the line that has to
    stand there instead.
    """
    print("\n== internet-speed: parameter validation ==")

    def problem(**overrides):
        arguments = internet_speed_arguments(**overrides)
        try:
            module.validate(arguments)
        except module.ConfigError as failure:
            return str(failure)
        return ""

    message = problem(mode=None)
    check("without a mode it is rejected", bool(message), True)
    # --mode stays required although only one value is left: a sensor
    # without parameters would otherwise flood a site line every hour.
    check("the message shows the copyable line",
          "--mode maximum" in message, True)

    # Mode minimum has moved out. An existing sensor should learn where
    # to - not just that it is gone.
    message = problem(mode="minimum")
    check("mode minimum is rejected", bool(message), True)
    check("and the message names the successor",
          "iperf-throughput" in message, True)

    message = problem(mode="maximum", min_download_mbit=30)
    check("a target rate is rejected as well", bool(message), True)
    check("the successor is named there too",
          "iperf-throughput" in message, True)

    message = problem(mode="maximum", server="abc")
    check("an unreadable server ID is a configuration error",
          "speedtest-cli --list" in message, True)

    check("a negative minimum interval is rejected",
          bool(problem(mode="maximum", measure_every_minutes=-5)), True)
    check("valid parameters produce no message",
          problem(mode="maximum"), "")

    # argparse knows exactly what was mistyped; without passing it on,
    # PRTG would only see "Check configured parameters". Only the value
    # of --source must not go out: it carries an internal address.
    tokens = ["--source", "10.1.2.3", "--mode", "min"]
    redacted = module.redact("unrecognized arguments: 10.1.2.3 min", tokens)
    check("the source address is removed from the message",
          "10.1.2.3" not in redacted, True)
    check("the typo itself stays readable", "min" in redacted, True)


def check_internet_speed_server_choice(module):
    """Three things go wrong in server selection - with three ways out.

    If the sensor lumps them together, it claims a cause it never
    checked. Seen on a probe: it reported "no longer in the list" about a
    server the same probe had listed a minute earlier.
    """
    print("\n== internet-speed: server selection ==")

    class FakeSpeedtest:
        """Just as much speedtest-cli as select_server touches."""

        class NoMatchedServers(Exception):
            pass

        class ServersRetrievalError(Exception):
            pass

        class SpeedtestBestServerFailure(Exception):
            pass

        def __init__(self, failure, stage, only_secure=None):
            self.failure = failure
            self.stage = stage
            # None = protocol irrelevant, else only the other one fails.
            self.only_secure = only_secure
            self.secure = None
            self.servers = {}

        def Speedtest(self, secure=True, **ignored):
            self.secure = secure
            return self

        def get_servers(self, chosen):
            if self.only_secure is not None:
                if self.secure != self.only_secure:
                    raise self.failure()
                self.servers = {0.0: [{"id": "13764", "d": 0.0,
                                       "url": "http://example/speedtest/u.php",
                                       "sponsor": "Example", "name": "Town"}]}
                return
            if self.stage == "list":
                raise self.failure()

        def get_best_server(self):
            if self.stage == "latency":
                raise self.failure()

    def message(failure, stage, server="13764"):
        fake = FakeSpeedtest(failure, stage)
        try:
            module.select_server(fake, internet_speed_arguments(server=server),
                                 5)
        except module.Failed as failed:
            return failed.message
        return ""

    text = message(FakeSpeedtest.NoMatchedServers, "list")
    check("a missing ID is named as such",
          "not in the server list" in text, True)

    text = message(FakeSpeedtest.ServersRetrievalError, "list")
    check("an unreachable server list likewise",
          "Could not retrieve the list" in text, True)

    text = message(FakeSpeedtest.SpeedtestBestServerFailure, "latency")
    check("a failed latency probe does not become a missing ID",
          "not in the server list" in text, False)
    check("it names the latency probe instead",
          "latency probe" in text, True)
    # Otherwise somebody hunts the fault in their own uplink instead of
    # the test server.
    check("and clarifies it is no verdict on the line",
          "not a verdict on the line" in text, True)

    text = message(FakeSpeedtest.SpeedtestBestServerFailure, "latency",
                   server=None)
    check("without a fixed server the message stays general",
          "No test server answered" in text, True)

    # Over HTTP and HTTPS speedtest.net serves two different directories
    # of ten servers each, without a single shared ID. Whoever reads an
    # ID off "speedtest-cli --list" is looking at the HTTP directory -
    # the sensor asks over HTTPS by default. Reproduced on a probe:
    # "--server 13764" worked, "--server 13764 --secure" reported No
    # matched servers.
    fake = FakeSpeedtest(FakeSpeedtest.NoMatchedServers, "list",
                         only_secure=False)
    module.select_server(fake, internet_speed_arguments(server="13764"), 5)
    check("an ID from the other directory is still found",
          fake.secure, False)

    # And when it is in neither of the two any more: the set of ten is
    # exchanged completely within minutes, measured. Without memory,
    # --server would be useless.
    directory = tempfile.mkdtemp()
    try:
        cache = module.CACHE_PATH
        module.CACHE_PATH = os.path.join(directory, "memory.json")
        arguments = internet_speed_arguments(server="13764")
        module.write_cache(module.cache_path(arguments), {
            "measured_at": time.time(), "code": "ok", "server_id": 13764,
            "server_url": "http://example/speedtest/upload.php",
            "server": "Example Ltd (Town)",
        })
        fake = FakeSpeedtest(FakeSpeedtest.NoMatchedServers, "list")
        module.select_server(fake, arguments, 5)
        entry = list(fake.servers.values())[0][0]
        check("a remembered address rescues the pinned server",
              entry["url"], "http://example/speedtest/upload.php")

        check("without memory the error message stands",
              message(FakeSpeedtest.NoMatchedServers, "list", server="99999"),
              module.server_list_message(FakeSpeedtest,
                                         FakeSpeedtest.NoMatchedServers(),
                                         [99999]))
    finally:
        module.CACHE_PATH = cache
        shutil.rmtree(directory, ignore_errors=True)


def link_quality_arguments(**overrides):
    arguments = {
        "self_check": False, "target": [], "internal_target": [],
        "packets": 20, "interval_ms": 200, "timeout_ms": 1000,
        "payload_bytes": 56, "source": None,
    }
    arguments.update(overrides)
    return arguments


# An invented helper answer: one impeccable and one dead target.
# Exactly this mixture is the interesting case - it separates
# reachability from the quality of the path that is still there.
LINK_RESULTS = [
    {"name": "Good", "mode": "icmp", "scope": "external", "sent": 20,
     "received": 20, "reachable": True, "code": "ok", "loss_percent": 0,
     "rtt_avg_ms": 12.5, "rtt_min_ms": 11.0, "rtt_max_ms": 14.0,
     "jitter_ms": 1.5},
    {"name": "Dead", "mode": "icmp", "scope": "external", "sent": 20,
     "received": 0, "reachable": False, "code": "no-reply",
     "loss_percent": 100},
]


# Two reports the way iperf3 really delivers them - captured on a probe
# against an own endpoint and trimmed to what is needed.
IPERF_TCP_REPORT = {
    "end": {
        "sum_sent": {"bits_per_second": 30090000.0, "retransmits": 3},
        "sum_received": {"bits_per_second": 29990000.0},
        "streams": [{"sender": {"mean_rtt": 32300}}],
    }
}
IPERF_UDP_REPORT = {
    "end": {
        "sum": {"bits_per_second": 30000000.0, "jitter_ms": 0.339,
                "lost_percent": 0.8, "lost_packets": 207, "packets": 25901},
    }
}


def iperf_arguments(**overrides):
    arguments = {
        "self_check": False, "server": "endpoint.example", "port": 5201,
        "download_mbit": 30, "upload_mbit": 10, "udp": False,
        "username": None, "password": None, "public_key": None,
        "seconds": 5, "measure_every_minutes": 60, "timeout_seconds": None,
        "profile": "default",
    }
    arguments.update(overrides)
    return arguments


def config_error(module, **overrides):
    """Fetch the text of a configuration error, or None."""
    try:
        module.validate(iperf_arguments(**overrides))
    except module.ConfigError as problem:
        return str(problem)
    return None


def check_iperf_throughput_output():
    """Output format, validation and credentials of the iperf sensor."""
    print("\n== iperf-throughput: output for PRTG ==")
    script = os.path.join(SENSOR_DIR, "iperf-throughput", "script",
                          "iperf-throughput.py")
    module = load_module("iperf_throughput", script)

    completed = run_script(script, "--self-check\n")
    check("the self-test yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("the answer carries the schema version", document.get("version"), 2)
    if os.path.exists(module.IPERF):
        check("a present iperf3 passes the self-test",
              document.get("status"), "ok")
    else:
        check("a missing iperf3 reports a sensor error",
              document.get("status"), "error")
        check("the message names the cause",
              "tool-missing" in document.get("message", ""), True)

    original_iperf = module.IPERF
    original_config_root = module.CONFIG_ROOT
    with tempfile.TemporaryDirectory() as tool_directory:
        candidate = os.path.join(tool_directory, "iperf3")
        module.CONFIG_ROOT = tool_directory

        def managed_tool_self_check(version, authentication=True,
                                    executable=True, source="managed"):
            features = "authentication" if authentication else "sctp"
            tool_env = os.path.join(tool_directory, "tool.env")
            if source == "system":
                with open(tool_env, "w", encoding="utf-8") as handle:
                    handle.write("SOURCE=system\n")
            elif os.path.exists(tool_env):
                os.unlink(tool_env)
            with open(candidate, "w", encoding="utf-8") as handle:
                handle.write(
                    "#!/bin/sh\n"
                    "printf '%s\\n' 'iperf %s' "
                    "'Optional features available: %s'\n"
                    % ("%s", version, features)
                )
            os.chmod(candidate, 0o755 if executable else 0o644)
            module.IPERF = candidate
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    result = module.self_check(iperf_arguments(self_check=True))
            except SystemExit:
                result = json.loads(output.getvalue())
            return result

        document = managed_tool_self_check("3.21")
        check("the approved authenticated iperf3 passes the self-test",
              document.get("status"), "ok")
        document = managed_tool_self_check("3.20")
        check("the wrong managed iperf3 version fails the self-test",
              document.get("status"), "error")
        document = managed_tool_self_check("3.21", authentication=False)
        check("an iperf3 without authentication fails the self-test",
              document.get("status"), "error")
        document = managed_tool_self_check("3.21", executable=False)
        check("a non-executable managed iperf3 fails the self-test",
              document.get("status"), "error")
        document = managed_tool_self_check("3.18", source="system")
        check("the Raspberry Pi OS iperf3 fallback passes the self-test",
              document.get("status"), "ok")
        document = managed_tool_self_check("3.17", source="system")
        check("a system iperf3 below 3.18 fails the self-test",
              document.get("status"), "error")
    module.IPERF = original_iperf
    module.CONFIG_ROOT = original_config_root

    completed = run_script(script, "--totally-unknown\n")
    document = json.loads(completed.stdout)
    check("an unknown parameter reports a sensor error",
          document.get("status"), "error")
    check("the message names the typo",
          "--totally-unknown" in document.get("message", ""), True)

    # argparse writes its help as plain text to stdout. In a terminal
    # that is right; through PRTG it would be no valid answer, and a parse
    # error would appear instead of the parameters. But whoever types
    # "--help" into the parameter field wants exactly this list - it is
    # the only place to find it without access to the probe.
    completed = run_script(script, "--help\n")
    check("--help yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("--help answers in JSON, not argparse text",
          document.get("version"), 2)
    for parameter in ("--server", "--download-mbit", "--udp", "--username",
                      "--password", "--measure-every-minutes"):
        check("--help names %s" % parameter,
              parameter in document.get("message", ""), True)
    check("--help names the documentation",
          "README.md" in document.get("message", ""), True)
    # The message is cut off at 2000 characters; a help that ends there
    # conceals exactly the parameters at the end.
    check("--help fits into a sensor message",
          len(document.get("message", "")) < 2000, True)

    completed = run_script(script, "\n")
    document = json.loads(completed.stdout)
    check("a run without parameters is rejected",
          document.get("status"), "error")
    check("and points at the parameter list",
          "--help" in document.get("message", ""), True)

    check_iperf_throughput_validation(module)
    check_iperf_throughput_credentials(module)
    check_iperf_throughput_endpoint_from_profile(module)
    check_iperf_throughput_parsing(module)
    check_iperf_throughput_channels(module)


def check_iperf_throughput_validation(module):
    print("\n== iperf-throughput: parameters ==")

    check("nothing works without an endpoint",
          "--server" in (config_error(module, server=None) or ""), True)
    # Without a target rate the sensor measures what the path carries.
    # That is a second mode of operation, not a failure.
    check("without a target rate the capacity is measured",
          config_error(module, download_mbit=None, upload_mbit=None), None)
    # With UDP that would not work: iperf3 then sends at its default of
    # one megabit per second and dutifully reported "no loss".
    check("--udp without a target rate is rejected",
          "--udp" in (config_error(module, udp=True, download_mbit=None,
                                   upload_mbit=None) or ""), True)
    # The most common mix-up: the channels report kbit/s, the parameter
    # expects Mbit/s.
    check("a rate in kbit/s is caught",
          "not plausible" in (config_error(module, download_mbit=30000) or ""),
          True)
    # A password without a user name would silently find no use, and the
    # sensor would keep measuring unauthenticated - nobody notices that.
    check("a password without a user name is rejected",
          "--username" in (config_error(module, password="secret") or ""), True)
    check("a negative minimum interval is rejected",
          config_error(module, measure_every_minutes=-1) is not None, True)
    check("the usual configuration is valid",
          config_error(module), None)

    check("without any rate both directions are measured",
          module.directions_of(iperf_arguments(download_mbit=None,
                                                upload_mbit=None)),
          {"download": None, "upload": None})
    check("a target rate selects its direction",
          module.directions_of(iperf_arguments(upload_mbit=None)),
          {"download": 30 * 1000 * 1000})
    # Without a target rate --bitrate is dropped; iperf3 then measures
    # with TCP what the path yields.
    # Five seconds are measured, not adopted: checked pairwise against
    # ten, the short measurement read higher and scattered six times less.
    check("five seconds are measured per direction", module.HOLD_SECONDS, 5)
    # The default is measured, but not set in stone: whoever suspects a
    # line of throttling only after seconds needs longer runs.
    command = module.build_command(iperf_arguments(seconds=30), "download",
                                   None, None)
    check("the duration is adjustable",
          command[command.index("--time") + 1], "30")
    check("too short is rejected",
          "--seconds" in (config_error(module, seconds=1) or ""), True)
    check("too long as well",
          "--seconds" in (config_error(module, seconds=99) or ""), True)
    # Otherwise the run would abort in its own alarm signal, and the
    # message would speak of a timeout instead of two parameters that do
    # not fit together.
    check("a too-tight time budget is caught up front",
          "--timeout-seconds" in (config_error(module, seconds=30,
                                               timeout_seconds=40) or ""), True)
    check("and otherwise it grows along on its own",
          module.time_budget(iperf_arguments(seconds=30)) >= 75, True)
    check("and the duration is in the iperf3 invocation",
          module.build_command(iperf_arguments(), "download", 30000000,
                               None)[module.build_command(
                                   iperf_arguments(), "download", 30000000,
                                   None).index("--time") + 1],
          str(module.HOLD_SECONDS))
    check("no --bitrate in the invocation without a target rate",
          "--bitrate" in module.build_command(iperf_arguments(), "download",
                                              None, None), False)
    check("with a target rate there is",
          "--bitrate" in module.build_command(iperf_arguments(), "download",
                                              30000000, None), True)

    # Public one-off endpoints briefly stop listening after a test while their
    # service manager starts the next server process. The sensor runs download
    # and upload as separate clients, so the second one needs to leave that
    # restart window first.
    original_iperf = module.IPERF
    original_run_direction = module.run_direction
    original_sleep = module.time.sleep
    endpoint_ready = {"value": True}
    events = []

    def one_off_direction(_args, direction, _rate, _credentials, _seconds_left):
        if not endpoint_ready["value"]:
            raise module.Failed("server-unreachable", "Connection refused")
        events.append(("direction", direction))
        endpoint_ready["value"] = False
        return {"bit_s": 10000000, "grade": module.TARGET_OK}

    def restart_endpoint(seconds):
        events.append(("pause", seconds))
        endpoint_ready["value"] = True

    failure_code = None
    single_direction_events = []
    try:
        module.IPERF = __file__
        module.run_direction = one_off_direction
        module.time.sleep = restart_endpoint
        try:
            module.measure(iperf_arguments(download_mbit=None,
                                           upload_mbit=None))
        except module.Failed as problem:
            failure_code = problem.code
        two_direction_events = list(events)

        endpoint_ready["value"] = True
        events.clear()
        module.measure(iperf_arguments(upload_mbit=None))
        single_direction_events = list(events)
    finally:
        module.IPERF = original_iperf
        module.run_direction = original_run_direction
        module.time.sleep = original_sleep

    check("two directions survive a one-off endpoint restart",
          failure_code, None)
    check("the endpoint gets two seconds between directions",
          two_direction_events,
          [("direction", "download"), ("pause", 2),
           ("direction", "upload")])
    check("one direction does not wait", single_direction_events,
          [("direction", "download")])

    # argparse quotes faulty input verbatim, and that is intended.
    # Password and endpoint name still must not appear in it.
    tokens = ["--server", "endpoint.internal", "--password", "very-secret"]
    redacted = module.redact("bad value: very-secret at endpoint.internal",
                             tokens)
    check("the password is removed from messages",
          "very-secret" not in redacted, True)
    check("the endpoint name as well",
          "endpoint.internal" not in redacted, True)


def check_iperf_throughput_endpoint_from_profile(module):
    """A variant names its endpoint, so PRTG does not have to.

    This is what makes a second measurement endpoint a second sensor with one
    parameter instead of four. The rule is the one that already governs the
    password: what is given directly wins.
    """
    print("\n== iperf-throughput: endpoint from the profile ==")

    directory = tempfile.mkdtemp()
    try:
        profile = os.path.join(directory, "berlin.env")
        with open(profile, "w", encoding="utf-8") as handle:
            handle.write("IPERF3_HOST=iperf.berlin.example\n"
                         "IPERF3_PORT=5301\n"
                         "IPERF3_USERNAME=probe-berlin\n"
                         "IPERF3_PASSWORD=secret\n")
        os.chmod(profile, 0o640)
        module.PROFILE_DIR = directory

        merged = module.merge_profile(
            iperf_arguments(profile="berlin", server=None, port=None,
                            username=None))
        check("the endpoint comes from the variant",
              (merged["server"], merged["port"], merged["username"]),
              ("iperf.berlin.example", 5301, "probe-berlin"))

        merged = module.merge_profile(
            iperf_arguments(profile="berlin", server="other.example",
                            port=9999, username="someone"))
        check("what is given directly still wins",
              (merged["server"], merged["port"], merged["username"]),
              ("other.example", 9999, "someone"))

        # Without a variant the port has to land somewhere, and 5201 is where
        # it landed before the profile could contribute one.
        merged = module.merge_profile(
            iperf_arguments(profile="absent", server="x.example", port=None))
        check("without a variant the port falls back to the default",
              merged["port"], 5201)

        with open(profile, "w", encoding="utf-8") as handle:
            handle.write("IPERF3_HOST=iperf.berlin.example\n"
                         "IPERF3_PORT=not-a-number\n")
        os.chmod(profile, 0o640)
        merged = module.merge_profile(
            iperf_arguments(profile="berlin", server=None, port=None))
        check("an unusable port in the variant does not break the run",
              merged["port"], 5201)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def check_iperf_throughput_credentials(module):
    """The three ways to the password - and the one that is refused."""
    print("\n== iperf-throughput: credentials ==")

    directory = tempfile.mkdtemp()
    try:
        key_path = os.path.join(directory, "public.pem")
        with open(key_path, "w", encoding="utf-8") as handle:
            handle.write("-----BEGIN PUBLIC KEY-----\n")
        profile = os.path.join(directory, "default.env")
        with open(profile, "w", encoding="utf-8") as handle:
            handle.write("# deposited by deployment\n"
                         "IPERF3_PASSWORD=from-the-profile\n")
        os.chmod(profile, 0o640)
        module.PROFILE_DIR = directory

        check("without a user name no authentication happens",
              module.credentials_for(iperf_arguments()), None)

        found = module.credentials_for(
            iperf_arguments(username="probe", public_key=key_path))
        check("with a user name the password comes from the profile",
              found and found["password"], "from-the-profile")

        found = module.credentials_for(
            iperf_arguments(username="probe", password="from-the-sensor",
                             public_key=key_path))
        check("a supplied password takes precedence",
              found and found["password"], "from-the-sensor")

        # A file everyone may read is not a secret. The sensor should not
        # silently accept that.
        os.chmod(profile, 0o644)
        check("a world-readable profile is discarded",
              credentials_failure(module, username="probe",
                                  public_key=key_path),
              "credentials-unreadable")
        os.chmod(profile, 0o640)

        check("a missing public key is named",
              credentials_failure(module, username="probe",
                                  public_key=os.path.join(directory, "gone.pem")),
              "credentials-unreadable")

        # The key travels in the same envelope: otherwise one manual step
        # would remain after deployment, and that step gets forgotten.
        import base64
        with open(profile, "w", encoding="utf-8") as handle:
            handle.write("IPERF3_PASSWORD=from-the-profile\n")
            handle.write("IPERF3_PUBLIC_KEY_B64=%s\n" % base64.b64encode(
                b"-----BEGIN PUBLIC KEY-----\nabc\n").decode())
        os.chmod(profile, 0o640)
        cache_file = os.path.join(directory, "cache.json")
        module.CACHE_PATH = cache_file
        found = module.credentials_for(iperf_arguments(username="probe"))
        check("the key comes from the profile",
              found and os.path.isfile(found["public_key"]), True)
        check("and is readable only for the own user",
              oct(os.stat(found["public_key"]).st_mode & 0o777), "0o600")

        with open(profile, "w", encoding="utf-8") as handle:
            handle.write("IPERF3_PASSWORD=x\nIPERF3_PUBLIC_KEY_B64=nopem\n")
        os.chmod(profile, 0o640)
        check("an unusable key is named",
              credentials_failure(module, username="probe"),
              "credentials-unreadable")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def credentials_failure(module, **overrides):
    try:
        module.credentials_for(iperf_arguments(**overrides))
    except module.Failed as problem:
        return problem.code
    return "no failure"


def check_iperf_throughput_parsing(module):
    """Pull the values that belong in channels from the iperf3 report."""
    print("\n== iperf-throughput: evaluation ==")

    target = 30 * 1000 * 1000
    tcp = module.summarise(IPERF_TCP_REPORT, False, target)
    # The receiver's view is taken: what arrived is the line's
    # performance. The sender's view also contains what the kernel merely
    # accepted.
    check("TCP takes the receiver's view", tcp["bit_s"], 29990000)
    check("TCP counts the retransmits", tcp["retransmits"], 3)
    check("TCP converts the round-trip time to milliseconds", tcp["rtt_ms"], 32.3)
    check("29.99 of 30 Mbit/s count as held", tcp["grade"], module.TARGET_OK)
    # Without a target rate there is nothing to hold, and an invented
    # finding would be worse than none.
    check("without a target rate there is no finding",
          module.summarise(IPERF_TCP_REPORT, False, None).get("grade"), None)
    check("the measured rate is still there",
          module.summarise(IPERF_TCP_REPORT, False, None)["bit_s"], 29990000)

    def tcp_grade(bit_s):
        return module.summarise({"end": {"sum_received": {"bits_per_second": bit_s},
                                         "sum_sent": {}}}, False, target)["grade"]

    # Up to five percent short is held, beyond half short is an error, and
    # everything in between only warns.
    check("28.5 of 30 Mbit/s are still held", tcp_grade(28.5e6), module.TARGET_OK)
    check("28.4 of 30 Mbit/s warn", tcp_grade(28.4e6), module.TARGET_WARNING)
    check("15 of 30 Mbit/s still only warn", tcp_grade(15e6),
          module.TARGET_WARNING)
    check("14.9 of 30 Mbit/s are an error", tcp_grade(14.9e6),
          module.TARGET_ERROR)

    udp = module.summarise(IPERF_UDP_REPORT, True, target)
    check("UDP reports the jitter", udp["jitter_ms"], 0.339)
    check("UDP reports the loss", udp["loss_percent"], 0.8)
    # With UDP the loss decides, not the rate: the target rate is sent
    # regardless of whether the line carries it.
    check("0.8 % loss is still within tolerance", udp["grade"], module.TARGET_OK)

    def udp_grade(loss):
        return module.summarise({"end": {"sum": {"bits_per_second": 3e7,
                                                 "lost_percent": loss}}},
                                True, target)["grade"]

    check("5 % loss warns", udp_grade(5.0), module.TARGET_WARNING)
    check("50 % loss still only warns", udp_grade(50.0), module.TARGET_WARNING)
    check("60 % loss is an error", udp_grade(60.0), module.TARGET_ERROR)

    for text, code in (("test authorization failed", "auth-failed"),
                       ("the server is busy running a test", "busy"),
                       ("unable to connect to server", "server-unreachable"),
                       ("Connection refused", "server-unreachable"),
                       ("something else entirely", "test-failed")):
        check("\"%s\" becomes %s" % (text[:28], code),
              module.classify(text), code)

    command = module.build_command(iperf_arguments(udp=True), "download",
                                   target, None)
    check("download lets the far end send", "--reverse" in command, True)
    check("UDP is invoked as such", "--udp" in command, True)
    check("no user name without credentials",
          "--username" in command, False)
    command = module.build_command(iperf_arguments(), "upload", target,
                                   {"username": "probe", "password": "x",
                                    "public_key": "/k.pem"})
    check("upload sends itself", "--reverse" in command, False)
    check("with credentials the key travels along",
          "/k.pem" in command, True)
    # The password goes through the environment, never the command line:
    # otherwise it would sit in every user's process list on the probe.
    check("the password is not in the invocation", "x" in command, False)


def check_iperf_throughput_channels(module):
    """What PRTG gets to see in the end."""
    print("\n== iperf-throughput: channels ==")

    measurement = {
        "code": "ok", "protocol": "udp", "endpoint": "endpoint.example:5201",
        "download_kbit": 30000, "download_target_kbit": 30000,
        "download_grade": module.TARGET_OK, "download_loss_percent": 0.12,
        "download_jitter_ms": 0.34,
        "upload_kbit": 9350, "upload_target_kbit": 10000,
        "upload_grade": module.TARGET_WARNING,
        "upload_loss_percent": 6.5, "upload_jitter_ms": 2.1,
        "target_grade": module.TARGET_WARNING, "duration_ms": 21000,
    }
    result = module.present(measurement, 0, iperf_arguments())
    by_id = {entry["id"]: entry for entry in result["channels"]}

    check("the channel list is sorted ascending",
          [entry["id"] for entry in result["channels"]],
          sorted(by_id))
    check("a missed target remains a successful measurement",
          by_id[10]["value"], module.LOOKUP_YES)
    check("and only shows in its own channel", by_id[20]["value"],
          module.TARGET_WARNING)
    # The yes/no lookup has no warning, so Target Met needs one that does.
    check("Target Met uses a lookup with a warning state",
          by_id[20]["lookup_name"], module.TARGET_LOOKUP)

    # A result cached before the update carries yes/no. It is still served
    # for up to an hour and must not lose its alarm in the meantime.
    legacy = dict(measurement, download_met=1, upload_met=0, target_met=0)
    for key in ("download_grade", "upload_grade", "target_grade"):
        del legacy[key]
    legacy_ids = {entry["id"]: entry["value"]
                  for entry in module.present(legacy, 0,
                                              iperf_arguments())["channels"]}
    check("a cached yes/no miss is still an error", legacy_ids[20],
          module.TARGET_ERROR)
    check("the failure code stays at ok", by_id[18]["value"], 0)
    # Without decimal places, half a percent of packet loss would vanish
    # in rounding - and that is exactly where a line starts becoming
    # unusable.
    check("packet loss has decimal places", by_id[22]["value"], 6.5)
    check("and is declared a percent value", by_id[22]["kind"], "percent")
    check("the message names the loss of the missed direction",
          "6.50 %" in result["message"], True)
    check("retransmits are absent from a UDP measurement", 23 in by_id, False)

    without_target = module.present(
        {"code": "ok", "protocol": "tcp", "endpoint": "endpoint:5201",
         "download_kbit": 755406, "upload_kbit": 97546, "rtt_ms": 33.2,
         "download_retransmits": 54, "duration_ms": 20445},
        0, iperf_arguments(download_mbit=None, upload_mbit=None))
    present_ids = {entry["id"] for entry in without_target["channels"]}
    # A "yes" without a target carries no statement - the channel is
    # omitted.
    check("without a target rate the Target Met channel is absent",
          20 in present_ids, False)
    check("the throughput channels are still there",
          {11, 12} <= present_ids, True)
    check("and the message names the measured rate",
          "Measured 755.4 Mbit/s down" in without_target["message"], True)

    failed = module.present({"code": "server-unreachable",
                             "message": "nope"}, 0, iperf_arguments())
    codes = {entry["id"]: entry["value"] for entry in failed["channels"]}
    check("an unreachable endpoint sets Test Result to error",
          codes[10], module.LOOKUP_NO)
    check("and enters its code", codes[18],
          module.FAILURE_CODES["server-unreachable"])


def check_link_quality_output():
    """Output format, target parser and channel order of the quality sensor."""
    print("\n== link-quality: output for PRTG ==")
    script = os.path.join(SENSOR_DIR, "link-quality", "script",
                          "link-quality.py")
    module = load_module("link_quality", script)

    completed = run_script(script, "\n")
    check("a run without a target yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("the answer carries the schema version", document.get("version"), 2)
    check("without a target it is rejected", document.get("status"), "error")
    check("and a copyable line is in the message",
          "--target 1.1.1.1" in document.get("message", ""), True)

    completed = run_script(script, "--totally-unknown\n")
    check("an unknown parameter yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("an unknown parameter reports a sensor failure",
          document.get("status"), "error")
    # The parameters are typed into a text field in PRTG and checked by
    # nothing there. The first sensor run is the only place a typo can
    # show up - so the message has to name it too.
    check("the message names the typo",
          "--totally-unknown" in document.get("message", ""), True)

    # Without an installed helper - the normal case on a development
    # machine - the self-test has to fail cleanly instead of crashing.
    completed = run_script(script, "--self-check\n")
    check("the self-test without a helper yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("a missing helper reports a sensor error",
          document.get("status"), "error")
    check("the message names the socket",
          module.HELPER_SOCKET in document.get("message", ""), True)

    check_link_quality_targets(module)
    check_link_quality_channels(module)
    check_link_quality_quality(module)


def check_link_quality_targets(module):
    """The target parser is where a configuration arrives."""
    print("\n== link-quality: target specs ==")

    for spec, expected in (
        ("1.1.1.1", ("1.1.1.1", "1.1.1.1", "icmp", 0)),
        ("Google=8.8.8.8", ("Google", "8.8.8.8", "icmp", 0)),
        ("tcp:example.com:443", ("example.com:443", "example.com", "tcp", 443)),
        ("Web=tcp:[2001:db8::1]:443", ("Web", "2001:db8::1", "tcp", 443)),
        # A bare IPv6 address consists almost entirely of colons.
        # Without brackets none of them may be read as a port.
        ("[2001:db8::1]", ("2001:db8::1", "2001:db8::1", "icmp", 0)),
        ("icmp:fritz.box", ("fritz.box", "fritz.box", "icmp", 0)),
    ):
        target = module.parse_target(spec, "external")
        check("%s is split correctly" % spec,
              (target["name"], target["host"], target["mode"], target["port"]),
              expected)

    for spec, needle in (
        ("tcp:host", "needs a port"),
        ("1.1.1.1:80", "ICMP has no ports"),
        ("[2001:db8::1", "never closed"),
        ("host:abc", "not a number"),
        ("host:0", "not a number"),
        ("", "no host part"),
    ):
        try:
            module.parse_target(spec, "external")
            message = "no failure"
        except module.ConfigError as problem:
            message = str(problem)
        check("%r is rejected" % spec, needle in message, True)

    def problem(**overrides):
        try:
            module.validate(link_quality_arguments(**overrides))
        except module.ConfigError as failure:
            return str(failure)
        return ""

    check("valid parameters produce no message",
          problem(target=["1.1.1.1"]), "")
    # The cap on targets times packets is the protection against a sensor
    # becoming a load tool. It has to trip before the first packet.
    check("too many packets per run are rejected",
          "packets per run" in problem(
              target=["a=1.1.1.1", "b=8.8.8.8", "c=9.9.9.9", "d=1.0.0.1",
                      "e=8.8.4.4"],
              packets=100), True)
    check("the same number of targets with fewer packets stays allowed",
          problem(target=["a=1.1.1.1", "b=8.8.8.8", "c=9.9.9.9", "d=1.0.0.1",
                          "e=8.8.4.4"], packets=20), "")
    check("a host name as source address is rejected",
          "literal" in problem(target=["1.1.1.1"], source="probe.example"),
          True)
    check("an address as source address stays allowed",
          problem(target=["1.1.1.1"], source="192.0.2.10"), "")
    check("too many external targets are rejected",
          "at most" in problem(target=["1.1.1.%d" % n
                                       for n in range(1, 9)]).lower(), True)
    # The source address carries an internal address and therefore must
    # not stand in a message to PRTG - the typo itself may.
    tokens = ["--source", "10.1.2.3", "--packets", "abc"]
    redacted = module.redact("invalid int value: '10.1.2.3' abc", tokens)
    check("the source address is removed from the message",
          "10.1.2.3" not in redacted, True)
    check("the typo itself stays readable", "abc" in redacted, True)


def check_link_quality_channels(module):
    """The channel structure hangs on the configuration, never the result."""
    print("\n== link-quality: channels ==")
    module.call_helper = lambda job, timeout: {
        "result": "ok", "code": "ok", "duration_ms": 4200,
        "targets": LINK_RESULTS,
    }
    result = module.work(link_quality_arguments(
        target=["Good=1.1.1.1", "Dead=192.0.2.1"],
        internal_target=["File=10.0.0.10"]))
    channels = {entry["id"]: entry for entry in result["channels"]}

    check("a successful measurement is a valid result",
          result["status"], "ok")
    check("channels are sorted ascending",
          [entry["id"] for entry in result["channels"]],
          sorted(entry["id"] for entry in result["channels"]))
    check("the success channel uses the alarm lookup",
          channels[10]["lookup_name"], module.ALARM_LOOKUP)
    check("the number of reachable targets is in a channel",
          channels[11]["value"], 1)
    # A dead target is a statement about reachability. The alarm hangs
    # here, not on the sensor status.
    check("a dead target hits the reachability channel",
          channels[20]["value"], module.LOOKUP_NO)

    # Round-trip times need decimal places: an internal target answers in
    # fractions of a millisecond and would sit at a permanent 0 as an
    # integer.
    check("the round-trip time is a float channel", channels[13]["type"],
          "float")
    check("the loss is reported as a percent channel", channels[12]["kind"],
          "percent")

    # Separate ranges per class: an additional external target must not
    # shift the internal ones' channels, or their history tears apart.
    check("external targets start at 30", channels[30]["name"], "Good Loss")
    check("internal targets start at 50", channels[50]["name"], "File Loss")
    check("the internal target keeps its channels without a result",
          (channels[50]["value"], channels[51]["value"]), (100.0, 0.0))
    check("an internal target gets its own reachability channel",
          channels[22]["value"], module.LOOKUP_NO)

    # The most important point: a dead target belongs in reachability,
    # not in quality. Otherwise a target that drops ICMP on principle
    # would pull the index to a permanent 0 - and a channel that always
    # shows 0 says nothing.
    # Twenty packets arrived, twenty were lost to the dead target, and
    # the internal target sent none for lack of a result: half.
    check("the total loss counts across all targets",
          channels[12]["value"], 50.0)
    check("the quality index rates only the answering path",
          channels[19]["value"] > 80, True)
    check("the message names the failed target",
          "Dead (no-reply)" in result["message"], True)

    # A failure remains a valid measurement: the sensor stays "ok", the
    # alarm hangs on the channel. Otherwise the measurement channels'
    # history would break off.
    module.call_helper = lambda job, timeout: {
        "result": "blocked", "code": "busy",
        "message": "Another measurement is already running", "targets": [],
    }
    result = module.work(link_quality_arguments(target=["Good=1.1.1.1"]))
    channels = {entry["id"]: entry for entry in result["channels"]}
    check("a busy helper remains a valid result",
          result["status"], "ok")
    check("the success channel then says no", channels[10]["value"],
          module.LOOKUP_NO)
    check("the cause is machine-readable", channels[18]["value"],
          module.FAILURE_CODES["busy"])
    # Even without a single measurement the target channels have to stand.
    check("the target channels survive a failure",
          channels[30]["value"], 100.0)

    # A misconfigured sensor, by contrast, has no measurement: that
    # becomes a sensor error, not a row of zeros.
    module.call_helper = lambda job, timeout: {
        "result": "blocked", "code": "no-privileges",
        "message": "This helper must run as root", "targets": [],
    }
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            module.work(link_quality_arguments(target=["1.1.1.1"]))
        ended = False
    except SystemExit:
        ended = True
    check("a helper without privileges is a sensor error", ended, True)
    check("and valid Script v2 JSON is produced",
          json.loads(output.getvalue()).get("status"), "error")


def check_link_quality_quality(module):
    """The quality index and the separation of measurement kinds."""
    print("\n== link-quality: quality index ==")

    check("a short, quiet path counts as good",
          module.quality_index(10, 1, 0) >= 90, True)
    check("loss strikes through clearly",
          module.quality_index(10, 1, 10) < 70, True)
    check("high round-trip time as well",
          module.quality_index(300, 5, 0) < 80, True)
    # The R factor of the E-model never reaches 100 by construction: its
    # optimum is 93.2, and even the shortest path costs a remainder.
    # Whoever sets a threshold at 95 therefore alarms permanently - that
    # belongs in the README and in a check, so nobody changes it quietly.
    check("the index stays within its bounds",
          (module.quality_index(5000, 500, 100),
           module.quality_index(0, 0, 0)), (0, 93))

    # A TCP handshake contains the far end's reaction and sits
    # systematically above an echo. Averaging both would produce a number
    # no path ever had.
    mixed = [
        {"name": "Echo", "mode": "icmp", "scope": "external", "sent": 10,
         "received": 10, "reachable": True, "loss_percent": 0,
         "rtt_avg_ms": 10.0, "jitter_ms": 1.0},
        {"name": "Handshake", "mode": "tcp", "scope": "external", "sent": 10,
         "received": 10, "reachable": True, "loss_percent": 0,
         "rtt_avg_ms": 90.0, "jitter_ms": 1.0},
    ]
    summary = module.aggregate(mixed)
    check("the round-trip time does not average across both kinds",
          summary["latency_ms"], 10.0)

    # Without an echo target the TCP targets take their place, or the
    # channel would stay permanently empty on a pure TCP configuration.
    summary = module.aggregate([mixed[1]])
    check("without an echo target the TCP targets count", summary["latency_ms"], 90.0)

    summary = module.aggregate(LINK_RESULTS)
    check("not all targets reachable is detected",
          summary["all_reachable"], False)
    check("the worst loss stands separately",
          summary["worst_loss_percent"], 100.0)


def check_link_quality_helper():
    """The privileged part: packet building, attribution and limits."""
    print("\n== link-quality: privileged helper ==")
    wrapper = os.path.join(SENSOR_DIR, "link-quality", "privileged",
                           "prtg-sensor-link-quality")
    module = load_module("prtg_sensor_link_quality", wrapper)

    # The classic property of an internet checksum: computed over the
    # finished packet it comes out zero. If that falls over, not a single
    # echo ever comes back.
    packet = module.build_echo(socket.AF_INET, 0x1234, 7,
                               module.build_payload(56))
    check("the finished packet's checksum is zero",
          module.checksum(packet), 0)
    check("the packet carries id and sequence number",
          struct.unpack("!BBHHH", packet[:8])[3:], (0x1234, 7))
    # With ICMPv6 the kernel sets the checksum itself - it needs the
    # pseudo-header with the addresses, which this program does not
    # know.
    packet6 = module.build_echo(socket.AF_INET6, 0x1234, 7, b"x" * 16)
    check("with ICMPv6 the field is left to the kernel",
          struct.unpack("!BBHHH", packet6[:8])[2], 0)

    identifier = 0x4321
    header = bytes([0x45]) + b"\x00" * 19

    reply = header + struct.pack("!BBHHH", 0, 0, 0, identifier, 9) + module.MAGIC
    check("an own answer is attributed",
          module.parse_reply(socket.AF_INET, reply, identifier), (9, "echo"))
    check("a foreign id is discarded",
          module.parse_reply(socket.AF_INET, reply, 0x1111), None)
    # A raw socket sees every ICMP answer of the system, including that
    # of a concurrently running ping(8). Without the marker in the
    # payload it would flow into the measurement.
    foreign = header + struct.pack("!BBHHH", 0, 0, 0, identifier, 9) + b"alien"
    check("an answer without the marker is discarded",
          module.parse_reply(socket.AF_INET, foreign, identifier), None)

    # An error message carries the start of the packet that triggered
    # it. That yields "refused" instead of just "silent" - the difference
    # between a firewall and a failed target.
    inner = header + struct.pack("!BBHHH", 8, 0, 0, identifier, 11)
    error = header + struct.pack("!BBHI", 3, 1, 0, 0) + inner
    check("an error message is attributed to its packet",
          module.parse_reply(socket.AF_INET, error, identifier),
          (11, "unreachable"))

    def blocked(**overrides):
        job = {"targets": [{"host": "127.0.0.1"}]}
        job.update(overrides)
        try:
            module.validate_job(job)
        except module.Blocked as problem:
            return problem.message
        return ""

    check("a valid task gets through", blocked(), "")
    check("too many packets are rejected",
          "packet count" in blocked(packets=1000), True)
    check("a too-tight interval is rejected",
          "interval" in blocked(interval_ms=1), True)
    check("too many targets are rejected",
          "targets are allowed" in blocked(
              targets=[{"host": "127.0.0.1"}] * 20), True)
    check("the product of targets and packets is capped",
          "must not exceed" in blocked(
              targets=[{"host": "127.0.0.1"}] * 5, packets=100), True)
    check("a task without a target is rejected",
          "at least one target" in blocked(targets=[]).lower(), True)
    check("a host name as source address is rejected",
          "literal" in blocked(source="probe.example"), True)

    # An echo to a multicast address makes many devices answer at once.
    # That is pointless as a measurement and dangerous as amplification.
    for address in ("224.0.0.1", "255.255.255.255"):
        try:
            module.check_address(socket.AF_INET, address)
            code = "no failure"
        except module.Unusable as problem:
            code = problem.code
        check("%s is rejected as a target" % address, code, "bad-address")

    # An unresolvable name must not discard the whole run: a typo in one
    # of five targets would otherwise take the other four's measurement
    # with it.
    target = module.parse_target({"host": "does.not.exist.invalid"}, 0, None)
    check("an unresolvable target stays a single target",
          target["unusable"], "resolve-failed")
    outcome = module.summarize(target, [], 0, target["unusable"])
    check("it reports full loss instead of going missing",
          (outcome["loss_percent"], outcome["reachable"]), (100, False))

    check("the jitter computation averages the gaps",
          module.jitter_of([10.0, 12.0, 11.0]), 1.5)
    check("a single value yields no jitter",
          module.jitter_of([10.0]), None)

    completed = subprocess.run(
        [sys.executable, wrapper], input="{}", capture_output=True, text=True,
        timeout=60)
    check("the helper always ends with exit code 0", completed.returncode, 0)
    answer = json.loads(completed.stdout)
    check("an incomplete task never leads to a measurement",
          answer.get("result"), "blocked")


def aruba_arguments(**overrides):
    arguments = {
        "self_check": False, "host": "192.0.2.1", "user": "monitoring",
        "password": "s3cr3t-value", "primary": "wired", "backup": "cellular",
        "backup_share": 25, "billing_day": 1, "timeout_ms": 10000,
    }
    arguments.update(overrides)
    return arguments


# Real answers of an Aruba gateway, with the addresses replaced by
# documentation ranges. Two properties of the API are kept deliberately:
# the text arrives HTML escaped, and one "_data" entry holds several lines
# at once. Both only show up against the device, and a parser built against
# invented data would miss them.
ARUBA_STATS = {"_data": [
    "Uplinks Statistics: \n------------------------------",
    "Wired VLAN:\t4086 (dhcp_inet)\n\tActive ports:\tGE0/0/0 \n"
    "\trx_pkts/sec: 369 tx_pkts/sec: 168\n"
    "\trx_bytes/sec: 394607 tx_bytes/sec: 70811\n"
    "\tIntf Rx Pkts: 3152791660  Intf Tx Pkts: 2158780541\n"
    "\tIntf Rx Bytes: 3575728579566  Intf Tx Bytes: 1644770077435\n"
    "\tVPN Rx Bytes: 73862078  VPN Tx Bytes: 125297960",
    "Cellular VLAN:\t4095 (lte_lte)\n\trx_pkts/sec: 1 tx_pkts/sec: 1\n"
    "\trx_bytes/sec: 212 tx_bytes/sec: 188\n"
    "\tIntf Rx Pkts: 35951762  Intf Tx Pkts: 37038228\n"
    "\tIntf Rx Bytes: 12391995528  Intf Tx Bytes: 12261798797\n"
    "\tVPN Rx Bytes: 74593170  VPN Tx Bytes: 207906924",
]}
ARUBA_DEBUG = {"_data": [
    "link: 0xe19a80 type: 1(Wired), link_id: 101",
    "vlan 4086 priority: 200 state: 4(CONNECTED) err: 0 "
    "nametag: &#39;dhcp_inet&#39;",
    "probe ip: &#39;192.0.2.79&#39; latency: 14800 jitter: 192 "
    "pkt_loss: 0.000% Rvalue: 92.570 state: 1(Reachable)",
    "probe ip: &#39;198.51.100.5&#39; latency: 11200 jitter: 508 "
    "pkt_loss: 2.000% Rvalue: 92.650 state: 1(Reachable)",
    "link: 0xe19f78 type: 2(Cellular), link_id: 105",
    "probe ip: &#39;192.0.2.79&#39; latency: 50250 jitter: 0 "
    "pkt_loss: 0.000% Rvalue: 91.694 state: 1(Reachable)",
]}
ARUBA_CELLULAR = {"_data": [
    "Modem Name                \t: Internal-LTE",
    "Link Status               \t: Connected",
    "RSRP (LTE)                \t: -99 dBm",
    "SINR                      \t: 8",
    "Data usage                \t: 1700 MB",
    "IMEI                      \t: 000000000000000",
    "GPS Latitude              \t: ",
    "-----------------------------",
]}


def aruba_table(*rows):
    return {"Uplink Management Table": [dict(row) for row in rows]}


def aruba_row(kind, up=True, utilisation="0.07%"):
    return {"Uplink Type": kind, "State": "Connected" if up else "Down",
            "Reachability": "Reachable" if up else "Unreachable",
            "B/w utiln": utilisation}


def check_aruba_uplink_output():
    """Output format, parsers and role assignment of the gateway sensor."""
    print("\n== aruba-uplink: output for PRTG ==")
    script = os.path.join(SENSOR_DIR, "aruba-uplink", "script",
                          "aruba-uplink.py")
    module = load_module("aruba_uplink", script)

    completed = run_script(script, "\n")
    check("a run without parameters yields exit code 0",
          completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("the answer carries the schema version", document.get("version"), 2)
    check("without a gateway it is rejected", document.get("status"), "error")
    # The parameters are typed into a text field in PRTG and checked by
    # nothing there. The message is the only place an administrator learns
    # what to write instead.
    check("and a copyable line is in the message",
          "--host 192.0.2.1" in document.get("message", ""), True)

    completed = run_script(script, "--host 192.0.2.1\n")
    document = json.loads(completed.stdout)
    check("without credentials it is rejected too",
          document.get("status"), "error")
    check("and that message names both parameters",
          "--user NAME --password SECRET" in document.get("message", ""), True)

    # A scheme or a port would silently land in the Host header and produce
    # a connection error nobody can trace back to the parameter.
    for spec in ("https://192.0.2.1", "192.0.2.1:4343"):
        completed = run_script(
            script, "--host %s --user u --password p\n" % spec)
        document = json.loads(completed.stdout)
        check("%r is rejected as a gateway address" % spec,
              document.get("status"), "error")

    # The PRTG manual is explicit: a credential placeholder must not appear
    # in anything the script prints. argparse quotes the offending value
    # back, so a typo next to the password would leak it into every
    # notification.
    completed = run_script(
        script, "--host 192.0.2.1 --user monitoring "
                "--password s3cr3t-value --timeout-ms abc\n")
    document = json.loads(completed.stdout)
    check("a typo next to the password still fails",
          document.get("status"), "error")
    check("but the password does not reach the message",
          "s3cr3t-value" in document.get("message", ""), False)
    check("while the parameter at fault is still named",
          "--timeout-ms" in document.get("message", ""), True)
    # A secret too short to mask cannot be blanked without shredding the
    # message - then the whole message goes.
    completed = run_script(
        script, "--host 192.0.2.1 --user monitoring --password ab "
                "--timeout-ms abc\n")
    document = json.loads(completed.stdout)
    check("a very short password drops the message entirely",
          "invalid int value" in document.get("message", ""), False)

    completed = run_script(script, "--totally-unknown\n")
    check("an unknown parameter yields exit code 0", completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("an unknown parameter reports a sensor failure",
          document.get("status"), "error")
    check("the message names the typo",
          "--totally-unknown" in document.get("message", ""), True)

    completed = run_script(
        script, "--host 192.0.2.1 --user u --password p "
                "--primary wired --backup wired\n")
    document = json.loads(completed.stdout)
    check("the same kind twice is rejected", document.get("status"), "error")
    check("and the message names the way out",
          "leave --backup out" in document.get("message", ""), True)

    # The parameters name the uplinks the site has. A site on one line says
    # so by leaving --backup out, and must not end up monitoring a second
    # path nobody configured.
    completed = run_script(
        script, "--self-check --host 192.0.2.1 --user monitoring "
                "--password s3cr3t-value\n")
    document = json.loads(completed.stdout)
    check("a configuration without --backup is a valid one",
          document.get("status"), "ok")

    # The deployment runs exactly this before it activates the sensor, and
    # rolls the rollout back on anything but "ok". No gateway is known at
    # that point - it is entered in PRTG afterwards.
    completed = run_script(script, "--self-check\n")
    check("the self-test without parameters yields exit code 0",
          completed.returncode, 0)
    document = json.loads(completed.stdout)
    check("a bare self-test passes so the rollout can activate the sensor",
          document.get("status"), "ok")

    completed = run_script(
        script, "--self-check --host 192.0.2.1 --user monitoring\n")
    document = json.loads(completed.stdout)
    check("but parameters that came along are checked",
          document.get("status"), "error")

    check_aruba_uplink_parsers(module)
    check_aruba_uplink_channels(module)
    check_aruba_uplink_volume(module)
    check_aruba_uplink_secrets(module)


def check_aruba_uplink_parsers(module):
    """The parsers are where foreign text enters the sensor."""
    print("\n== aruba-uplink: parsers ==")

    rates = module.parse_uplink_stats(module.data_lines(ARUBA_STATS))
    check("the byte rates are summed per kind and direction",
          rates, {"wired": 465418, "cellular": 400})
    check("a block without rates stays at zero",
          module.parse_uplink_stats(["Wired VLAN:\t4086 (dhcp_inet)"]),
          {"wired": 0})
    check("text before any section is ignored",
          module.parse_uplink_stats(["rx_bytes/sec: 5 tx_bytes/sec: 5"]), {})

    quality = module.parse_link_quality(module.data_lines(ARUBA_DEBUG))
    # The gateway probes several targets per uplink; averaging over them is
    # the honest summary, and microseconds become milliseconds on the way.
    check("the probes land at the right uplink", sorted(quality),
          ["cellular", "wired"])
    check("latency is averaged and converted",
          round(quality["wired"]["latency_ms"], 3), 13.0)
    check("jitter as well",
          round(quality["wired"]["jitter_ms"], 3), 0.35)
    check("loss as well", round(quality["wired"]["loss_percent"], 3), 1.0)
    check("the R value as well", round(quality["wired"]["quality"], 3), 92.61)
    check("a single probe needs no averaging",
          round(quality["cellular"]["latency_ms"], 3), 50.25)
    # HTML escaping is why data_lines exists at all: without unescaping,
    # the quoted address swallows the rest of the line.
    check("the HTML escaped quotes do not break the line",
          len(module.parse_link_quality(
              ["link: 0x1 type: 1(Wired), link_id: 1",
               "probe ip: &#39;192.0.2.1&#39; latency: 1000 jitter: 0 "
               "pkt_loss: 0.000% Rvalue: 90.000 state: 1(Reachable)"])), 1)
    # The one command that is not documented API surface. A changed format
    # has to end in a failure code, never in an exception.
    check("an answer without link blocks yields nothing, not a crash",
          module.parse_link_quality(["Uplink Manager: Enabled", ""]), {})

    values = module.cellular_values(ARUBA_CELLULAR)
    check("the radio values are read", values,
          {"rsrp": -99.0, "sinr": 8.0, "data_usage": 1700.0})
    # IMEI, IMSI, cell ID and GPS sit in the same block. They are device and
    # location identifiers and must not reach a channel or a message.
    check("no identifier is carried along",
          [key for key in values if key not in ("rsrp", "sinr", "data_usage")],
          [])
    check("a line without a colon is skipped",
          module.parse_key_values(["-------", "Key : Value"]),
          {"Key": "Value"})
    check("an empty value stays empty",
          module.parse_key_values(["Standby SIM : "]), {"Standby SIM": ""})

    uplinks = module.parse_uplinks(aruba_table(
        aruba_row("Wired", up=False, utilisation="1.00%"),
        aruba_row("Wired", up=True, utilisation="4.00%"),
        aruba_row("Cellular")))
    # Several uplinks of one kind: the kind stands as soon as one of them
    # does, and the utilisation is the worst of them.
    check("two wired uplinks collapse into one statement",
          (uplinks["wired"]["up"], uplinks["wired"]["count"],
           uplinks["wired"]["utilisation"]), (True, 2, 4.0))
    try:
        module.parse_uplinks({"_data": []})
        message = ""
    except module.Failed as problem:
        message = problem.code
    check("a missing table is a bad answer, not a crash", message,
          "bad-answer")


def check_aruba_uplink_channels(module):
    """Which channels exist follows the configuration, never the result."""
    print("\n== aruba-uplink: channels ==")

    uplinks = module.parse_uplinks(aruba_table(aruba_row("Wired"),
                                               aruba_row("Cellular")))
    rates = module.parse_uplink_stats(module.data_lines(ARUBA_STATS))
    quality = module.parse_link_quality(module.data_lines(ARUBA_DEBUG))

    def identifiers(**overrides):
        args = aruba_arguments(**overrides)
        summary = module.summarise(args, uplinks, rates)
        document = module.present(args, summary, {"rsrp": -99.0}, quality, 0,
                                  12, "ok", "")
        return [entry["id"] for entry in document["channels"]]

    default = identifiers()
    check("the channels come out ascending", default, sorted(default))
    check("with a backup every block is present", default,
          [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22,
           30, 31, 32, 33, 40, 41, 42, 43])
    # Without an alternative path the backup channels stay and report that
    # the site does not use one. Dropping them would tear the history apart
    # the day somebody adds a second line.
    check("without a backup the backup channels stay",
          identifiers(backup="none"),
          [10, 11, 12, 13, 14, 15, 16, 17, 18, 20,
           30, 31, 32, 33, 40, 41, 42, 43])

    def states(**overrides):
        args = aruba_arguments(**overrides)
        summary = module.summarise(args, uplinks, rates)
        document = module.present(args, summary, {"rsrp": -99.0}, quality, 0,
                                  12, "ok", "")
        return {entry["id"]: entry["value"]
                for entry in document["channels"]}

    # The third state is what makes a single-line site readable: not an
    # outage, not a healthy backup, but a path this site does not use.
    check("an unused backup is neither up nor down",
          states(backup="none")[13], module.STATE_UNUSED)
    check("a backup in use reports its state", states()[13],
          module.STATE_UP)
    check("a dead backup is an error",
          module.state_value(False), module.STATE_DOWN)
    check("and the unused state wins over the measurement",
          module.state_value(True, False), module.STATE_UNUSED)
    # An unused path has nothing to measure, so its quality channels have
    # to stand at zero rather than repeat the primary's numbers.
    check("an unused backup measures nothing",
          [states(backup="none")[identifier]
           for identifier in (14, 40, 41, 42, 43)],
          [0.0, 0.0, 0.0, 0.0, 0.0])
    check("the primary never reports the unused state",
          states(backup="none")[12], module.STATE_UP)
    check("a wired-only site carries no radio channels",
          [identifier for identifier in identifiers(backup="none")
           if identifier in (19, 21, 22)], [])
    check("an LTE-only site does carry them",
          [identifier for identifier
           in identifiers(primary="cellular", backup="none")
           if identifier in (19, 21, 22)], [19, 21, 22])
    check("the roles can be swapped",
          identifiers(primary="cellular", backup="wired"), default)

    # A failure must not change the channel structure - a channel that
    # disappears during an outage tears its history apart in PRTG.
    failed = module.failure_result(aruba_arguments(), "gateway-unreachable",
                                   "nope")
    check("an unreachable gateway keeps every channel",
          [entry["id"] for entry in failed["channels"]], default)
    codes = {entry["id"]: entry["value"] for entry in failed["channels"]}
    check("and sets Test Result to error", codes[10], module.LOOKUP_NO)
    check("and enters its code", codes[18],
          module.FAILURE_CODES["gateway-unreachable"])
    check("and reports the primary as down", codes[12], module.STATE_DOWN)

    # The path measurement is secondary: losing it must not pull the result
    # channel down with it.
    partial = module.present(aruba_arguments(),
                             module.summarise(aruba_arguments(), uplinks,
                                              rates),
                             {}, {}, 0, 12, "no-quality-data", "no data")
    codes = {entry["id"]: entry["value"] for entry in partial["channels"]}
    check("a missing path measurement stays a valid run", codes[10],
          module.LOOKUP_YES)
    check("but is named by its code", codes[18],
          module.FAILURE_CODES["no-quality-data"])
    check("and leaves the quality channels at zero", codes[33], 0.0)

    # The threshold, not equality: a gateway that load-balances puts a
    # little traffic on the backup permanently.
    quiet = module.summarise(aruba_arguments(), uplinks,
                             {"wired": 10000, "cellular": 100})
    check("a little traffic on the backup is no changeover",
          (round(quiet["backup_share"], 2), quiet["on_primary"]),
          (0.99, True))
    moved = module.summarise(aruba_arguments(), uplinks,
                             {"wired": 100, "cellular": 10000})
    check("most of it on the backup is one", moved["on_primary"], False)
    check("a dead primary is never on the primary",
          module.summarise(
              aruba_arguments(),
              module.parse_uplinks(aruba_table(aruba_row("Wired", up=False),
                                               aruba_row("Cellular"))),
              rates)["primary_up"], False)
    # Without a backup there is no share to compare against.
    check("without a backup only the primary decides",
          module.summarise(aruba_arguments(backup="none"), uplinks,
                           rates)["on_primary"], True)

    empty = module.summarise(aruba_arguments(), {}, {})
    check("a gateway without uplinks yields zeros, not missing channels",
          (empty["connected"], empty["primary_up"], empty["backup_share"]),
          (0, False, 0.0))


def check_aruba_uplink_volume(module):
    """The mobile data volume - the one number the gateway gets wrong.

    An Aruba 9004-LTE on AOS-10.7.1.0 reports "Data usage" through a signed
    32-bit byte counter: past 2 GiB in a period it turns negative and works
    its way back towards zero, so a threshold on the volume never fires. The
    sensor therefore counts for itself, out of the interface counters of the
    same answer, which are wide enough to hold a month.
    """
    print("\n== aruba-uplink: data volume ==")

    totals = module.parse_uplink_totals(module.data_lines(ARUBA_STATS))
    check("the cumulative counters are read per uplink kind",
          (totals.get("wired"), totals.get("cellular")),
          (3575728579566 + 1644770077435, 12391995528 + 12261798797))
    # The VPN lines sit in the same block and count a subset. Adding them in
    # would count the tunnelled traffic twice.
    check("the VPN lines are left out",
          module.parse_uplink_totals([
              "Cellular VLAN:\t4095 (lte_lte)",
              "\tIntf Rx Bytes: 100  Intf Tx Bytes: 200",
              "\tVPN Rx Bytes: 7  VPN Tx Bytes: 9"]),
          {"cellular": 300})

    # A billing day of 29 to 31 has to land somewhere in February.
    check("a billing day that the month does not have moves to its last",
          (module.day_in_month(31, 2026, 2), module.day_in_month(31, 2026, 1),
           module.day_in_month(15, 2026, 2)), (28, 31, 15))
    check("before the billing day the period still belongs to last month",
          module.billing_period_start(15, datetime.date(2026, 3, 4)),
          "2026-02-15")
    check("on the billing day the new period begins",
          module.billing_period_start(15, datetime.date(2026, 3, 15)),
          "2026-03-15")
    check("in January it reaches back into the previous year",
          module.billing_period_start(15, datetime.date(2026, 1, 2)),
          "2025-12-15")
    check("a billing day of 0 has no period at all",
          module.billing_period_start(0, datetime.date(2026, 3, 4)), "")

    today = datetime.date(2026, 3, 10)
    # The first run has no reading to measure against. Reporting the counter
    # itself would claim months of traffic as this period's volume.
    volume, state = module.usage_since(None, 5_000, 1, today)
    check("the first run reports no volume yet", volume, None)
    check("but remembers where it started", state["baseline"], 5_000)

    volume, state = module.usage_since(state, 9_000, 1, today)
    check("the second run counts the difference", volume, 4_000)

    # A probe that was down for an hour must not lose that hour: the volume
    # is a difference against the start of the period, not a sum of deltas.
    volume, _ = module.usage_since(state, 500_000, 1, today)
    check("a gap between runs does not lose traffic", volume, 495_000)

    # The gateway's interface counters start at zero after a restart.
    # 9_000 stood on the counter, but 5_000 of that was there before the
    # period began: only the 4_000 above the baseline may survive a restart.
    rebooted, state = module.usage_since(state, 40, 1, today)
    check("a restarted gateway keeps what it carried in this period",
          rebooted, 4_040)
    check("and counts on from there",
          module.usage_since(state, 100, 1, today)[0], 4_100)

    later, fresh = module.usage_since(state, 12_000, 1,
                                      datetime.date(2026, 4, 1))
    check("the billing day starts the volume over", later, 0)
    check("and takes the current reading as the new start",
          fresh["baseline"], 12_000)
    check("a month without a billing day in between keeps counting",
          module.usage_since(state, 12_000, 1,
                             datetime.date(2026, 3, 31))[0], 16_000)

    # Billing day 0 is the plain counter: no period, so nothing resets it.
    running = {"period_start": "", "baseline": 0, "carry": 0, "last": 1_000}
    check("a billing day of 0 never starts over",
          module.usage_since(running, 8_000, 0,
                             datetime.date(2027, 1, 1))[0], 8_000)

    # What PRTG sees. The channel has to be there even while the volume is
    # not known yet - a channel that appears later tears its history apart.
    uplinks = module.parse_uplinks(aruba_table(aruba_row("Wired"),
                                               aruba_row("Cellular")))
    summary = module.summarise(aruba_arguments(), uplinks, {})
    unknown = module.present(aruba_arguments(), summary, {"data_usage": -1148.0},
                             {}, 0, 5, "ok", "")
    usage_channel = [entry for entry in unknown["channels"]
                     if entry["id"] == 22]
    check("the volume channel exists from the first run",
          (len(usage_channel), usage_channel[0]["value"]), (1, 0.0))
    check("and the message says the volume is only starting",
          "counted from this run on" in unknown["message"], True)
    # Whoever compares the channel against the gateway has to learn why the
    # two disagree, or the sensor looks like the broken one.
    check("a negative counter on the gateway is named, not copied",
          "-1148 MB" in unknown["message"], True)

    summary = dict(summary, data_usage_mb=2948.0, data_usage_known=True)
    known = module.present(aruba_arguments(), summary, {"data_usage": 1700.0},
                           {}, 0, 5, "ok", "")
    check("a known volume reaches the channel",
          [entry["value"] for entry in known["channels"]
           if entry["id"] == 22], [2948.0])
    check("and then the message keeps quiet about it",
          ("counted from this run on" in known["message"]
           or "data counter" in known["message"]), False)

    script = os.path.join(SENSOR_DIR, "aruba-uplink", "script",
                          "aruba-uplink.py")
    completed = run_script(
        script, "--host 192.0.2.1 --user u --password p --billing-day 32\n")
    document = json.loads(completed.stdout)
    check("a billing day the calendar does not have is rejected",
          document.get("status"), "error")
    check("and the message names the range",
          "--billing-day must be between 0 and 31"
          in document.get("message", ""), True)


def check_aruba_uplink_secrets(module):
    """Nothing the sensor prints may carry the password."""
    print("\n== aruba-uplink: secrets ==")

    document = module.self_check(aruba_arguments(self_check=True))
    check("complete parameters pass the self-test",
          document.get("status"), "ok")
    # The self-test must not reach out to the gateway: activating a sensor
    # would otherwise depend on a device across the site.
    check("and the self-test names the gateway, not the password",
          ("192.0.2.1" in document["message"],
           "s3cr3t-value" in json.dumps(document)), (True, False))

    tokens = ["--user", "monitoring", "--password", "s3cr3t-value"]
    check("the password is masked wherever it appears",
          module.redact("saw s3cr3t-value here", tokens), "saw ... here")
    # The user name is no secret - it stands in the sensor configuration
    # anyway, and blanking a short one would shred the whole message.
    check("the user name is left alone",
          module.redact("saw monitoring here", tokens), "saw monitoring here")
    check("a secret too short to mask drops the message",
          "invalid" in module.redact(
              "invalid value", ["--password", "va"]), False)
    check("a message without the secret survives untouched",
          module.redact("--timeout-ms takes a number", tokens),
          "--timeout-ms takes a number")


# --- The return channel service ------------------------------------------
#
# The service is not a file of its own in the repository: the helper writes it
# from a heredoc, because only the helper arrives under a signature (ADR
# 0006). The checks cut it out of the helper the same way, so what they load
# is byte for byte what a probe runs.

HELPER_PATH = os.path.join(PROJECT_DIR, "libexec", "prtg-nats-probe-helper")
CHANNEL_HEREDOC = "CHANNELEOF"


def return_channel_source():
    with open(HELPER_PATH, encoding="utf-8") as handle:
        lines = handle.read().splitlines(keepends=True)
    start = next(index for index, line in enumerate(lines)
                 if line.rstrip().endswith("<<'%s'" % CHANNEL_HEREDOC))
    end = next(index for index in range(start + 1, len(lines))
               if lines[index].rstrip("\n") == CHANNEL_HEREDOC)
    return "".join(lines[start + 1:end])


def load_return_channel(directory):
    path = os.path.join(directory, "prtg-nats-return-channel")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(return_channel_source())
    return load_module("return_channel", path)


def channel_record(**overrides):
    record = {"sensor": "wlan-auth", "result": "ok", "summary": "fine",
              "job": "0123456789ab", "body": {"ssid": "Example"}}
    record.update(overrides)
    return record


def offer(module, buffer, record):
    """Hand one record over the way a sensor does, as one JSON line."""
    line = json.dumps(record).encode("utf-8")
    return json.loads(module.handle_line(buffer, line))


def check_return_channel_interface():
    """What a sensor sees: one JSON line in, the record ID back."""
    print("\n== return channel: local interface ==")
    with tempfile.TemporaryDirectory() as directory:
        module = load_return_channel(directory)
        buffer = module.Buffer(os.path.join(directory, "buffer"),
                               limit=module.MINIMUM_LIMIT)

        reply = offer(module, buffer, channel_record())
        check("a record without an ID is accepted", reply.get("ok"), True)
        check_true("and the answer carries the ID it was given",
                   re.fullmatch(r"[0-9a-f-]{36}", reply.get("id", "")))

        chosen = "3f0c3d1e-58a4-4c39-9a3b-7e0f2b6f4d10"
        reply = offer(module, buffer, channel_record(id=chosen))
        check("a record that brings its own ID keeps it",
              reply.get("id"), chosen)

        reply = offer(module, buffer, channel_record(probe="someone-else"))
        batch = module.Buffer(os.path.join(directory, "buffer"),
                              limit=module.MINIMUM_LIMIT).batch(10)
        check("records come out in the order they went in",
              [record["id"] for record in batch][1:], [chosen, reply["id"]])
        check("the probe is the transport's to say, not the record's",
              "probe" in batch[2], False)
        check_true("a record without a time gets one in UTC",
                   batch[0]["time"].endswith("Z"))

        for description, record in [
            ("a sensor name with a slash is refused",
             channel_record(sensor="../wlan")),
            ("a gap is the service's to write, not a sensor's",
             channel_record(result="gap")),
            ("a result outside ok and error is refused",
             channel_record(result="maybe")),
            ("an ID that is no UUID is refused", channel_record(id="7")),
            ("a body that is no object is refused", channel_record(body=[1])),
            ("a code longer than the platform takes is refused",
             channel_record(code="x" * 129)),
            ("a time that does not parse is refused",
             channel_record(time="yesterday")),
        ]:
            check(description, offer(module, buffer, record).get("ok"), False)
        check("a line that is no JSON is answered, not dropped",
              json.loads(module.handle_line(buffer, b"{nope")).get("ok"),
              False)
        check("and nothing refused reached the buffer",
              len(buffer.batch(50)), 3)


def fill(module, buffer, count, size=3000):
    """Records of a known size, numbered by their time so order shows."""
    identities = []
    for number in range(count):
        record = module.normalise(channel_record(
            summary="run %d" % number, body={"padding": "x" * size},
            time="2026-11-08T10:%02d:%02dZ" % divmod(number, 60)))
        identities.append(buffer.append(record))
    return identities


def check_return_channel_overflow():
    """A full buffer drops its oldest records and says so."""
    print("\n== return channel: buffer limit ==")
    with tempfile.TemporaryDirectory() as directory:
        module = load_return_channel(directory)
        path = os.path.join(directory, "buffer")
        buffer = module.Buffer(path, limit=module.MINIMUM_LIMIT)
        identities = fill(module, buffer, 200)

        stats = buffer.stats()
        check_true("the buffer stays within its limit",
                   0 < stats["bytes"] <= stats["limit"])
        batch = buffer.batch(500)
        gap = batch[0]
        kept = [record["id"] for record in batch[1:]]
        dropped = len(identities) - len(kept)
        check("the first thing to deliver is the gap", gap["result"], "gap")
        check("it is reported under the service's own name",
              gap["sensor"], "return-channel")
        check("the newest records are the ones kept",
              kept, identities[-len(kept):])
        check("the gap counts exactly what is missing",
              gap["body"]["dropped"], dropped)
        check("and names the time range of what it lost",
              (gap["body"]["from"], gap["body"]["until"]),
              ("2026-11-08T10:00:00.000000Z",
               "2026-11-08T10:%02d:%02d.000000Z" % divmod(dropped - 1, 60)))
        check("one overflow after another is still one gap",
              [record["result"] for record in batch].count("gap"), 1)

        # Offered to the platform, the gap may be stored there already.
        # What is dropped after that is counted in a gap of its own.
        fill(module, buffer, 5)
        later = buffer.batch(500)
        check("a gap that went out is not counted into again",
              (later[0]["id"], later[0]["body"]["dropped"]),
              (gap["id"], gap["body"]["dropped"]))
        check("the drops after it make a second gap",
              later[1]["result"], "gap")

        reopened = module.Buffer(path, limit=module.MINIMUM_LIMIT)
        check("a restart finds the same records in the same order",
              [record["id"] for record in reopened.batch(500)],
              [record["id"] for record in later])
        check("a record larger than the service takes is refused",
              offer(module, buffer, channel_record(
                  body={"padding": "x" * module.MAXIMUM_RECORD_BYTES})
              ).get("ok"), False)


class StubPlatform:
    """The records endpoint, idempotent on the ID like the real one."""

    def __init__(self):
        self.stored = []
        self.requests = []
        self.down = False
        self.lose_answer = False
        self.acknowledge_only = None
        self.refuse = set()

    def post(self, path, document):
        self.requests.append((path, document))
        if self.down:
            raise ConnectionRefusedError(111, "Connection refused")
        records = document["records"]
        if any(record["id"] in self.refuse for record in records):
            return 422, {"detail": "invalid"}
        stored, duplicates = [], []
        for record in records[:self.acknowledge_only]:
            if record["id"] in self.stored:
                duplicates.append(record["id"])
            else:
                self.stored.append(record["id"])
                stored.append(record["id"])
        if self.lose_answer:
            self.lose_answer = False
            raise TimeoutError("timed out")
        return 200, {"stored": stored, "duplicates": duplicates}


def check_return_channel_delivery():
    """Delivery in order, without loss or duplicates, across an outage."""
    print("\n== return channel: delivery ==")
    with tempfile.TemporaryDirectory() as directory:
        module = load_return_channel(directory)
        # Small enough that every acknowledgement rewrites the file, so the
        # rewrite is what the restart below has to survive.
        module.COMPACT_AFTER = 1
        path = os.path.join(directory, "buffer")
        buffer = module.Buffer(path, limit=module.MINIMUM_LIMIT)
        platform = StubPlatform()
        check("a buffer that never held a record sends nothing",
              module.deliver_once(buffer, platform.post), False)
        identities = fill(module, buffer, 10, size=10)

        platform.down = True
        for _ in range(3):
            try:
                module.deliver_once(buffer, platform.post)
            except OSError:
                pass
        check("an outage keeps every record", len(buffer.batch(500)), 10)

        platform.down = False
        platform.acknowledge_only = 4
        module.deliver_once(buffer, platform.post)
        path_sent, document = platform.requests[-1]
        check("records go to the platform's records endpoint",
              path_sent, "/probe/v1/records")
        check("with the buffer's fill level beside them",
              sorted(document["buffer"]), ["bytes", "limit"])
        check("only what was acknowledged leaves the buffer",
              [record["id"] for record in buffer.batch(500)], identities[4:])

        platform.acknowledge_only = None
        platform.lose_answer = True
        try:
            module.deliver_once(buffer, platform.post)
        except OSError:
            pass
        check("a lost answer keeps the records it was about",
              len(buffer.batch(500)), 6)
        module.deliver_once(buffer, platform.post)
        check("sent again, they come back as duplicates and leave",
              buffer.pending(), False)
        check("the platform holds every record once, in order",
              platform.stored, identities)
        check("an empty buffer sends nothing",
              module.deliver_once(buffer, platform.post), False)
        sent = len(platform.requests)
        check("unless a heartbeat is due",
              module.deliver_once(buffer, platform.post, heartbeat=True), True)
        check("which is one empty batch with the buffer's fill level",
              (len(platform.requests) - sent, platform.requests[-1][1]),
              (1, {"records": [], "buffer": buffer.stats()}))
        check("a heartbeat is due before the first delivery",
              module.heartbeat_due(None, 10.0, 300), True)
        check("not within the interval after a delivery",
              module.heartbeat_due(100.0, 399.0, 300), False)
        check("and again once the interval has passed",
              module.heartbeat_due(100.0, 400.0, 300), True)
        check("the interval comes from channel.env",
              module.heartbeat_interval({"HEARTBEAT_SECONDS": "120"}), 120)
        check("and defaults to five minutes",
              module.heartbeat_interval({}), 300)
        check("a broken value falls back to the default",
              module.heartbeat_interval({"HEARTBEAT_SECONDS": "often"}), 300)

        reopened = module.Buffer(path, limit=module.MINIMUM_LIMIT)
        check("and stays empty after a restart", reopened.pending(), False)

        late = fill(module, reopened, 3, size=10)
        platform.refuse = {late[1]}
        with contextlib.redirect_stderr(io.StringIO()):
            module.deliver_once(reopened, platform.post)
        module.deliver_once(reopened, platform.post)
        check("a record the platform refuses holds up nothing behind it",
              [identity for identity in platform.stored if identity in late],
              [late[0], late[2]])
        gap = platform.requests[-1][1]["records"][0]
        check("and is accounted for as a gap",
              (gap["result"], gap["code"], gap["body"]["dropped"]),
              ("gap", "refused", 1))
        check("which leaves the buffer once acknowledged",
              reopened.pending(), False)


def openssl_run(directory, *arguments):
    subprocess.run(["openssl"] + list(arguments), cwd=directory, check=True,
                   capture_output=True, timeout=60)


class StubSigner:
    """The certificate endpoint: signs a CSR with a throwaway CA."""

    def __init__(self, directory):
        self.directory = directory
        self.refuse = False
        self.requests = 0

    def post(self, path, document):
        self.requests += 1
        if self.refuse:
            return 403, {"detail": "not enrolled"}
        with open(os.path.join(self.directory, "request.csr"), "w") as handle:
            handle.write(document["csr"])
        self.sign("request.csr", "signed.pem")
        with open(os.path.join(self.directory, "signed.pem")) as handle:
            return 200, {"certificate": handle.read(),
                         "not_after": "2027-02-06T00:00:00Z"}

    def sign(self, request, output):
        openssl_run(self.directory, "x509", "-req", "-in", request,
                    "-CA", "ca.pem", "-CAkey", "ca-key.pem",
                    "-set_serial", str(int(time.time() * 1000)),
                    "-days", "90", "-sha256", "-extfile", "client.ext",
                    "-out", output)


def channel_pki(directory):
    """A client CA and a first certificate, as channel-install leaves them."""
    with open(os.path.join(directory, "client.ext"), "w") as handle:
        handle.write("basicConstraints=CA:FALSE\n"
                     "keyUsage=critical,digitalSignature\n"
                     "extendedKeyUsage=clientAuth\n")
    openssl_run(directory, "genpkey", "-algorithm", "EC", "-pkeyopt",
                "ec_paramgen_curve:P-256", "-out", "ca-key.pem")
    openssl_run(directory, "req", "-x509", "-new", "-key", "ca-key.pem",
                "-subj", "/CN=Example probe CA", "-days", "365",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-out", "ca.pem")
    openssl_run(directory, "genpkey", "-algorithm", "EC", "-pkeyopt",
                "ec_paramgen_curve:P-256", "-out", "key.pem")
    openssl_run(directory, "req", "-new", "-key", "key.pem", "-subj",
                "/CN=prtg-nats-probe", "-out", "first.csr")
    signer = StubSigner(directory)
    signer.sign("first.csr", "cert.pem")
    return signer


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def check_return_channel_renewal():
    """From day 60 of 90 the service fetches a certificate for a new key."""
    print("\n== return channel: certificate renewal ==")
    with tempfile.TemporaryDirectory() as directory:
        module = load_return_channel(directory)
        day = 86400
        check("on day 59 the certificate is left alone",
              module.renewal_due(0, 90 * day, 59 * day), False)
        check("from day 60 it is renewed",
              module.renewal_due(0, 90 * day, 60 * day), True)

        if not shutil.which("openssl"):
            print("  skip  no openssl to sign a renewal with")
            return
        pki = os.path.join(directory, "pki")
        os.mkdir(pki)
        signer = channel_pki(pki)
        key, certificate = (read_text(os.path.join(pki, name))
                            for name in ("key.pem", "cert.pem"))

        check("a fresh certificate is not renewed",
              module.renew_if_due(signer, pki), False)
        check("and the platform is not asked", signer.requests, 0)

        later = time.time() + 61 * day
        signer.refuse = True
        with contextlib.suppress(module.DeliveryError):
            module.renew_if_due(signer, pki, now=later)
        check("a refused renewal keeps the pair that works",
              (read_text(os.path.join(pki, "key.pem")),
               read_text(os.path.join(pki, "cert.pem"))), (key, certificate))
        check("and leaves nothing half done behind",
              sorted(name for name in os.listdir(pki)
                     if ".next." in name or ".csr." in name), [])

        signer.refuse = False
        with contextlib.redirect_stderr(io.StringIO()):
            renewed = module.renew_if_due(signer, pki, now=later)
        check("an old certificate is renewed", renewed, True)
        check("with a key that was never used before",
              read_text(os.path.join(pki, "key.pem")) != key, True)
        check("and the certificate that belongs to it",
              module.matches(os.path.join(pki, "cert.pem"),
                             os.path.join(pki, "key.pem")), True)

        # A crash between the two renames: the key is in place, the
        # certificate still waits beside it.
        openssl_run(pki, "req", "-new", "-key", "key.pem", "-subj",
                    "/CN=prtg-nats-probe", "-out", "again.csr")
        signer.sign("again.csr", "cert.next.pem")
        module.recover_renewal(pki)
        check("an interrupted swap is finished on the next start",
              (os.path.exists(os.path.join(pki, "cert.next.pem")),
               module.matches(os.path.join(pki, "cert.pem"),
                              os.path.join(pki, "key.pem"))), (False, True))


def check_return_channel_units():
    """The unit the helper writes for the service, hardened like the rest."""
    print("\n== return channel: unit ==")
    with open(HELPER_PATH, encoding="utf-8") as handle:
        source = handle.read()
    unit = source.split("${RETURN_CHANNEL_UNIT}.service.new\" <<EOF", 1)[1]
    unit = unit.split("\nEOF\n", 1)[0]
    for setting in ("Restart=always", "LimitCORE=0", "NoNewPrivileges=yes",
                    "ProtectSystem=strict", "ProtectHome=yes",
                    "PrivateTmp=yes", "ReadWritePaths=${RETURN_CHANNEL_DIR}",
                    "StateDirectory=prtg-nats-return-channel",
                    "WantedBy=multi-user.target"):
        check("the service unit sets %s" % setting, setting in unit, True)
    socket_unit = source.split(
        "${RETURN_CHANNEL_UNIT}.socket.new\" <<EOF", 1)[1].split("\nEOF\n")[0]
    check("only root may hand records over",
          ("SocketUser=root" in socket_unit, "SocketMode=0600" in socket_unit),
          (True, True))
    check("unenrolling takes the service along",
          "remove_return_channel_service\n  rm -rf -- \"${RETURN_CHANNEL_DIR}\""
          in source, True)


if __name__ == "__main__":
    check_manifests()
    check_parameter_declarations()
    check_privilege_path()
    check_wlan_auth_output()
    check_wlan_auth_configuration()
    check_wlan_auth_scheduler()
    check_wlan_auth_sensor_log()
    check_internet_speed_output()
    check_iperf_throughput_output()
    check_link_quality_output()
    check_link_quality_helper()
    check_aruba_uplink_output()
    check_return_channel_interface()
    check_return_channel_overflow()
    check_return_channel_delivery()
    check_return_channel_renewal()
    check_return_channel_units()
    if FAILURES:
        print("\n%d sensor check(s) failed." % FAILURES,
              file=sys.stderr)
    sys.exit(1 if FAILURES else 0)
