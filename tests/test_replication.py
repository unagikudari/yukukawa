"""Phase 4C — two-node replication consuming trust.admit(): B rejects forged/untrusted origin.

Two REAL stores (node A emits, node B pulls); the wire is `read_stream`'s output. Requires the
dedicated test databases `kawa_test_a` / `kawa_test_b` (createdb + scripts/apply_migrations.py) —
deliberately NOT `dbname=kawa`: these fixtures TRUNCATE, and the dogfood log must never be the
test fixture. Skips cleanly when either DB is unavailable.
"""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.events import Event, PlanCreated
from kawa.domain.identity import IdentityContext
from kawa.domain.ids import HLC, digest, event_hash
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import admit_batch, frontier, pull, read_stream

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "events, event_links, event_link, event_observation, event_claim, event_plan, "
    "event_work, event_work_dependency, event_work_retired, event_result, current_claim_standing, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy, work_dispatch, "
    # step 8: admission consults fork evidence (durable freeze), so isolation must clear it too
    "security_fork_evidence, result_occurrence_quarantine"
)


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
    """An attested Kawa runtime on store A (origin 'node-a'), plus B's registries with A enrolled."""
    cred = load_or_create_local_node(str(tmp_path / "node-a.json"), node_ref="node-a")
    kawa = Kawa(conn_a, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a"))
    keys = PublicKeyRegistry(str(tmp_path / "b-keys.json"))
    trust = TrustRegistry(str(tmp_path / "b-trust.json"))
    keys.register(cred.signing_key_ref, cred.public_pem())
    trust.enroll("node-a", cred.signing_key_ref)
    return kawa, cred, keys, trust


def _count(conn) -> int:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        return cur.fetchone()[0]


def _head(conn, origin: str):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT origin_seq, self_hash FROM events WHERE origin_node=%s "
                    "ORDER BY origin_seq DESC LIMIT 1", (origin,))
        return cur.fetchone()


def _forge(origin_node: str, seq: int, prev_hash: str | None, cred) -> Event:  # type: ignore[no-untyped-def]
    """An attacker-crafted Event: internally consistent (verify() passes), claiming `origin_node`,
    signed by `cred` — whoever that key belongs to."""
    payload = PlanCreated(plan_ref="evil", project_ref="kawa", objective="forged")
    pd = digest(payload.model_dump(mode="json"))
    hlc = f"9999999999999.0.{origin_node}"
    sh = event_hash(origin_node=origin_node, origin_seq=seq, hlc=hlc, kind=payload.kind.value,
                    subject_ref=None, actor_ref="attacker", policy_digest=None,
                    payload_digest=pd, prev_hash=prev_hash)
    return Event(event_id=sh, origin_node=origin_node, origin_seq=seq, hlc=hlc, kind=payload.kind,
                 subject_ref=None, actor_ref="attacker", policy_digest=None, payload_digest=pd,
                 prev_hash=prev_hash, self_hash=sh, signature=cred.sign(sh),
                 signing_key_ref=cred.signing_key_ref, signature_scheme="ed25519", payload=payload)


