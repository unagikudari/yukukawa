"""Deterministic reachability probe collector: `node_reachable` Observations
from an operator-owned allowlist (plan-fleet-telemetry #189 rev 3, step 3).

Usage:
  KAWA_DSN=dbname=kawa KAWA_NODE=<node> python scripts/collect_node_probe.py

Targets come ONLY from a git-ignored operator config of `label = "url"` pairs
(#189 §D — the frozen probe security boundary):

  * the LABEL is an operator-chosen safe string and is the ONLY part that
    enters the qualifier / projection / UI;
  * the URL is an absolute http(s) address and stays in `source_ref` inside
    the log (deliberate: runtime Situation Awareness belongs in the deployed
    instance, not the repository);
  * no hostname synthesis, no discovery, no following user/DB-provided
    values — the SSRF surface is the operator's own list and nothing else.

Config location: $KAWA_PROBE_TARGETS, default ~/.kawa/probe_targets.conf.
Format (TOML-style pairs, frozen by round-2): one `label = "https://…"` per
line; `#` comments and blank lines allowed.

Failure semantics (§B applied to probes):

  * config ABSENT            -> absence: nothing emitted, exit 0 (a node with
    no probe duty has made no reachability claim — indistinguishable from
    never having probed, by construction)
  * config MALFORMED         -> the ENTIRE run is refused, exit non-zero,
    nothing emitted. The config is a security allowlist: a partially-trusted
    allowlist is not an allowlist, so there is no per-line salvage here
    (unlike df's per-qualifier skip, where targets are independent facts).
  * probe target UNREACHABLE -> value_bool=False. This is a MEASUREMENT
    (refusal observed), never a collector failure and never absence.
  * unexpected internal error -> loud oneshot failure (raise), nothing
    defaulted.

Reachable means: an HTTP status line was received from the target — any
status code counts (a 500 or a redirect is a live listener answering).
Redirects are never followed (§D); the 3xx response itself is the evidence.
Unreachable means: DNS failure, connection refused/reset, TLS failure, or
timeout. No auth headers are ever sent; the body is read capped and
discarded (status line + latency are the observation, §D).

occurred_at = probe completion time (§C — a probe HAS a transport gap, so
completion is the honest instant); fetched_at == occurred_at (the collector
learns the result at the same instant it exists). The label travels as the
fixed-form TRAILING `qualifier=<label>` field of source_revision, same
convention as the telemetry/goatcounter collectors.
"""
from __future__ import annotations

import datetime
import errno
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from kawa import nodehealth
from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.projections.facet_registry import REGISTRY
from kawa.storage.db import connect

_DEFAULT_CONFIG = "~/.kawa/probe_targets.conf"
_TIMEOUT_S = 5.0
_BODY_CAP = 2048
# operator-chosen safe string: never a URL fragment, never whitespace — the
# trailing-qualifier capture and the projection PK both rely on this shape
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LINE_RE = re.compile(r'^([^=\s]+)\s*=\s*"([^"]*)"\s*$')

