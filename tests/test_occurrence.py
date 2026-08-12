"""Step 8D acceptance (#111): the plan-node occurrence key, the three-way discriminator,
BC-2 duplicate containment, and the BC-4 replication-lag retry negative control.
"""
from __future__ import annotations

import inspect
import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.domain.occurrence import work_occurrence_key
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import pull

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


def _kawa(conn, tmp_path, name: str):  # type: ignore[no-untyped-def]
    cred = load_or_create_local_node(str(tmp_path / f"{name}.json"), node_ref=name)
    return Kawa(conn, identity=IdentityContext.from_local_node(cred, actor_ref=f"agent-{name}")), cred


def _exec_state(conn, work_ref):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT execution FROM current_work WHERE work_ref=%s", (work_ref,))
        return cur.fetchone()[0]


def _quarantine(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, first_event_id, work_ref FROM result_occurrence_quarantine")
        return cur.fetchall()


def test_occurrence_key_is_durable_cause_bytes_only() -> None:
    # structure test: the derivation takes durable cause coordinates and NOTHING else —
    # no connection to fall back on (BC-1), no clock, no node identity
    params = set(inspect.signature(work_occurrence_key).parameters)
    assert params == {"plan_ref", "work_ref", "causal_prior_result_ref"}
    k1 = work_occurrence_key(plan_ref="p", work_ref="w", causal_prior_result_ref=None)
    assert k1 == work_occurrence_key(plan_ref="p", work_ref="w", causal_prior_result_ref=None)
    assert k1 != work_occurrence_key(plan_ref="p", work_ref="w", causal_prior_result_ref="r1")


def test_no_new_hashed_envelope_column(conn_a) -> None:  # type: ignore[no-untyped-def]
    # schema test: occurrence_key lives on the PAYLOAD table, never the envelope, and the
    # envelope hash derivation takes exactly its pre-step-8 inputs
    from kawa.domain.ids import event_hash
    with conn_a.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='events'")
        events_cols = {r[0] for r in cur.fetchall()}
    assert "occurrence_key" not in events_cols
    assert set(inspect.signature(event_hash).parameters) == {
        "origin_node", "origin_seq", "hlc", "kind", "subject_ref", "actor_ref",
        "policy_digest", "payload_digest", "prev_hash",
    }


def test_quarantine_is_a_projection_rebuild_converges(conn_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from kawa.projections.reducers import rebuild
    kawa, _ = _kawa(conn_a, tmp_path, "node-a")
    kawa.create_plan("p1", "kawa", "rebuild test")
    kawa.derive_work("w1", "p1", "implement")
    k = work_occurrence_key(plan_ref="p1", work_ref="w1", causal_prior_result_ref=None)
    kawa.record_result("w1", "failure", "r1", occurrence_key=k)
    kawa.record_result("w1", "success", "r1-dup", occurrence_key=k)
    conn_a.commit()
    before_q, before_state = _quarantine(conn_a), _exec_state(conn_a, "w1")
    rebuild(conn_a)
    assert _quarantine(conn_a) == before_q                 # rebuild-equals-incremental
    assert _exec_state(conn_a, "w1") == before_state == "retryable"


def test_three_way_discriminator(conn_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, _ = _kawa(conn_a, tmp_path, "node-a")
    kawa.create_plan("p1", "kawa", "occurrence test")
    kawa.derive_work("w1", "p1", "implement")
    k_first = work_occurrence_key(plan_ref="p1", work_ref="w1", causal_prior_result_ref=None)

    # CASE 2 first (crash-before-Result retry): same key, NO recorded consumer yet → records
    r1 = kawa.record_result("w1", "failure", "res-1", occurrence_key=k_first)
    assert _exec_state(conn_a, "w1") == "retryable" and _quarantine(conn_a) == []

    # CASE 1 (duplicate re-record of the SAME attempt, e.g. restart replay): same key,
    # a recorded Result already consumed it → contained, inert for projections.
    # The duplicate even lies 'success' — if it applied, execution would move to finished.
    kawa.record_result("w1", "success", "res-1-replayed", occurrence_key=k_first)
    assert _exec_state(conn_a, "w1") == "retryable"            # nothing moved
    q = _quarantine(conn_a)
    assert len(q) == 1 and q[0][1] == r1.event_id              # audited against the first consumer

    # CASE 3 (deliberate retry after failure): the trigger carries the failure Result's id →
    # NEW key → records normally and moves state
    k_retry = work_occurrence_key(plan_ref="p1", work_ref="w1",
                                  causal_prior_result_ref=r1.event_id)
    assert k_retry != k_first
    kawa.record_result("w1", "success", "res-2", occurrence_key=k_retry)
    assert _exec_state(conn_a, "w1") == "finished"
    assert len(_quarantine(conn_a)) == 1                       # containment unchanged


def test_results_without_occurrence_key_are_untouched(conn_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    # pre-step-8 behavior is preserved byte-for-byte for legacy emitters
    kawa, _ = _kawa(conn_a, tmp_path, "node-a")
    kawa.create_plan("p1", "kawa", "legacy")
    kawa.derive_work("w1", "p1", "implement")
    kawa.record_result("w1", "failure", "r1")
    kawa.record_result("w1", "success", "r2")
    assert _exec_state(conn_a, "w1") == "finished" and _quarantine(conn_a) == []


def test_bc4_replication_lag_retry_is_not_false_merged(conn_a, conn_b, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The r2 counterexample: node A recorded the failure Result; node B retries BEFORE that
    Result reaches it. BC-1 (the trigger carries `causal_prior_result_ref`) keeps the retry's
    key correct on the lagging node; after full convergence the retry is applied on BOTH nodes,
    never quarantined. The forbidden local-∅ fallback is shown to be exactly the false merge."""
    kawa_a, cred_a = _kawa(conn_a, tmp_path, "node-a")
    kawa_b, cred_b = _kawa(conn_b, tmp_path, "node-b")
    # cross-enroll so replication admits both origins on both sides
    keys_a, trust_a = PublicKeyRegistry(str(tmp_path / "ka.json")), TrustRegistry(str(tmp_path / "ta.json"))
    keys_b, trust_b = PublicKeyRegistry(str(tmp_path / "kb.json")), TrustRegistry(str(tmp_path / "tb.json"))
    for keys, trust in ((keys_a, trust_a), (keys_b, trust_b)):
        keys.register(cred_a.signing_key_ref, cred_a.public_pem())
        keys.register(cred_b.signing_key_ref, cred_b.public_pem())
        trust.enroll("node-a", cred_a.signing_key_ref)
        trust.enroll("node-b", cred_b.signing_key_ref)

    kawa_a.create_plan("p1", "kawa", "lag test")
    kawa_a.derive_work("w1", "p1", "implement")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys_b, trust=trust_b)           # B knows the plan/work…
    k_first = work_occurrence_key(plan_ref="p1", work_ref="w1", causal_prior_result_ref=None)
    r1 = kawa_a.record_result("w1", "failure", "res-1", occurrence_key=k_first)
    conn_a.commit()                                            # …but NOT the failure Result yet

    # the retry TRIGGER (dispatch) carries the causal prior — B derives the correct key even
    # though its local log has never seen r1 (BC-1: no local query, no ∅ fallback)
    k_retry = work_occurrence_key(plan_ref="p1", work_ref="w1",
                                  causal_prior_result_ref=r1.event_id)
    kawa_b.record_result("w1", "success", "res-2", occurrence_key=k_retry)
    conn_b.commit()

    # converge both ways; the retry must be APPLIED everywhere, quarantined nowhere
    pull(conn_b, conn_a, keys=keys_b, trust=trust_b)
    pull(conn_a, conn_b, keys=keys_a, trust=trust_a)
    assert _quarantine(conn_a) == [] and _quarantine(conn_b) == []
    assert _exec_state(conn_a, "w1") == "finished"
    assert _exec_state(conn_b, "w1") == "finished"

    # negative control: the FORBIDDEN local fallback (prior=None because "locally unseen")
    # derives the first-attempt key — exactly the false merge BC-1 exists to prevent
    assert work_occurrence_key(plan_ref="p1", work_ref="w1",
                               causal_prior_result_ref=None) == k_first


def test_duplicate_containment_converges_in_any_arrival_order(conn_a, conn_b, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Two origins each record a Result consuming the SAME key (a true duplicate across nodes,
    e.g. a restart re-dispatch raced to another participant). Whoever is FIRST in the causal
    total order wins on every node, regardless of arrival order (BC-2 convergence)."""
    kawa_a, cred_a = _kawa(conn_a, tmp_path, "node-a")
    kawa_b, cred_b = _kawa(conn_b, tmp_path, "node-b")
    keys_a, trust_a = PublicKeyRegistry(str(tmp_path / "ka.json")), TrustRegistry(str(tmp_path / "ta.json"))
    keys_b, trust_b = PublicKeyRegistry(str(tmp_path / "kb.json")), TrustRegistry(str(tmp_path / "tb.json"))
    for keys, trust in ((keys_a, trust_a), (keys_b, trust_b)):
        keys.register(cred_a.signing_key_ref, cred_a.public_pem())
        keys.register(cred_b.signing_key_ref, cred_b.public_pem())
        trust.enroll("node-a", cred_a.signing_key_ref)
        trust.enroll("node-b", cred_b.signing_key_ref)

    kawa_a.create_plan("p1", "kawa", "race")
    kawa_a.derive_work("w1", "p1", "implement")
    conn_a.commit()
    pull(conn_b, conn_a, keys=keys_b, trust=trust_b)
    k = work_occurrence_key(plan_ref="p1", work_ref="w1", causal_prior_result_ref=None)
    ra = kawa_a.record_result("w1", "success", "res-a", occurrence_key=k)   # earlier HLC
    conn_a.commit()
    rb = kawa_b.record_result("w1", "success", "res-b", occurrence_key=k)   # later HLC
    conn_b.commit()

    pull(conn_a, conn_b, keys=keys_a, trust=trust_a)   # A hears B's duplicate after its own
    pull(conn_b, conn_a, keys=keys_b, trust=trust_b)   # B hears A's WINNER after its own loser
    qa, qb = _quarantine(conn_a), _quarantine(conn_b)
    assert [q[0] for q in qa] == [rb.event_id] and qa[0][1] == ra.event_id
    assert [q[0] for q in qb] == [rb.event_id] and qb[0][1] == ra.event_id  # same winner everywhere
    assert _exec_state(conn_a, "w1") == "finished" and _exec_state(conn_b, "w1") == "finished"
