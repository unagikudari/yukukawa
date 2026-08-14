"""Step 9a acceptance (#113 rev 2 (a)/(b) + r2 BC-ii): envelope v2, stubs, atomic upgrade.

Two-store layout like test_replication.py; skips when the test DBs are absent.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.events import Event, PlanCreated
from kawa.domain.identity import IdentityContext
from kawa.domain.ids import event_hash, scope_digest_of
from kawa.domain.trust import TrustRegistry
from kawa.projections.reducers import rebuild
from kawa.storage.replication import admit_batch, pull, read_stream, serve_batch
from kawa.storage.wire import WireVerificationError, from_wire, to_wire

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
def node_a(conn_a, tmp_path):  # type: ignore[no-untyped-def]
    cred = load_or_create_local_node(str(tmp_path / "node-a.json"), node_ref="node-a")
    kawa = Kawa(conn_a, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a"), default_scope=None)
    keys = PublicKeyRegistry(str(tmp_path / "b-keys.json"))
    trust = TrustRegistry(str(tmp_path / "b-trust.json"))
    keys.register(cred.signing_key_ref, cred.public_pem())
    trust.enroll("node-a", cred.signing_key_ref)
    return kawa, cred, keys, trust


def _stub_state(conn, event_id):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT materialized, scope_ref, scope_digest FROM events WHERE event_id=%s",
                    (event_id,))
        return cur.fetchone()


def _plans(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref FROM current_plans ORDER BY plan_ref")
        return [r[0] for r in cur.fetchall()]


# ---------------- (a): preimages, downgrade triple-negative, single derivation ----------------

def test_downgrade_triple_negative(conn_a, node_a) -> None:  # type: ignore[no-untyped-def]
    kawa, _, _, _ = node_a
    v2 = kawa._emit_reduce(PlanCreated(plan_ref="p2", project_ref="kawa", objective="scoped"),
                           scope_ref="s1")
    assert v2.envelope_version == 2 and v2.scope_digest == scope_digest_of("s1") and v2.verify()
    # v2 re-presented as v1 (version stripped, scope dropped): different preimage -> fails
    as_v1 = v2.model_copy(update={"envelope_version": 1, "scope_ref": None, "scope_digest": None})
    assert not as_v1.verify()
    # v1 re-presented as v2: different preimage -> fails
    v1 = kawa.create_plan("p1", "kawa", "legacy")
    assert v1.envelope_version == 1 and v1.verify()
    as_v2 = v1.model_copy(update={"envelope_version": 2})
    assert not as_v2.verify()
    # a v1 envelope carrying a scope is structurally invalid
    v1_scoped = v1.model_copy(update={"scope_digest": scope_digest_of("s1")})
    assert not v1_scoped.verify()
    # a scope swap after the fact is an identity change (the digest is IN the hash)
    swapped = v2.model_copy(update={"scope_ref": "s2", "scope_digest": scope_digest_of("s2")})
    assert not swapped.verify()
    # unknown version: refused, never guessed
    assert not v2.model_copy(update={"envelope_version": 3}).verify()


def test_single_hash_derivation_structure() -> None:
    """#113 (a): exactly ONE preimage construction exists (ids.event_hash); every other
    module calls it. A second derivation would be a split-brain verifier."""
    root = pathlib.Path(__file__).resolve().parents[1] / "kawa"
    builders = []
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # the ENVELOPE preimage is uniquely identified by its prev_hash binding — other
        # canonical digests (operation digests etc.) may share field names like origin_node
        if '"prev_hash": prev_hash' in text or "'prev_hash': prev_hash" in text:
            builders.append(py.name)
    assert builders == ["ids.py"]


# ---------------- (b): stubs cross the boundary, chain intact, reducer-inert ----------------

def test_v2_withheld_as_stub_chain_survives(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "v1 before")
    scoped = kawa._emit_reduce(PlanCreated(plan_ref="p-secret", project_ref="kawa",
                                           objective="scoped"), scope_ref="s1")
    kawa.create_plan("p3", "kawa", "v1 after — chains onto the stubbed head")
    conn_a.commit()
    report = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert report.rejected == [] and len(report.admitted) == 3
    mat, sref, sdig = _stub_state(conn_b, scoped.event_id)
    assert mat is False and sref is None and sdig == scope_digest_of("s1")  # digest-only leak
    with conn_b.cursor() as cur:
        cur.execute("SELECT count(*) FROM event_plan WHERE event_id=%s", (scoped.event_id,))
        assert cur.fetchone()[0] == 0                    # no payload row
    assert _plans(conn_b) == ["p1", "p3"]                # reducer-inert: projections see v1 only
    # v1 events are untouched by the withholding rule
    assert _stub_state(conn_b, kawa.create_plan("p4", "kawa", "later").event_id) is None
    conn_a.commit()
    assert pull(conn_b, conn_a, keys=keys, trust=trust).admitted != []   # chain still flows


def test_mixed_stub_rebuild_is_inert_and_convergent(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "v1")
    kawa._emit_reduce(PlanCreated(plan_ref="p-secret", project_ref="kawa", objective="x"),
                      scope_ref="s1")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys, trust=trust)
    before = _plans(conn_b)
    rebuild(conn_b)                                      # rebuild-equals-incremental over stubs
    assert _plans(conn_b) == before == ["p1"]


# ---------------- BC-ii: the upgrade path (re-delivery with payload) ----------------

def test_stub_upgrade_atomic_idempotent_and_mismatch_refused(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    scoped = kawa._emit_reduce(PlanCreated(plan_ref="p-secret", project_ref="kawa",
                                           objective="scoped"), scope_ref="s1")
    kawa.create_plan("p2", "kawa", "after")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys, trust=trust)         # B holds the stub
    assert _plans(conn_b) == ["p2"]
    full = read_stream(conn_a, {})                        # A's local view has the bytes
    scoped_full = next(e for e in full if e.event_id == scoped.event_id)
    # FIRST, the attack: same envelope identity, different bytes — refused, still a stub,
    # and the origin is NOT poisoned (a later legit event still admits)
    forged = scoped_full.model_copy(update={
        "payload": PlanCreated(plan_ref="p-EVIL", project_ref="kawa", objective="swapped")})
    r0 = admit_batch(conn_b, [forged], keys=keys, trust=trust)
    assert [x.reason for x in r0.rejected] == ["upgrade_digest_mismatch"]
    assert _stub_state(conn_b, scoped.event_id)[0] is False   # still a stub
    assert _plans(conn_b) == ["p2"]
    kawa.create_plan("p3", "kawa", "still flowing")
    conn_a.commit()
    assert pull(conn_b, conn_a, keys=keys, trust=trust).admitted != []   # not poisoned
    # THEN the honest upgrade (simulating a 9b granted pull: admit_batch directly,
    # bypassing serve_batch's 9a withhold-all rule)
    r = admit_batch(conn_b, [scoped_full], keys=keys, trust=trust)
    assert r.admitted == [scoped.event_id] and r.rejected == []
    mat, sref, _ = _stub_state(conn_b, scoped.event_id)
    assert mat is True and sref == "s1"                   # cleartext arrives WITH the bytes
    assert sorted(_plans(conn_b)) == ["p-secret", "p2", "p3"]  # NOW it reduces — eligibility, not identity
    # idempotent: re-delivering the full event again is a no-op
    r2 = admit_batch(conn_b, [scoped_full], keys=keys, trust=trust)
    assert r2.admitted == [] and r2.rejected == []
    # and the append-only guard still forbids everything except that one monotone transition
    with pytest.raises(Exception, match="append-only"):
        with conn_b.cursor() as cur:
            cur.execute("UPDATE events SET materialized=false WHERE event_id=%s",
                        (scoped.event_id,))
    conn_b.rollback()


# ---------------- wire: stub representation ----------------

def test_wire_stub_round_trip_and_leak_boundary(conn_a, node_a) -> None:  # type: ignore[no-untyped-def]
    kawa, _, _, _ = node_a
    scoped = kawa._emit_reduce(PlanCreated(plan_ref="p-secret", project_ref="kawa",
                                           objective="scoped"), scope_ref="s1")
    conn_a.commit()
    w = to_wire(scoped, as_stub=True)
    assert w["payload_canonical"] is None
    assert w["envelope"]["scope_ref"] is None             # cleartext scope never leaves
    assert w["envelope"]["scope_digest"] == scope_digest_of("s1")
    e = from_wire(json.loads(json.dumps(w)))
    assert e.is_stub and e.verify() and e.self_hash == scoped.self_hash
    # a v1 envelope smuggling a scope over the wire is refused
    v1 = kawa.create_plan("p1", "kawa", "legacy")
    bad = to_wire(v1)
    bad["envelope"]["scope_digest"] = scope_digest_of("s1")
    with pytest.raises(WireVerificationError):
        from_wire(bad)
    # serve_batch is the boundary rule: v2 withheld, v1 untouched
    served = serve_batch([scoped, v1])
    assert served[0].is_stub and served[0].scope_ref is None
    assert served[1].payload is not None