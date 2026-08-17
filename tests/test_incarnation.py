"""Step 8A/8B acceptance (#111): incarnation lineage, fork evidence, freeze, resolve_fork.

Same two-store layout as test_replication.py (kawa_test_a / kawa_test_b; skips when absent).
"""
from __future__ import annotations

import time

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.events import Event, PlanCreated
from kawa.domain.identity import IdentityContext
from kawa.domain.ids import digest, event_hash
from kawa.domain.trust import TrustRegistry, genesis_incarnation
from kawa.storage.authority_gate import AuthorityRefused, resolve_fork
from kawa.storage.replication import (
    admit_batch,
    check_incarnation_contiguity,
    incarnation_intervals,
    pull,
)

psycopg = pytest.importorskip("psycopg")

from tests.test_replication import _ALL  # same isolation set  # noqa: E402


def _fresh(dsn_env: str, default: str):  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get(dsn_env, default), autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"replication test DB unavailable: {exc}")
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


def _rival(origin_node: str, seq: int, prev_hash: str | None, cred, plan_ref: str = "rival") -> Event:
    """An internally-consistent rival successor at a position, signed by `cred`."""
    payload = PlanCreated(plan_ref=plan_ref, project_ref="kawa", objective="the other branch")
    pd = digest(payload.model_dump(mode="json"))
    hlc = f"{int(time.time() * 1000)}.0.{origin_node}"   # near-now: these tests exercise provenance/origin, not temporal admissibility (#145)
    sh = event_hash(origin_node=origin_node, origin_seq=seq, hlc=hlc, kind=payload.kind.value,
                    subject_ref=None, actor_ref="rival", policy_digest=None,
                    payload_digest=pd, prev_hash=prev_hash)
    return Event(event_id=sh, origin_node=origin_node, origin_seq=seq, hlc=hlc, kind=payload.kind,
                 subject_ref=None, actor_ref="rival", policy_digest=None, payload_digest=pd,
                 prev_hash=prev_hash, self_hash=sh, signature=cred.sign(sh),
                 signing_key_ref=cred.signing_key_ref, signature_scheme="ed25519", payload=payload)


def _prev_hash(conn, origin: str, seq: int) -> str | None:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT self_hash FROM events WHERE origin_node=%s AND origin_seq=%s",
                    (origin, seq))
        row = cur.fetchone()
        return row[0] if row else None


def _evidence(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT origin_node, origin_seq, classification, frozen, held_hash, rival_hash "
                    "FROM security_fork_evidence ORDER BY origin_node, origin_seq, rival_hash")
        return cur.fetchall()


# ---------------- 8A: lineage verbs ----------------

def test_succession_is_explicit_fresh_key_and_total(tmp_path) -> None:  # type: ignore[no-untyped-def]
    t = TrustRegistry(str(tmp_path / "t.json"))
    t.enroll("node-x", "k1")
    assert t.incarnation_ref("k1") == genesis_incarnation("node-x")
    # rotation continues the SAME incarnation (key hygiene, not a continuity boundary)
    t.rotate("node-x", "k1", "k2")
    assert t.incarnation_ref("k2") == genesis_incarnation("node-x")
    # succession: fresh key mandatory, parentage recorded, node pinned
    t.succeed_incarnation("node-x", genesis_incarnation("node-x"), "inc:node-x:2", "k3")
    assert t.incarnation_ref("k3") == "inc:node-x:2"
    assert t.incarnation_parent("inc:node-x:2") == genesis_incarnation("node-x")
    with pytest.raises(ValueError, match="FRESH key"):
        t.succeed_incarnation("node-x", "inc:node-x:2", "inc:node-x:3", "k2")   # key reuse aliases
    with pytest.raises(ValueError, match="unknown parent"):
        t.succeed_incarnation("node-x", "inc:node-x:nope", "inc:node-x:3", "k4")
    with pytest.raises(ValueError, match="never crosses nodes"):
        t.succeed_incarnation("node-y", "inc:node-x:2", "inc:node-y:1", "k5")
    with pytest.raises(ValueError, match="minted once"):
        t.succeed_incarnation("node-x", genesis_incarnation("node-x"), "inc:node-x:2", "k6")
    # terminal-revocation semantics preserved across the new shape
    t.revoke("k3")
    with pytest.raises(ValueError, match="revoked"):
        t.enroll("node-x", "k3")


