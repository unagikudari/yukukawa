"""Step-3 probe tests (#189 rev 3): fail-closed allowlist parsing (§D),
probe reachability semantics against REAL local sockets (a live listener,
an unfollowed redirect, a refused connection — measurements, not mocks),
and RCH consumption in the fleet_node reducer.

The collector's DB path is exercised through the reducer tests' Kawa API
emissions; main()'s pre-DB exits (absent/malformed config) are tested with
HOME pointed at a tmpdir so no real ~/.kawa or DSN is ever touched."""
from __future__ import annotations

import http.server
import os
import socket
import sys
import threading

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.console_rollup import refresh_console_projections

psycopg = pytest.importorskip("psycopg")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import collect_node_probe as cp  # noqa: E402

_TABLES = ("content_embedding, event_content, events, event_links, event_link, "
           "event_observation, event_claim, event_plan, event_work, "
           "event_work_dependency, event_work_retired, event_result, "
           "current_claim_standing, current_plans, current_work, "
           "current_work_dependency, runtime_work_occupancy, work_dispatch, "
           "situation_rollup, fleet_node, evidence_provenance, projection_state, "
           "fleet_node_facet")


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_TABLES}")
    c.commit()
    yield c
    c.rollback()
    c.close()


# ---- §D config parsing: fail-closed allowlist ----

def test_parse_targets_accepts_pairs_comments_blanks():  # type: ignore[no-untyped-def]
    text = ('# fleet probes\n'
            '\n'
            'evo = "https://evo.example:8099/"\n'
            'mint.node = "http://mint.example:8099/health"\n')
    assert cp.parse_targets(text) == {"evo": "https://evo.example:8099/",
                                      "mint.node": "http://mint.example:8099/health"}


@pytest.mark.parametrize("bad", [
    'evo https://x/',                          # not a pair
    'evo = https://x/',                        # unquoted
    '../etc = "https://x/"',                   # unsafe label charset
    'evo = "ftp://x/"',                        # non-http(s) scheme
    'evo = "/relative/path"',                  # not absolute
    'evo = "https://a/"\nevo = "https://b/"',  # duplicate label
    'evo = "https://user:secret@x/"',          # embedded credentials (review A-1):
    'evo = "https://token@x/"',                # source_ref persists the URL verbatim
    'evo = "https://x/health?token=abc"',      # query-string secrets (round-2 advisory)
    'evo = "https://x/#frag"',                 # fragment — same bare-endpoint rule
])
def test_parse_targets_is_fail_closed(bad):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        cp.parse_targets(bad)


def test_parse_targets_accepts_ipv6_literal():  # type: ignore[no-untyped-def]
    assert cp.parse_targets('v6 = "http://[::1]:8099/health"\n') == {
        "v6": "http://[::1]:8099/health"}


# ---- probe semantics against real sockets (real measurements) ----

class _Handler(http.server.BaseHTTPRequestHandler):
    requests: list = []

    def do_GET(self):  # type: ignore[no-untyped-def]
        _Handler.requests.append(self.path)
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/followed")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    def log_message(self, *a):  # type: ignore[no-untyped-def]
        pass


@pytest.fixture()
def live_server():  # type: ignore[no-untyped-def]
    _Handler.requests = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_probe_live_listener_is_reachable(live_server):  # type: ignore[no-untyped-def]
    r = cp.probe("t", live_server + "/")
    assert r.reachable is True
    assert "status=200" in r.evidence and "latency_ms=" in r.evidence


def test_probe_redirect_is_reachable_and_never_followed(live_server):  # type: ignore[no-untyped-def]
    r = cp.probe("t", live_server + "/redirect")
    assert r.reachable is True                      # a 3xx IS a received status line
    assert "status=302" in r.evidence
    assert _Handler.requests == ["/redirect"]       # §D: the Location was not fetched


def test_probe_refused_connection_is_measured_false():  # type: ignore[no-untyped-def]
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                       # nothing listens here now
    r = cp.probe("t", f"http://127.0.0.1:{port}/")
    assert r.reachable is False                     # measurement, not absence
    assert r.evidence.startswith("error=")


def test_probe_tls_failure_is_measured_false(live_server):  # type: ignore[no-untyped-def]
    # https:// against a REAL plaintext listener: the TLS handshake fails
    # before any status line exists — a transport refusal, measured False
    r = cp.probe("t", live_server.replace("http://", "https://") + "/")
    assert r.reachable is False
    assert r.evidence.startswith("error=")


def test_probe_hung_listener_times_out_as_measured_false(monkeypatch):  # type: ignore[no-untyped-def]
    # REAL socket that accepts and then never speaks HTTP
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setattr(cp, "_TIMEOUT_S", 0.3)
    try:
        r = cp.probe("t", f"http://127.0.0.1:{srv.getsockname()[1]}/")
    finally:
        srv.close()
    assert r.reachable is False                     # timeout is a transport refusal
    assert r.evidence.startswith("error=")


