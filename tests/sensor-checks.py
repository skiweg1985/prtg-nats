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
          "prtg.standardlookups.yesno.stateyesok")
    check("the association time survives a failure",
          channels[12]["value"], 2700)
    check("the failure cause is machine-readable", channels[18]["value"], 3)
    check("without a DHCP stage the DHCP channel is absent", 14 in channels, False)
    check("channels are sorted ascending",
          [entry["id"] for entry in result["channels"]],
          sorted(entry["id"] for entry in result["channels"]))
    check("the message names the cause",
          "auth-failed-eap" in result["message"], True)


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
    check("29.99 of 30 Mbit/s count as held", tcp["met"], True)
    # Without a target rate there is nothing to hold, and an invented
    # finding would be worse than none.
    check("without a target rate there is no finding",
          module.summarise(IPERF_TCP_REPORT, False, None)["met"], None)
    check("the measured rate is still there",
          module.summarise(IPERF_TCP_REPORT, False, None)["bit_s"], 29990000)
    check("20 of 30 Mbit/s do not",
          module.summarise({"end": {"sum_received": {"bits_per_second": 2e7},
                                    "sum_sent": {}}}, False, target)["met"],
          False)

    udp = module.summarise(IPERF_UDP_REPORT, True, target)
    check("UDP reports the jitter", udp["jitter_ms"], 0.339)
    check("UDP reports the loss", udp["loss_percent"], 0.8)
    # With UDP the loss decides, not the rate: the target rate is sent
    # regardless of whether the line carries it.
    check("0.8 % loss is still within tolerance", udp["met"], True)
    check("5 % loss is not",
          module.summarise({"end": {"sum": {"bits_per_second": 3e7,
                                            "lost_percent": 5.0}}},
                           True, target)["met"], False)

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
        "download_met": 1, "download_loss_percent": 0.12,
        "download_jitter_ms": 0.34,
        "upload_kbit": 4000, "upload_target_kbit": 10000, "upload_met": 0,
        "upload_loss_percent": 6.5, "upload_jitter_ms": 2.1,
        "target_met": 0, "duration_ms": 21000,
    }
    result = module.present(measurement, 0, iperf_arguments())
    by_id = {entry["id"]: entry for entry in result["channels"]}

    check("the channel list is sorted ascending",
          [entry["id"] for entry in result["channels"]],
          sorted(by_id))
    check("a missed target remains a successful measurement",
          by_id[10]["value"], module.LOOKUP_YES)
    check("and only shows in its own channel", by_id[20]["value"],
          module.LOOKUP_NO)
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
          "--backup none" in document.get("message", ""), True)

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
    # Without an alternative path the backup channels would report a
    # permanent alarm for something the site does not have.
    check("without a backup the backup channels are gone",
          identifiers(backup="none"),
          [10, 11, 12, 15, 16, 17, 18, 20, 30, 31, 32, 33])
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
    check("and reports the primary as down", codes[12], module.LOOKUP_NO)

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


if __name__ == "__main__":
    check_manifests()
    check_parameter_declarations()
    check_privilege_path()
    check_wlan_auth_output()
    check_wlan_auth_configuration()
    check_internet_speed_output()
    check_iperf_throughput_output()
    check_link_quality_output()
    check_link_quality_helper()
    check_aruba_uplink_output()
    if FAILURES:
        print("\n%d sensor check(s) failed." % FAILURES,
              file=sys.stderr)
    sys.exit(1 if FAILURES else 0)
