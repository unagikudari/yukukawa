"""Authenticated anti-entropy pull over HTTP (#111 8C) — replication between two REAL nodes.

Stdlib only, like the Operator Console. Two routes:

    GET  /replication/challenge    -> {"nonce": ...}          (single-use, in-process)
    POST /replication/pull         -> {"events": [wire...]}   (only for a verified active peer)

Who may pull: an enrolled, currently-active node — Phase-0 read authorization is fleet
membership; per-scope authorization is exactly what `scope_ref` adds in step 9 (stated
boundary, event-log §5). The client signs `(nonce ‖ frontier ‖ sent_at)` with its node key
(the event-signing Ed25519 profile); the server verifies provenance AND standing against
ITS OWN registries — trust is receiver-local in both directions now — plus a freshness
window and single-use nonce. Unsigned, unknown, rotated, revoked, stale, or replayed
requests are refused with no data (fail-closed on every axis).

The nonce cache is deliberately in-process and per-server (the step-5 posture: a restart is
a new process incarnation and voids outstanding challenges — failing toward re-challenge,
never toward replay: an unknown nonce is refused). The freshness window bounds the replay
exposure of the *challenge*, and the signature covers the frontier so a request cannot be
re-targeted.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import psycopg

from kawa.domain.credential import NodeCredential, PublicKeyRegistry, resolve_and_verify_provenance
from kawa.domain.ids import HLC, digest
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import (
    PullReport,
    Rejection,
    admit_batch,
    frontier,
    read_stream,
    serve_batch,
)
from kawa.storage.wire import WireVerificationError, from_wire, to_wire

FRESHNESS_WINDOW_SECONDS = 60


class PullRefused(RuntimeError):
    """A pull did not produce a report: the server refused it (403 — `reason` carries the
    server's typed refusal) or the transport failed (`reason='unreachable'`). Typed so a
    runtime loop can handle refusal/outage without a raw urllib exception crashing it
    (PR #112 review finding 4); deliberately NOT a PullReport — nothing was admitted or
    rejected event-wise, and dressing an auth failure as an empty report would read as
    'caught up'."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _request_digest(nonce: str, frontier_map: dict[str, int], sent_at: float) -> str:
    """The signed coordinate of one pull request: nonce (single-use), the requester's frontier
    (so a capture cannot be re-targeted), and the send time (freshness bound)."""
    return digest({"nonce": nonce, "frontier": frontier_map, "sent_at": sent_at})


class PullAuthorizer:
    """Server-side gate, separated from HTTP mechanics so tests can drive it deterministically.

    The nonce cache is bounded two ways (PR #112 review finding 3 — a challenge-spam DoS
    otherwise grows it forever): an unconsumed nonce expires with the freshness window (it
    could never authorize a fresh request past that anyway), and a hard cap evicts oldest
    first. Both evictions fail CLOSED — an evicted nonce is simply unknown, forcing a
    re-challenge, never an acceptance."""

    MAX_PENDING_NONCES = 4096

    def __init__(self, keys: PublicKeyRegistry, trust: TrustRegistry,
                 window_seconds: int = FRESHNESS_WINDOW_SECONDS) -> None:
        self.keys = keys
        self.trust = trust
        self.window = window_seconds
        self._nonces: dict[str, float] = {}   # nonce -> issued_at; insertion-ordered
        self._lock = Lock()

    def _prune(self, now: float) -> None:
        expired = [n for n, t in self._nonces.items() if now - t > self.window]
        for n in expired:
            del self._nonces[n]
        while len(self._nonces) > self.MAX_PENDING_NONCES:
            self._nonces.pop(next(iter(self._nonces)))   # oldest-first; eviction fails closed

    def challenge(self, *, now: float | None = None) -> str:
        t = time.time() if now is None else now
        nonce = os.urandom(16).hex()
        with self._lock:
            self._nonces[nonce] = t
            self._prune(t)     # after insert, so the cap holds even for this call's nonce
        return nonce

    def authorize(self, body: dict, *, now: float | None = None) -> str | None:
        """None when authorized; otherwise the typed refusal reason. Every axis fails closed."""
        for field in ("node", "key_ref", "scheme", "nonce", "sent_at", "frontier", "signature"):
            if field not in body:
                return "malformed"
        t0 = time.time() if now is None else now
        with self._lock:
            self._prune(t0)
            if body["nonce"] not in self._nonces:
                return "nonce_unknown_or_replayed"    # single-use: unknown and reused look identical
            del self._nonces[body["nonce"]]
        if abs(t0 - float(body["sent_at"])) > self.window:
            return "stale"
        dg = _request_digest(body["nonce"], body["frontier"], body["sent_at"])
        if not resolve_and_verify_provenance(dg, body["signature"], body["key_ref"],
                                             body["scheme"], self.keys):
            return "provenance_invalid"
        if self.trust.standing(body["key_ref"]) != "active":
            return f"trust_{self.trust.standing(body['key_ref'])}"
        if self.trust.node_ref(body["key_ref"]) != body["node"]:
            return "forged_origin"                    # a trusted key may not pull as another node
        return None


def serve(dsn: str, authorizer: PullAuthorizer, host: str = "127.0.0.1",
          port: int = 0) -> ThreadingHTTPServer:
    """Start the replication server (returns it; caller owns shutdown). port=0 picks a free port."""

    class _Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/replication/challenge":
                self._json(404, {"error": "no such route"})
                return
            self._json(200, {"nonce": authorizer.challenge()})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/replication/pull":
                self._json(404, {"error": "no such route"})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            except Exception:
                self._json(400, {"error": "malformed"})
                return
            refusal = authorizer.authorize(body)
            if refusal is not None:
                self._json(403, {"error": refusal})   # fail-closed: no data on any refusal
                return
            with psycopg.connect(dsn) as conn:
                events = read_stream(conn, {k: int(v) for k, v in body["frontier"].items()})
            # node boundary: withhold per the step-9a rule (all v2 — grants arrive in 9b)
            self._json(200, {"events": [to_wire(e) for e in serve_batch(events)]})

        def log_message(self, *a: object) -> None:  # quiet
            pass

    httpd = ThreadingHTTPServer((host, port), _Handler)
    return httpd


def pull_http(dest: psycopg.Connection, base_url: str, *, credential: NodeCredential,
              keys: PublicKeyRegistry, trust: TrustRegistry,
              clock: HLC | None = None) -> PullReport:
    """One anti-entropy pull over the wire: challenge → signed frontier request → verify each
    event over its received bytes (`from_wire`) → the SAME trust-gated `admit_batch` as
    in-process replication. Transport adds reachability, never leniency: a wire-verification
    failure is a typed Rejection, and everything downstream is unchanged admission."""
    try:
        with urllib.request.urlopen(base_url + "/replication/challenge") as resp:
            nonce = json.loads(resp.read())["nonce"]
        frontier_map = frontier(dest)
        sent_at = time.time()
        dg = _request_digest(nonce, frontier_map, sent_at)
        body = json.dumps({
            "node": credential.node_ref, "key_ref": credential.signing_key_ref,
            "scheme": credential.signature_scheme, "nonce": nonce, "sent_at": sent_at,
            "frontier": frontier_map, "signature": credential.sign(dg),
        }).encode("utf-8")
        req = urllib.request.Request(base_url + "/replication/pull", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        try:
            reason = json.loads(err.read()).get("error", f"http_{err.code}")
        except Exception:
            reason = f"http_{err.code}"
        raise PullRefused(reason) from err
    except urllib.error.URLError as err:
        raise PullRefused("unreachable") from err
    batch = []
    wire_rejected: list[Rejection] = []
    for obj in payload["events"]:
        try:
            batch.append(from_wire(obj))
        except (WireVerificationError, KeyError, ValueError):
            env = obj.get("envelope", {}) if isinstance(obj, dict) else {}
            wire_rejected.append(Rejection(
                str(env.get("event_id", "?")), str(env.get("origin_node", "?")),
                int(env.get("origin_seq", 0) or 0), "wire_invalid"))
    report = admit_batch(dest, batch, keys=keys, trust=trust, clock=clock)
    return PullReport(admitted=report.admitted, rejected=wire_rejected + report.rejected)