def test_pull_catchup_verbatim_and_idempotent(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    """cursor-catchup: an empty B reaches A's event set through the trust gate; re-pull is a no-op;
    admission stores A's attestation VERBATIM (B never re-signs — replication grants nothing)."""
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "replicate me")
    kawa.derive_work("w1", "p1", "implement", role_requirement="Implementer")
    kawa.record_result("w1", "success", "r1")

    clock = HLC(node="node-b")
    report = pull(conn_b, conn_a, keys=keys, trust=trust, clock=clock)
    assert len(report.admitted) == 3 and report.rejected == []
    assert frontier(conn_b) == frontier(conn_a) == {"node-a": 3}

    # projections rebuilt B-side from the SAME events → same understanding
    with conn_b.cursor() as cur:
        cur.execute("SELECT lifecycle FROM current_plans WHERE plan_ref='p1'")
        assert cur.fetchone() is not None
        cur.execute("SELECT execution FROM current_work WHERE work_ref='w1'")
        assert cur.fetchone()[0] == "finished"
        # verbatim attestation: B stores the origin's signature, key_ref, scheme unchanged
        cur.execute("SELECT signature, signing_key_ref FROM events WHERE origin_seq=1")
        sig_b, kref_b = cur.fetchone()
    with conn_a.cursor() as cur:
        cur.execute("SELECT signature FROM events WHERE origin_seq=1")
        assert (sig_b, kref_b) == (cur.fetchone()[0], cred.signing_key_ref)

    # happens-before: B's clock advanced past everything it saw
    last_phys = max(int(e.hlc.split(".", 1)[0]) for e in read_stream(conn_a, {}))
    assert clock.physical_ms >= last_phys

    again = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert again.admitted == [] and again.rejected == []           # idempotent re-delivery


def test_untrusted_signature_is_rejected(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A forged continuation of A's stream signed by a key B has never enrolled: provenance cannot
    resolve → rejected, nothing stored. Fail closed — unknown is never 'accept'."""
    kawa, _, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "genuine")
    pull(conn_b, conn_a, keys=keys, trust=trust)

    attacker = load_or_create_local_node(str(tmp_path / "attacker.json"), node_ref="node-x")
    seq, sh = _head(conn_b, "node-a")
    forged = _forge("node-a", seq + 1, sh, attacker)
    assert forged.verify()                                          # internally consistent, still refused
    before = _count(conn_b)
    report = admit_batch(conn_b, [forged], keys=keys, trust=trust)
    assert report.admitted == []
    assert [r.reason for r in report.rejected] == ["provenance_invalid"]
    assert _count(conn_b) == before


def test_trusted_key_cannot_speak_as_another_node(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """THE forged-origin case trust.admit() alone cannot catch: node C's key is enrolled and ACTIVE,
    and C signs an Event claiming origin_node='node-a'. Signature valid, standing active — but the
    key is bound to node-c, so admission refuses it as forged_origin."""
    kawa, _, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "genuine")
    pull(conn_b, conn_a, keys=keys, trust=trust)

    c_cred = load_or_create_local_node(str(tmp_path / "node-c.json"), node_ref="node-c")
    keys.register(c_cred.signing_key_ref, c_cred.public_pem())
    trust.enroll("node-c", c_cred.signing_key_ref)                  # C is genuinely trusted…

    seq, sh = _head(conn_b, "node-a")
    forged = _forge("node-a", seq + 1, sh, c_cred)                  # …but speaks as node-a
    report = admit_batch(conn_b, [forged], keys=keys, trust=trust)
    assert [r.reason for r in report.rejected] == ["forged_origin"]
    assert report.admitted == []


def test_distrust_is_forward_only(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    """③④ S7 on the replication path: revoking A's key stops A's FUTURE events at B's gate, while
    everything already admitted stays — past evidence preserved, never rewritten."""
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "pre-revocation")
    first = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert len(first.admitted) == 1

    trust.revoke(cred.signing_key_ref)                              # forward-only distrust
    kawa.derive_work("w-late", "p1", "implement")                   # A keeps emitting…
    kawa.record_result("w-late", "success", "r-late")
    report = pull(conn_b, conn_a, keys=keys, trust=trust)

    assert report.admitted == []                                    # …but B admits none of it
    assert [r.reason for r in report.rejected] == ["trust_revoked", "predecessor_rejected"]
    assert _count(conn_b) == 1                                      # the past is intact…
    with conn_b.cursor() as cur:
        cur.execute("SELECT plan_ref FROM current_plans")
        assert cur.fetchone()[0] == "p1"                            # …and still drives understanding
        cur.execute("SELECT count(*) FROM current_work WHERE work_ref='w-late'")
        assert cur.fetchone()[0] == 0                               # the post-revocation work never landed


def test_rekeying_after_revocation_does_not_resurrect_the_stream(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Round-1 review follow-up, admission result PINNED: even if a node re-keys after its key was
    revoked (fresh enroll for the same node_ref is allowed), its origin stream stays wedged at the
    revocation point — the distrust-era events are inadmissible (trust_revoked), so nothing after
    them can chain (predecessor_rejected). Terminal distrust of a stream is a consequence of
    contiguity, not a registry flag; a re-admitted node re-joins as a NEW origin."""
    kawa, cred, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "pre-revocation")
    pull(conn_b, conn_a, keys=keys, trust=trust)

    trust.revoke(cred.signing_key_ref)
    kawa.derive_work("w-mid", "p1", "implement")                    # distrust-era event, signed K1

    cred2 = load_or_create_local_node(str(tmp_path / "node-a-rekey.json"), node_ref="node-a")
    keys.register(cred2.signing_key_ref, cred2.public_pem())
    trust.enroll("node-a", cred2.signing_key_ref)                   # fresh enroll, same node: allowed
    k2 = Kawa(conn_a, identity=IdentityContext.from_local_node(cred2, actor_ref="agent-a"))
    k2.record_result("w-mid", "success", "r-mid")                   # post-re-key event, signed K2

    report = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert report.admitted == []
    assert [r.reason for r in report.rejected] == ["trust_revoked", "predecessor_rejected"]
    assert frontier(conn_b) == {"node-a": 1}                        # wedged at the revocation point