def test_scoped_revocation_bc3(tmp_path) -> None:  # type: ignore[no-untyped-def]
    t = TrustRegistry(str(tmp_path / "t.json"))
    t.enroll("node-x", "k1")
    t.revoke("k1", from_seq=5)
    assert t.standing("k1") == "revoked"                 # current standing: strict
    assert t.standing("k1", at_seq=4) == "active"        # pre-fork trunk still evaluates
    assert t.standing("k1", at_seq=5) == "revoked"       # the fork point itself is revoked
    # a TOTAL revocation is revoked at every seq — and a scoped call never narrows it back
    t.enroll("node-x", "k2")
    t.revoke("k2")
    t.revoke("k2", from_seq=99)
    assert t.standing("k2", at_seq=1) == "revoked"


def test_total_revocation_escalates_scoped_never_narrows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # PR #112 review finding 2: strictening is one-way in BOTH directions
    t = TrustRegistry(str(tmp_path / "t.json"))
    t.enroll("node-x", "k1")
    t.revoke("k1", from_seq=10)
    assert t.standing("k1", at_seq=5) == "active"        # scoped: pre-fork trunk verifies
    t.revoke("k1")                                       # escalate to TOTAL
    assert t.standing("k1", at_seq=5) == "revoked"       # scope dropped everywhere
    t.revoke("k1", from_seq=99)                          # a later scoped call cannot narrow back
    assert t.standing("k1", at_seq=5) == "revoked"


def test_revoked_key_rival_cannot_refreeze(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    # PR #112 review finding 1: the loser of a resolved fork (a distrusted key) must not be
    # able to mint fresh rivals and freeze the origin again — that would be a permanent DoS
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "trunk")
    kawa.create_plan("p2", "kawa", "head")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys, trust=trust)
    trust.revoke(cred.signing_key_ref, from_seq=2)       # as resolve_fork would
    rival = _rival("node-a", 2, _prev_hash(conn_b, "node-a", 1), cred, plan_ref="rival-1")
    r = admit_batch(conn_b, [rival], keys=keys, trust=trust)
    assert [x.reason for x in r.rejected] == ["collision"]   # plain collision, no evidence
    assert _evidence(conn_b) == []                            # nothing frozen
    rival2 = _rival("node-a", 1, None, cred, plan_ref="rival-2")   # pre-fork position, same key
    r2 = admit_batch(conn_b, [rival2], keys=keys, trust=trust)
    assert [x.reason for x in r2.rejected] == ["collision"] and _evidence(conn_b) == []


