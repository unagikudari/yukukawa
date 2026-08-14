"""Step 9b acceptance (#113 rev 2 (c) + r2 BC-iii/BC-v): scope grants, the offer/retain
algebra, the least-visible flip, and the fleet default end-to-end.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.events import PlanCreated
from kawa.domain.identity import IdentityContext
from kawa.domain.ids import scope_digest_of
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import admit_batch, pull, read_stream, serve_batch
from kawa.adapters.replication_http import PullAuthorizer, pull_http, serve

psycopg = pytest.importorskip("psycopg")

from tests.test_replication import _ALL  # noqa: E402


def _fresh(dsn_env: str, default: str):  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get(dsn_env, default), autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    return c


@pytest.fixture()
def conn_a():  # type: ignore[no-untyped-def]
    c = _fresh("KAWA_TEST_DSN_A", "dbname=kawa_test_a")
    yield c
    c.close()


@pytest.fixture()
def conn_b():  # type: ignore[no-untyped-def]
    c = _fresh("KAWA_TEST_DSN_B", "dbname=kawa_test_b")
    yield c
    c.close()


@pytest.fixture()
def mesh(conn_a, conn_b, tmp_path):  # type: ignore[no-untyped-def]
    """Two nodes wired the 9b way: node credentials, cross-enrollment (which grants `fleet`
    by default — BC-iii), and a FLEET-DEFAULT emitter on A (the flipped configuration)."""
    cred_a = load_or_create_local_node(str(tmp_path / "a.json"), node_ref="node-a")
    cred_b = load_or_create_local_node(str(tmp_path / "b.json"), node_ref="node-b")
    kawa_a = Kawa(conn_a, identity=IdentityContext.from_local_node(cred_a, actor_ref="agent-a"))
    assert kawa_a.default_scope == "fleet"               # the BC-iii emitter half, by default
    trust_a = TrustRegistry(str(tmp_path / "a-trust.json"))   # A's server-side registries
    keys_a = PublicKeyRegistry(str(tmp_path / "a-keys.json"))
    keys_a.register(cred_b.signing_key_ref, cred_b.public_pem())
    trust_a.enroll("node-b", cred_b.signing_key_ref)          # grants fleet by default
    trust_b = TrustRegistry(str(tmp_path / "b-trust.json"))   # B's receiver-side registries
    keys_b = PublicKeyRegistry(str(tmp_path / "b-keys.json"))
    keys_b.register(cred_a.signing_key_ref, cred_a.public_pem())
    trust_b.enroll("node-a", cred_a.signing_key_ref)
    return dict(kawa_a=kawa_a, cred_a=cred_a, cred_b=cred_b,
                trust_a=trust_a, keys_a=keys_a, trust_b=trust_b, keys_b=keys_b)


def _payload_state(conn, event_id):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT materialized FROM events WHERE event_id=%s", (event_id,))
        row = cur.fetchone()
        return None if row is None else row[0]


def _plans(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref FROM current_plans ORDER BY plan_ref")
        return [r[0] for r in cur.fetchall()]


def test_enrollment_grants_fleet_and_defaults_replicate_fully(conn_a, conn_b, mesh) -> None:  # type: ignore[no-untyped-def]
    """BC-iii end-to-end: flipped emitters + default enrollment grants = the dogfood keeps
    replicating, now as v2 fleet-scoped payloads instead of v1."""
    m = mesh
    assert m["trust_a"].scope_grants("node-b") == frozenset({"fleet"})
    ev = m["kawa_a"].create_plan("p1", "kawa", "flipped default")
    assert ev.envelope_version == 2 and ev.scope_ref == "fleet"
    conn_a.commit()
    r = pull(conn_b, conn_a, keys=m["keys_b"], trust=m["trust_b"],
             source_trust=m["trust_a"], puller_node="node-b", scopes=("fleet",))
    assert r.rejected == [] and r.admitted == [ev.event_id]
    assert _payload_state(conn_b, ev.event_id) is True and _plans(conn_b) == ["p1"]


def test_offer_retain_matrix(conn_a, conn_b, mesh) -> None:  # type: ignore[no-untyped-def]
    """granted ∩ requested, all four disagreement directions, every mismatch = stub-not-gap."""
    m = mesh
    ka = m["kawa_a"]
    e_fleet = ka.create_plan("p-fleet", "kawa", "granted+requested")
    e_s1 = ka._emit_reduce(PlanCreated(plan_ref="p-s1", project_ref="kawa", objective="granted, unrequested"),
                           scope_ref="s1")
    e_s2 = ka._emit_reduce(PlanCreated(plan_ref="p-s2", project_ref="kawa", objective="ungranted"),
                           scope_ref="s2")
    conn_a.commit()
    m["trust_a"].grant_scope("node-b", "s1")             # granted but B won't request it
    r = pull(conn_b, conn_a, keys=m["keys_b"], trust=m["trust_b"],
             source_trust=m["trust_a"], puller_node="node-b", scopes=("fleet", "s2"))
    # s2: requested but ungranted -> server withholds -> stub, no receiver complaint
    # s1: granted but unrequested -> offer = granted ∩ requested excludes it -> stub
    # fleet: granted+requested -> full
    assert r.rejected == []
    assert _payload_state(conn_b, e_fleet.event_id) is True
    assert _payload_state(conn_b, e_s1.event_id) is False
    assert _payload_state(conn_b, e_s2.event_id) is False
    assert _plans(conn_b) == ["p-fleet"]                 # chain intact, projections granted-only


def test_receiver_defense_in_depth_unrequested_payload(conn_a, conn_b, mesh) -> None:  # type: ignore[no-untyped-def]
    """A malicious/misconfigured server ships an s1 payload B never requested: B drops the
    payload, admits the envelope as a stub, reports scope_unrequested — chain never breaks."""
    m = mesh
    e_s1 = m["kawa_a"]._emit_reduce(
        PlanCreated(plan_ref="p-s1", project_ref="kawa", objective="pushed"), scope_ref="s1")
    conn_a.commit()
    full_stream = read_stream(conn_a, {})                # NO serve_batch: simulates the bad server
    r = admit_batch(conn_b, full_stream, keys=m["keys_b"], trust=m["trust_b"],
                    requested_scopes=frozenset({"fleet"}))
    assert [x.reason for x in r.rejected] == ["scope_unrequested"]
    assert _payload_state(conn_b, e_s1.event_id) is False     # envelope admitted, as a stub
    assert _plans(conn_b) == []
    # ...and the same defense guards the UPGRADE path
    r2 = admit_batch(conn_b, full_stream, keys=m["keys_b"], trust=m["trust_b"],
                     requested_scopes=frozenset({"fleet"}))
    assert [x.reason for x in r2.rejected] == ["scope_unrequested"]
    assert _payload_state(conn_b, e_s1.event_id) is False
    # a later honest, requested pull upgrades it
    m["trust_a"].grant_scope("node-b", "s1")
    r3 = pull(conn_b, conn_a, keys=m["keys_b"], trust=m["trust_b"],
              source_trust=m["trust_a"], puller_node="node-b", scopes=("fleet", "s1"))
    assert r3.admitted == [e_s1.event_id]
    assert _payload_state(conn_b, e_s1.event_id) is True and _plans(conn_b) == ["p-s1"]


def test_scope_digest_mismatch_full_reject(conn_a, conn_b, mesh) -> None:  # type: ignore[no-untyped-def]
    m = mesh
    e = m["kawa_a"].create_plan("p1", "kawa", "fleet")
    conn_a.commit()
    [ev] = read_stream(conn_a, {})
    lying = ev.model_copy(update={"scope_ref": "s-other"})    # cleartext lies about the digest
    r = admit_batch(conn_b, [lying], keys=m["keys_b"], trust=m["trust_b"],
                    requested_scopes=frozenset({"s-other"}))
    assert [x.reason for x in r.rejected] == ["scope_digest_mismatch"]
    assert _payload_state(conn_b, e.event_id) is None         # nothing admitted


def test_unscoped_v2_is_node_local_bc_v(conn_a, conn_b, mesh) -> None:  # type: ignore[no-untyped-def]
    """BC-v normative: unscoped v2 = node-local materialization; envelope-only replication;
    NO grant can ever name it — the payload is reachable only at its origin."""
    m = mesh
    ev = m["kawa_a"]._emit_reduce(
        PlanCreated(plan_ref="p-local", project_ref="kawa", objective="private"),
        scope_ref=None, envelope_version=2)
    assert ev.envelope_version == 2 and ev.scope_digest is None
    assert _plans(conn_a) == ["p-local"]                 # materialized at the origin
    conn_a.commit()
    m["trust_a"].grant_scope("node-b", "fleet")          # no grant can reach an unscoped event
    r = pull(conn_b, conn_a, keys=m["keys_b"], trust=m["trust_b"],
             source_trust=m["trust_a"], puller_node="node-b", scopes=("fleet",))
    assert r.rejected == []
    assert _payload_state(conn_b, ev.event_id) is False and _plans(conn_b) == []


def test_scope_revocation_is_forward_only(conn_a, conn_b, mesh) -> None:  # type: ignore[no-untyped-def]
    m = mesh
    e1 = m["kawa_a"].create_plan("p1", "kawa", "before revocation")
    conn_a.commit()
    pull(conn_b, conn_a, keys=m["keys_b"], trust=m["trust_b"],
         source_trust=m["trust_a"], puller_node="node-b", scopes=("fleet",))
    assert _payload_state(conn_b, e1.event_id) is True
    m["trust_a"].revoke_scope("node-b", "fleet")
    e2 = m["kawa_a"].create_plan("p2", "kawa", "after revocation")
    conn_a.commit()
    r = pull(conn_b, conn_a, keys=m["keys_b"], trust=m["trust_b"],
             source_trust=m["trust_a"], puller_node="node-b", scopes=("fleet",))
    assert r.rejected == []
    assert _payload_state(conn_b, e2.event_id) is False       # future flow stopped
    assert _payload_state(conn_b, e1.event_id) is True        # held payloads stay — knowledge
    assert _plans(conn_b) == ["p1"]


def test_http_end_to_end_grants_and_metadata_boundary(conn_a, conn_b, mesh, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The wire path: fleet flows full, s-secret crosses digest-only; the response body names
    no scope identifier beyond what was requested (§12.4 metadata boundary)."""
    m = mesh
    e_fleet = m["kawa_a"].create_plan("p-fleet", "kawa", "shared")
    e_sec = m["kawa_a"]._emit_reduce(
        PlanCreated(plan_ref="p-sec", project_ref="kawa", objective="secret"), scope_ref="s-secret")
    conn_a.commit()
    dsn_a = os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a")
    authorizer = PullAuthorizer(m["keys_a"], m["trust_a"])
    httpd = serve(dsn_a, authorizer)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        r = pull_http(conn_b, base, credential=m["cred_b"], keys=m["keys_b"],
                      trust=m["trust_b"], scopes=("fleet",))
        assert r.rejected == []
        assert _payload_state(conn_b, e_fleet.event_id) is True
        assert _payload_state(conn_b, e_sec.event_id) is False
        assert _plans(conn_b) == ["p-fleet"]
        # metadata boundary: the withheld event's scope identifier appears nowhere on the wire —
        # only its pseudonymous digest does
        with conn_b.cursor() as cur:
            cur.execute("SELECT scope_ref, scope_digest FROM events WHERE event_id=%s",
                        (e_sec.event_id,))
            sref, sdig = cur.fetchone()
        assert sref is None and sdig == scope_digest_of("s-secret")
        # backfill over the wire (BC-ii): grant + request the scope later — the held stub
        # upgrades through a plain pull, even though the frontier already counts it
        m["trust_a"].grant_scope("node-b", "s-secret")
        r2 = pull_http(conn_b, base, credential=m["cred_b"], keys=m["keys_b"],
                       trust=m["trust_b"], scopes=("fleet", "s-secret"))
        assert r2.admitted == [e_sec.event_id] and r2.rejected == []
        assert _payload_state(conn_b, e_sec.event_id) is True
        assert _plans(conn_b) == ["p-fleet", "p-sec"]
    finally:
        httpd.shutdown()
