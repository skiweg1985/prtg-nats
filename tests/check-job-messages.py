#!/usr/bin/env python3

"""Checks every job log message the backend can emit against the locales.

The interface renders a job log by looking the event code up as a translation
key: "jobs.iperf.enrolled" becomes "jobs.events.iperf.enrolled". A code with no
entry falls back to the raw key, so the operator watching a rollout reads
"jobs.iperf.enrolled" instead of a sentence - and nothing fails, which is how a
whole group of messages stayed untranslated for months.

The frontend's own check compares the two languages against each other and is
satisfied when a key is missing from both. This is the other direction: what
the backend logs, against what the locales carry.

Read out of the syntax tree rather than grepped. Most of these calls span
several lines, and a pattern that silently stops matching is a check that
passes while checking nothing.

Two failures are reported:

  1. an event code with no message in one of the languages;
  2. a message whose placeholder is not among the params of the call, so the
     interface renders "{{endpoint}}" literally.

Calls whose params are not literals - built from a variable, or merged with
"**" - are still checked for their key; only their placeholders are skipped,
because there is nothing to compare them against.

Both shapes of the call are read: context.log(code) in the handlers and
jobs.log(job, code) in the runner. A code the call picks out of a module-level
table counts as every code in that table, because any of them can be the one
that ends up in the log.

Runs on its own, without Docker or network; the exit code is 1 as soon as
one check fails.
"""

import ast
import json
import os
import re
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_DIR, "web", "backend", "app")
LOCALES_DIR = os.path.join(PROJECT_DIR, "web", "frontend", "src", "i18n", "locales")

# What JobProgress.tsx does with the code before it looks it up.
CODE_PREFIX = "jobs."
KEY_PREFIX = "jobs.events."

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class LogCall:
    """One context.log() call: where it is, what it logs, what it hands over."""

    def __init__(self, path, lineno, code, params):
        self.path = path
        self.lineno = lineno
        self.code = code
        # None means "not readable from the source", which is not the same as
        # an empty set - a call without params really does hand over nothing.
        self.params = params

    @property
    def where(self):
        return "%s:%d" % (os.path.relpath(self.path, PROJECT_DIR), self.lineno)

    @property
    def key(self):
        return KEY_PREFIX + self.code[len(CODE_PREFIX) :]


def dict_keys(node):
    """The keys of a dict literal, or None if they cannot all be read.

    A "**rest" entry arrives as a key of None, and a computed key is not a
    constant string. Either way the call may pass more than what is written
    here, so the placeholders cannot be judged.
    """
    if not isinstance(node, ast.Dict):
        return None
    names = set()
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        names.add(key.value)
    return names


def code_tables(tree):
    """Module dicts whose values are job codes, by the name they are bound to.

    The runner picks the message for a finished job out of one of these rather
    than writing it at the call. Without them that call carries no readable
    code at all and would be skipped - which is how "Job failed: {{reason}}"
    reached an operator's screen with nothing to put in the placeholder.
    """
    tables = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        codes = {
            entry.value
            for entry in value.values
            if isinstance(entry, ast.Constant)
            and isinstance(entry.value, str)
            and entry.value.startswith(CODE_PREFIX)
        }
        if not codes or len(codes) != len(value.values):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                tables[target.id] = codes
    return tables


def codes_of(node, tables):
    """Every code one first argument to log() can turn out to be."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if node.value.startswith(CODE_PREFIX) else set()
    # TABLE.get(key, "jobs.fallback") - the fallback is a code like any other.
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in tables
    ):
        found = set(tables[node.func.value.id])
        for argument in node.args[1:]:
            found |= codes_of(argument, tables)
        return found
    # An IfExp picks between two codes: both can be logged.
    if isinstance(node, ast.IfExp):
        return codes_of(node.body, tables) | codes_of(node.orelse, tables)
    return set()


def log_calls(path):
    """Every context.log("jobs.…") in one file."""
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    tables = code_tables(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "log":
            continue
        # Two shapes reach the same log: a handler calls context.log(code) and
        # the runner calls jobs.log(job, code). Looking only at the first
        # argument checked the handlers and left the runner - the one place
        # that logs how a job ended - unchecked.
        codes = set()
        for argument in node.args:
            codes = codes_of(argument, tables)
            if codes:
                break
        if not codes:
            continue

        params = set()
        for keyword in node.keywords:
            if keyword.arg == "params":
                params = dict_keys(keyword.value)
        for code in sorted(codes):
            yield LogCall(path, node.lineno, code, params)


def collect_calls():
    found = []
    for root, _, files in os.walk(BACKEND_DIR):
        for name in sorted(files):
            if name.endswith(".py"):
                found.extend(log_calls(os.path.join(root, name)))
    return sorted(found, key=lambda call: (call.path, call.lineno))


def flatten(value, prefix=""):
    flat = {}
    for key, entry in value.items():
        path = "%s.%s" % (prefix, key) if prefix else key
        if isinstance(entry, dict):
            flat.update(flatten(entry, path))
        else:
            flat[path] = str(entry)
    return flat


def load_locales():
    tables = {}
    for name in sorted(os.listdir(LOCALES_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(LOCALES_DIR, name), "r", encoding="utf-8") as handle:
            tables[name[: -len(".json")]] = flatten(json.load(handle))
    return tables


def main():
    tables = load_locales()
    if not tables:
        print("no locale files found under %s" % LOCALES_DIR, file=sys.stderr)
        return 1

    calls = collect_calls()
    if not calls:
        print("no context.log() calls found under %s" % BACKEND_DIR, file=sys.stderr)
        return 1

    problems = []
    for call in calls:
        for language in sorted(tables):
            message = tables[language].get(call.key)
            if message is None:
                problems.append(
                    "%s: no %s message for %s" % (call.where, language, call.code)
                )
                continue
            if call.params is None:
                continue
            for name in sorted(PLACEHOLDER.findall(message)):
                if name not in call.params:
                    problems.append(
                        "%s: %s message for %s uses {{%s}}, which the call does "
                        "not pass" % (call.where, language, call.code, name)
                    )

    if problems:
        print(
            "job message check failed with %d problem(s):" % len(problems),
            file=sys.stderr,
        )
        for problem in problems:
            print("  - %s" % problem, file=sys.stderr)
        return 1

    codes = sorted({call.code for call in calls})
    print(
        "job messages are complete: %d codes in %d call(s), across %s"
        % (len(codes), len(calls), ", ".join(sorted(tables)))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