def test_legacy_flat_registry_loads_with_genesis_attribution(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json
    p = str(tmp_path / "legacy.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"k-old": {"node_ref": "node-x", "standing": "active"}}, f)
    t = TrustRegistry(p)
    assert t.standing("k-old") == "active"
    assert t.node_ref("k-old") == "node-x"
    assert t.incarnation_ref("k-old") == genesis_incarnation("node-x")


# ---------------- 8A: per-incarnation seq intervals ----------------

def test_per_incarnation_intervals_contiguous_and_lineage_ordered(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "first incarnation work")
    kawa.create_plan("p2", "kawa", "more")
    conn_a.commit()
    # the node restores: a NEW incarnation with a FRESH key, chaining onto the held head
    cred2 = load_or_create_local_node(str(tmp_path / "node-a-inc2.json"), node_ref="node-a")
    keys.register(cred2.signing_key_ref, cred2.public_pem())
    trust.succeed_incarnation("node-a", genesis_incarnation("node-a"),
                              "inc:node-a:2", cred2.signing_key_ref)
    kawa2 = Kawa(conn_a, identity=IdentityContext.from_local_node(cred2, actor_ref="agent-a"), default_scope=None)
    kawa2.create_plan("p3", "kawa", "after restore")
    conn_a.commit()

    intervals = incarnation_intervals(conn_a, trust, "node-a")
    assert intervals == [
        (genesis_incarnation("node-a"), 1, 2),
        ("inc:node-a:2", 3, 3),
    ]
    assert check_incarnation_contiguity(intervals) == []
    # negative control: a fabricated interleaving IS detected as evidence
    fabricated = [(genesis_incarnation("node-a"), 1, 2), ("inc:node-a:2", 3, 3),
                  (genesis_incarnation("node-a"), 4, 4)]
    assert check_incarnation_contiguity(fabricated) != []


# ---------------- 8B: evidence, classification, freeze ----------------

def test_same_incarnation_equivocation_freezes_in_any_order(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "trunk 1")
    kawa.create_plan("p2", "kawa", "trunk 2")
    conn_a.commit()
    assert pull(conn_b, conn_a, keys=keys, trust=trust).rejected == []
    # a VM-clone of the SAME incarnation (same key) publishes a different successor at seq 2
    rival = _rival("node-a", 2, _prev_hash(conn_b, "node-a", 1), cred)
    r1 = admit_batch(conn_b, [rival], keys=keys, trust=trust)
    assert [x.reason for x in r1.rejected] == ["equivocation"]
    ev = _evidence(conn_b)
    assert len(ev) == 1 and ev[0][2] == "equivocation" and ev[0][3] is True
    # order negative control: replaying the same rival, or the legit stream, changes nothing —
    # and the origin is frozen for EVERYTHING until an operator acts
    r2 = admit_batch(conn_b, [rival], keys=keys, trust=trust)
    assert [x.reason for x in r2.rejected] == ["origin_frozen"]
    kawa.create_plan("p3", "kawa", "post-fork legit")
    conn_a.commit()
    r3 = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert r3.admitted == [] and {x.reason for x in r3.rejected} == {"origin_frozen"}
    assert _evidence(conn_b) == ev                      # evidence unchanged by replay


def test_freeze_survives_restart(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "x")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys, trust=trust)
    rival = _rival("node-a", 1, None, cred)
    admit_batch(conn_b, [rival], keys=keys, trust=trust)
    conn_b.commit()
    kawa.create_plan("p2", "kawa", "post-fork — must stay refused after restart")
    conn_a.commit()
    # a "restarted" receiver: a brand-new connection and brand-new admission state
    dsn = os.environ.get("KAWA_TEST_DSN_B", "dbname=kawa_test_b")
    with psycopg.connect(dsn) as conn_b2:
        r = pull(conn_b2, conn_a, keys=keys, trust=trust)
        assert {x.reason for x in r.rejected} == {"origin_frozen"} and r.admitted == []


def test_cross_incarnation_collision_is_restore_fork(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "trunk")
    kawa.create_plan("p2", "kawa", "head")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys, trust=trust)
    # a PROPER restore: new incarnation, fresh key, recorded parentage — but it re-emits seq 2
    cred2 = load_or_create_local_node(str(tmp_path / "node-a-restored.json"), node_ref="node-a")
    keys.register(cred2.signing_key_ref, cred2.public_pem())
    trust.succeed_incarnation("node-a", genesis_incarnation("node-a"),
                              "inc:node-a:2", cred2.signing_key_ref)
    rival = _rival("node-a", 2, _prev_hash(conn_b, "node-a", 1), cred2)
    r = admit_batch(conn_b, [rival], keys=keys, trust=trust)
    assert [x.reason for x in r.rejected] == ["restore_fork"]
    assert _evidence(conn_b)[0][2] == "restore_fork"


def test_unauthenticated_junk_cannot_freeze(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "x")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys, trust=trust)
    stranger = load_or_create_local_node(str(tmp_path / "stranger.json"), node_ref="node-a")
    rival = _rival("node-a", 1, None, stranger)          # key never enrolled anywhere
    r = admit_batch(conn_b, [rival], keys=keys, trust=trust)
    assert [x.reason for x in r.rejected] == ["collision"]   # DoS guard: plain collision
    assert _evidence(conn_b) == []                            # no evidence, no freeze
    kawa.create_plan("p2", "kawa", "still flowing")
    conn_a.commit()
    assert pull(conn_b, conn_a, keys=keys, trust=trust).admitted != []


# ---------------- 8B: resolve_fork ----------------

def _one_member_cell(kawa_b, cred_b, conn_b):  # type: ignore[no-untyped-def]
    """A 1-member genesis Cell for fork resolution ON THE RESOLVING NODE (the Phase-0
    fleet shape, #118), plus a helper to mint receipts under it."""
    from kawa.domain.authority import (
        FORK_RESOLUTION_KEY, configuration_coordinate_digest,
        fork_resolution_operation_digest, make_proof)
    from kawa.domain.events import AuthorityConfiguration, AuthorityReceipt
    cd = configuration_coordinate_digest(authority_key=FORK_RESOLUTION_KEY, authority_epoch=0,
                                         members=[cred_b.signing_key_ref], quorum=1,
                                         prior_configuration_digest=None)
    kawa_b._emit_reduce(AuthorityConfiguration(
        authority_key=FORK_RESOLUTION_KEY, configuration_digest=cd, authority_epoch=0,
        members=[cred_b.signing_key_ref], quorum=1, succession_proof=make_proof(cd, [cred_b])))
    conn_b.commit()

    def receipt_for(origin_node, origin_seq, chosen_head):  # type: ignore[no-untyped-def]
        op = fork_resolution_operation_digest(origin_node=origin_node, origin_seq=origin_seq,
                                              chosen_head=chosen_head,
                                              configuration_digest=cd, policy_digest=None)
        ev = kawa_b._emit_reduce(AuthorityReceipt(
            authority_key=FORK_RESOLUTION_KEY, operation_digest=op, configuration_digest=cd,
            authority_epoch=0, quorum_proof=make_proof(op, [cred_b])))
        conn_b.commit()
        return ev.event_id

    return receipt_for