def test_unsigned_events_do_not_cross_nodes(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    """An unattested runtime's events (honest NULL signature, valid locally) stop at the node
    boundary: cross-node admission requires attestation."""
    _, _, keys, trust = node_a
    u = Kawa(conn_a, identity=IdentityContext.from_local_runtime(node_ref="node-u", actor_ref="a"))
    u.create_plan("pu", "kawa", "unattested local work")
    report = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert report.admitted == []
    assert [r.reason for r in report.rejected] == ["unsigned"]
    assert _count(conn_b) == 0


def test_collision_at_held_position_is_reported(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§4.1: re-delivery of the SAME event is a no-op, but a DIFFERENT event claiming an
    already-held (origin, seq) position is a detected collision — reported, never a silent drop."""
    kawa, _, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "genuine")
    pull(conn_b, conn_a, keys=keys, trust=trust)

    c_cred = load_or_create_local_node(str(tmp_path / "node-c.json"), node_ref="node-c")
    keys.register(c_cred.signing_key_ref, c_cred.public_pem())
    trust.enroll("node-c", c_cred.signing_key_ref)
    seq, _sh = _head(conn_b, "node-a")
    imposter = _forge("node-a", seq, None, c_cred)                  # different content at a held position
    report = admit_batch(conn_b, [imposter], keys=keys, trust=trust)
    assert [r.reason for r in report.rejected] == ["collision"]
    assert report.admitted == [] and _count(conn_b) == 1


def test_gap_is_reported_not_skipped(conn_a, conn_b, node_a) -> None:  # type: ignore[no-untyped-def]
    """gap-detect: withhold one event from the wire; the next one fails contiguity and is REPORTED —
    admission never papers over a hole in a gap-free stream."""
    kawa, _, keys, trust = node_a
    kawa.create_plan("p1", "kawa", "one")
    kawa.derive_work("w1", "p1", "implement")
    kawa.record_result("w1", "success", "r1")
    wire = read_stream(conn_a, frontier(conn_b))
    withheld = [wire[0], wire[2]]                                   # drop the middle event
    report = admit_batch(conn_b, withheld, keys=keys, trust=trust)
    assert report.admitted == [wire[0].event_id]
    assert [r.reason for r in report.rejected] == ["chain_gap"]
    assert frontier(conn_b) == {"node-a": 1}                        # holds exactly the contiguous prefix