assert REGISTRY["node_reachable"].method == "http_probe"  # frozen §A mirror


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """§D: redirects are never followed. Returning None makes urllib surface
    the 3xx as HTTPError — which the probe counts as a RECEIVED status line."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class ProbeResult(object):
    def __init__(self, label: str, url: str, reachable: bool, evidence: str,
                 occurred_at: str):
        self.label, self.url, self.reachable = label, url, reachable
        self.evidence, self.occurred_at = evidence, occurred_at


def parse_targets(text: str) -> dict[str, str]:
    """Fail-closed allowlist parse: ANY invalid line refuses the whole config."""
    targets: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            raise ValueError(f"line {lineno}: not a `label = \"url\"` pair")
        label, url = m.group(1), m.group(2)
        if not _LABEL_RE.match(label):
            raise ValueError(f"line {lineno}: label {label!r} is not an operator-safe "
                             "string ([A-Za-z0-9._-], must not start with . _ -)")
        if label in targets:
            raise ValueError(f"line {lineno}: duplicate label {label!r}")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"line {lineno}: URL for {label!r} is not an absolute "
                             "http(s) address")
        if parsed.username or parsed.password:
            # §D: source_ref persists the URL verbatim in the log — embedded
            # credentials would be durably exposed there (review A-1). The
            # probe never authenticates, so such a URL is always a mistake.
            raise ValueError(f"line {lineno}: URL for {label!r} embeds credentials "
                             "(user:pass@…) — refused, the URL is persisted in "
                             "source_ref and the probe never authenticates")
        if parsed.query or parsed.fragment:
            # round-2 advisory: `?token=…` would leak into source_ref the same
            # way. A liveness target needs no query — refuse the whole shape
            # rather than judge which parameters are secrets.
            raise ValueError(f"line {lineno}: URL for {label!r} carries a query/"
                             "fragment — refused, probe targets are bare "
                             "scheme://host[:port]/path liveness endpoints")
        targets[label] = url
    return targets


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# local-resource exhaustion on the PROBING node: measuring this as the
# TARGET's refusal would be a false CRIT about a node that was never reached
# (review B-1; EADDRNOTAVAIL = ephemeral-port exhaustion, round-2 advisory) —
# these errnos make the run a loud collector failure instead
_LOCAL_ERRNOS = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOBUFS,
                           errno.ENOMEM, errno.EADDRNOTAVAIL})


def _raise_if_local_failure(exc: BaseException) -> None:
    reason = getattr(exc, "reason", exc)            # URLError wraps the OSError
    if isinstance(reason, OSError) and reason.errno in _LOCAL_ERRNOS:
        raise RuntimeError(f"probing node resource failure, not a measurement: "
                           f"{reason}") from exc


def probe(label: str, url: str) -> ProbeResult:
    """One reachability measurement. Expected TRANSPORT failures are the
    False measurement; local-resource failures and anything unexpected
    propagate (loud oneshot, §B: never a defaulted value)."""
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "kawa-probe"})
    start = time.monotonic()
    try:
        with opener.open(req, timeout=_TIMEOUT_S) as resp:
            resp.read(_BODY_CAP)                    # capped and discarded (§D)
            status: int | None = resp.status
        reachable, detail = True, f"status={status}"
    except urllib.error.HTTPError as exc:           # non-2xx INCLUDING unfollowed 3xx:
        exc.read(_BODY_CAP)                         # a status line was received
        exc.close()
        reachable, detail = True, f"status={exc.code}"
    except (urllib.error.URLError, TimeoutError, socket.timeout,
            ConnectionError, ssl.SSLError) as exc:
        _raise_if_local_failure(exc)
        reason = getattr(exc, "reason", exc)
        reachable, detail = False, f"error={type(exc).__name__}: {str(reason)[:120]}"
    latency_ms = int((time.monotonic() - start) * 1000)
    return ProbeResult(label, url, reachable,
                       f"{detail} latency_ms={latency_ms}", _now())


def main() -> int:
    node = os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0]
    config_path = os.path.expanduser(
        os.environ.get("KAWA_PROBE_TARGETS") or _DEFAULT_CONFIG)
    status: dict = {"ts": _now(), "node": node, "config": config_path}

    if not os.path.exists(config_path):
        status["result"] = "no probe config (absence, nothing emitted)"
        _write_status(status)
        return 0
    try:
        targets = parse_targets(open(config_path, encoding="utf-8").read())
    except ValueError as exc:
        status["result"] = f"config refused (fail-closed allowlist): {exc}"
        _write_status(status)
        print(json.dumps(status, indent=2), file=sys.stderr)
        return 2
    if not targets:
        status["result"] = "config has no targets (absence, nothing emitted)"
        _write_status(status)
        return 0

    results = [probe(label, url) for label, url in sorted(targets.items())]

    cred = load_or_create_local_node(os.path.expanduser("~/.kawa/node_credential.json"),
                                     node_ref=node)
    keys = PublicKeyRegistry(os.path.expanduser("~/.kawa/keys.json"))
    keys.register(cred.signing_key_ref, cred.public_pem())
    with connect() as conn:
        k = Kawa(conn, identity=IdentityContext.from_local_node(
            cred, actor_ref="probe-collector"))
        for r in results:
            k.record_observation(
                "node_reachable", value_bool=r.reachable, method="http_probe",
                occurred_at=r.occurred_at,              # completion time (§C)
                source_ref=r.url,                       # URL log-only (§D)
                source_revision=f"tool=http_probe qualifier={r.label}",
                content_digest="sha256:" + hashlib.sha256(r.evidence.encode()).hexdigest(),
                fetched_at=r.occurred_at)
        conn.commit()
    status["probed"] = {r.label: {"reachable": r.reachable, "evidence": r.evidence}
                       for r in results}
    status["emitted"] = len(results)
    _write_status(status)
    print(json.dumps(status, indent=2))
    return 0


def _write_status(status: dict) -> None:
    status_file = os.path.join(nodehealth.status_dir(), "probe.status")
    os.makedirs(os.path.dirname(status_file), exist_ok=True)
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)


if __name__ == "__main__":
    sys.exit(main())
