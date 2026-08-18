#!/usr/bin/env python3
"""Fake `herdr` executable for adapter tests (#200 rev 3 §REAL-6).

It replays REAL captures (tests/fixtures/herdr/*, taken from a live
Herdr 0.8.0 on the dogfood node) and records every invocation, so tests can
assert both what the adapter parses and what it SENDS — including the total
bytes that would reach a PTY.

Driven by two env vars:
  FAKE_HERDR_SCENARIO  json: {"<command key>": <response> | [<response>, ...]}
  FAKE_HERDR_LOG       jsonl: one line of argv per invocation

A response is either {"fixture": "name.json"} (replayed verbatim, the
default and the honest path), {"stdout": "...", "code": N}, or
{"exit": N, "stdout": ""}. A list is consumed left to right, the last entry
repeating — that is how state transitions (blocked -> done) are modelled
without inventing payloads.
"""
from __future__ import annotations

import json
import os
import sys

# the fake is COPIED onto PATH under the name `herdr`, so its own location
# says nothing about where the captures live — the caller pins them
FIXTURES = os.environ.get("FAKE_HERDR_FIXTURES") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "herdr")


def _key(argv: list[str]) -> str:
    """Command key = the leading non-flag words after the pinned session."""
    words: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg == "--session":
            skip = True
            continue
        if arg.startswith("--"):
            break
        words.append(arg)
        if len(words) == 2:
            break
    return " ".join(words)


def _bump(log: str | None, key: str) -> int:
    """How many times this key was already called (the fake is a fresh
    process each time, so the count lives beside the log)."""
    path = (log or os.path.join(FIXTURES, "..", "fake-herdr")) + ".counts"
    counts = {}
    if os.path.exists(path):
        counts = json.load(open(path, encoding="utf-8"))
    index = counts.get(key, 0)
    counts[key] = index + 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(counts, fh)
    return index


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_HERDR_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(argv) + "\n")

    if "--version" in argv:
        print(open(os.path.join(FIXTURES, "version.txt"), encoding="utf-8").read().strip())
        return 0

    scenario = json.loads(os.environ.get("FAKE_HERDR_SCENARIO") or "{}")
    entry = scenario.get(_key(argv))
    if entry is None:
        sys.stderr.write(f"fake-herdr: no scenario entry for {_key(argv)!r}\n")
        return 64
    if isinstance(entry, list):
        entry = entry[min(_bump(log, _key(argv)), len(entry) - 1)]
    if "fixture" in entry:
        body = open(os.path.join(FIXTURES, entry["fixture"]), encoding="utf-8").read()
        if not body.endswith("\n"):
            body += "\n"
        # measured on the live runtime: results go to stdout with rc 0,
        # structured refusals to STDERR with rc 1. The fake derives the
        # stream from the payload so a test can never accidentally assert a
        # stream split that the real CLI does not have.
        if '"error"' in body:
            sys.stderr.write(body)
            return int(entry.get("code", 1))
        sys.stdout.write(body)
        return int(entry.get("code", 0))
    sys.stdout.write(entry.get("stdout", ""))
    sys.stderr.write(entry.get("stderr", ""))
    return int(entry.get("code", 0))


if __name__ == "__main__":
    sys.exit(main())