def test_resolve_fork_is_a_cp_operation(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """#118 10C (D1): fork-freeze release requires a VALID AuthorityReceipt — receipt-first,
    operation-bound, idempotent (BC-2), with NO operator fallback."""
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "trunk 1")
    kawa.create_plan("p2", "kawa", "trunk 2")
    kawa.create_plan("p3", "kawa", "head")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys, trust=trust)
    rival = _rival("node-a", 3, _prev_hash(conn_b, "node-a", 2), cred)
    admit_batch(conn_b, [rival], keys=keys, trust=trust)
    held_head = _prev_hash(conn_b, "node-a", 3)
    # B's own resolving authority: a 1-member genesis Cell held by B's node credential
    cred_b = load_or_create_local_node(str(tmp_path / "node-b.json"), node_ref="node-b")
    keys.register(cred_b.signing_key_ref, cred_b.public_pem())
    trust.enroll("node-b", cred_b.signing_key_ref)
    kawa_b = Kawa(conn_b, identity=IdentityContext.from_local_node(cred_b, actor_ref="op-b"),
                  default_scope=None)
    receipt_for = _one_member_cell(kawa_b, cred_b, conn_b)
    # no receipt → frozen stays frozen (INCOMPLETE never exercises, BC-3)
    with pytest.raises(AuthorityRefused, match="incomplete"):
        resolve_fork(conn_b, trust, origin_node="node-a", origin_seq=3,
                     chosen_head=held_head, receipt_event_id="sha256:" + "0" * 64, keys=keys)
    # a receipt bound to a DIFFERENT operation does not exercise (operation binding)
    wrong = receipt_for("node-a", 3, rival.event_id)
    with pytest.raises(AuthorityRefused, match="rival_adoption_deferred|operation_mismatch"):
        resolve_fork(conn_b, trust, origin_node="node-a", origin_seq=3,
                     chosen_head=rival.event_id, receipt_event_id=wrong, keys=keys)
    with pytest.raises(AuthorityRefused, match="operation_mismatch"):
        resolve_fork(conn_b, trust, origin_node="node-a", origin_seq=3,
                     chosen_head=held_head, receipt_event_id=wrong, keys=keys)
    # the VALID, correctly-bound receipt resolves — keep-held only (D2 still deferred)
    ok = receipt_for("node-a", 3, held_head)
    resolve_fork(conn_b, trust, origin_node="node-a", origin_seq=3,
                 chosen_head=held_head, receipt_event_id=ok, keys=keys,
                 reason="clone detected, keep held")
    assert all(not row[3] for row in _evidence(conn_b))       # unfrozen, audited by receipt
    # BC-2: idempotent replay under the SAME receipt completes as a no-op
    resolve_fork(conn_b, trust, origin_node="node-a", origin_seq=3,
                 chosen_head=held_head, receipt_event_id=ok, keys=keys)
    # a consumed fork point can never be re-decided (consume-once)
    other = receipt_for("node-a", 3, held_head)
    with pytest.raises(AuthorityRefused, match="already_resolved"):
        resolve_fork(conn_b, trust, origin_node="node-a", origin_seq=3,
                     chosen_head=held_head, receipt_event_id=other, keys=keys)
    # BC-3 (step 8): the losing key is revoked FROM the fork — a lagging replica still
    # verifies the pre-fork trunk it never saw
    assert trust.standing(cred.signing_key_ref) == "revoked"
    assert trust.standing(cred.signing_key_ref, at_seq=2) == "active"
    with conn_b.cursor() as cur:                              # lagging replica: fresh, empty B-side
        cur.execute(f"TRUNCATE {_ALL}")
    conn_b.commit()
    r = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert len(r.admitted) == 2                               # seq 1..2 trunk verified
    assert {x.reason for x in r.rejected} == {"trust_revoked"}  # seq 3+ stays distrusted