@pytest.mark.parametrize("err", [
    OSError(24, "Too many open files"),                  # EMFILE
    OSError(105, "No buffer space available"),           # ENOBUFS
    OSError(99, "Cannot assign requested address"),      # EADDRNOTAVAIL (round-2)
])
def test_local_resource_exhaustion_is_collector_failure_not_crit(monkeypatch, err):  # type: ignore[no-untyped-def]
    # review B-1: prober-side resource exhaustion must never be recorded as
    # the target's refusal. Injected (errno exhaustion is not reproducible safely).
    import urllib.error
    import urllib.request as ur

    class _Opener:
        def open(self, req, timeout=None):  # type: ignore[no-untyped-def]
            raise urllib.error.URLError(err)
    monkeypatch.setattr(ur, "build_opener", lambda *h: _Opener())
    with pytest.raises(RuntimeError, match="resource failure"):
        cp.probe("t", "http://127.0.0.1:1/")


# ---- main() pre-DB exits: absence vs fail-closed refusal ----

def test_absent_config_is_absence_exit_zero(tmp_path, monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KAWA_PROBE_TARGETS", str(tmp_path / "nope.conf"))
    monkeypatch.setenv("KAWA_NODE", "test")
    assert cp.main() == 0                           # nothing emitted, not an error


def test_malformed_config_refuses_whole_run(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "probe_targets.conf"
    cfg.write_text('good = "https://x.example/"\nbad line\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KAWA_PROBE_TARGETS", str(cfg))
    monkeypatch.setenv("KAWA_NODE", "test")
    assert cp.main() == 2                           # no per-line salvage of an allowlist


# ---- RCH consumption in refresh_fleet_node (#189 step 3) ----

def _emit_reach(k, label, reachable, occurred="2026-08-18T10:00:00Z"):  # type: ignore[no-untyped-def]
    return k.record_observation(
        "node_reachable", value_bool=reachable, method="http_probe",
        occurred_at=occurred, source_ref="https://target.example:8099/",
        source_revision=f"tool=http_probe qualifier={label}",
        content_digest="sha256:0", fetched_at=occurred)


def test_matching_label_sets_reachability_ok(conn):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-probe"))
    _emit_reach(k, "test", True)
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT reachability, reachability_source, reachability_as_of "
                    "FROM fleet_node WHERE node_ref='test'")
        rch, src, as_of = cur.fetchone()
    assert rch == "OK"
    assert src == "observation:node_reachable label=test"   # named source (schema CHECK)
    assert as_of is not None


def test_false_probe_is_crit_and_stales_siblings_at_render(conn):  # type: ignore[no-untyped-def]
    from kawa.console.render import render
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-probe"))
    _emit_reach(k, "test", False)
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT reachability FROM fleet_node WHERE node_ref='test'")
        assert cur.fetchone()[0] == "CRIT"          # a measured refusal, never absence
    page = render(conn, "/fleet")
    assert "RCH CRIT" in page
    assert "STALE" in page                          # phase-1 rule: CRIT stales siblings


def test_unmatched_label_updates_no_node(conn):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-probe"))
    _emit_reach(k, "not-a-node", False)
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT reachability FROM fleet_node")
        assert {r[0] for r in cur.fetchall()} == {"UNKNOWN"}   # no invented mapping
        cur.execute("SELECT qualifier FROM fleet_node_facet WHERE predicate='node_reachable'")
        assert cur.fetchall() == [("not-a-node",)]  # still visible as a facet


def test_latest_probe_wins(conn):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-probe"))
    _emit_reach(k, "test", True, occurred="2026-08-18T10:00:00Z")
    _emit_reach(k, "test", False, occurred="2026-08-18T11:00:00Z")   # later refusal
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT reachability FROM fleet_node WHERE node_ref='test'")
        assert cur.fetchone()[0] == "CRIT"          # §C latest-wins, applied once in the facet


def test_multi_prober_same_label_is_deterministic_latest_wins(conn):  # type: ignore[no-untyped-def]
    # review C-1: the facet PK includes the PROBING node, so two probers of
    # one label are TWO facet rows — the reducer must re-apply latest-wins
    # across them, never scan-order-pick. The stale prober says True, the
    # newer one says False: CRIT must win, and win again on every refresh.
    k_a = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-probe"))
    k_b = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="other", actor_ref="pytest-probe"))
    _emit_reach(k_a, "test", True, occurred="2026-08-18T10:00:00Z")
    _emit_reach(k_b, "test", False, occurred="2026-08-18T11:00:00Z")
    conn.commit()
    for _ in range(3):                              # determinism across refreshes
        refresh_console_projections(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT reachability FROM fleet_node WHERE node_ref='test'")
            assert cur.fetchone()[0] == "CRIT"
            cur.execute("SELECT count(*) FROM fleet_node_facet "
                        "WHERE predicate='node_reachable' AND qualifier='test'")
            assert cur.fetchone()[0] == 2           # both probers stay visible as facets


def test_exact_timestamp_tie_breaks_on_node_ref_deterministically(conn):  # type: ignore[no-untyped-def]
    # round-2 advisory: with occurred_at AND fetched_at exactly equal, the
    # remaining chain is node_ref DESC — 'test' > 'other', so the 'test'
    # prober's refusal must win, every time, regardless of insert order.
    k_a = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="other", actor_ref="pytest-probe"))
    k_b = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-probe"))
    same = "2026-08-18T12:00:00Z"
    _emit_reach(k_a, "test", True, occurred=same)   # inserted first…
    _emit_reach(k_b, "test", False, occurred=same)  # …but node_ref DESC wins
    conn.commit()
    for _ in range(3):
        refresh_console_projections(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT reachability FROM fleet_node WHERE node_ref='test'")
            assert cur.fetchone()[0] == "CRIT"
