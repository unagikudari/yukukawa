"""Step 2 epistemic nucleus (#97 rev 2) — the review's ten acceptance items + the round-2
binding constraints, as literal tests. Uses the dedicated test DBs (kawa_test_a / kawa_test_b)."""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.events import LinkAsserted, ObservationRecorded
from kawa.domain.identity import IdentityContext
from kawa.projections.reducers import rebuild

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "content_embedding, event_content, events, event_links, event_link, event_observation, event_claim, event_plan, "
    "event_work, event_work_dependency, event_work_retired, event_result, current_claim_standing, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy, work_dispatch"
)


def _fresh(env: str, default: str):  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get(env, default), autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    return c


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    c = _fresh("KAWA_TEST_DSN_A", "dbname=kawa_test_a")
    yield c
    c.close()


@pytest.fixture()
def k(conn):  # type: ignore[no-untyped-def]
    return Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))


def _links_snapshot(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT source_ref, relation, target_ref, resolved FROM event_links "
                    "ORDER BY source_ref, relation, target_ref")
        return cur.fetchall()


def _standing_snapshot(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT claim_event_id, standing FROM current_claim_standing ORDER BY claim_event_id")
        return cur.fetchall()


# ---- acceptance 1 + 2: dangling link admits, backfills deterministically, order-independent ----
# Within ONE origin the chain guarantees a link's in-origin target precedes it, so genuine
# dangling targets arise only across origins during replication — tested exactly that way.

def test_dangling_link_unresolved_then_backfilled_and_rebuild_equal(conn, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
    from kawa.domain.trust import TrustRegistry
    from kawa.storage.replication import admit_batch, read_stream

    conn_b = _fresh("KAWA_TEST_DSN_B", "dbname=kawa_test_b")
    try:
        linker = load_or_create_local_node(str(tmp_path / "linker.json"), node_ref="node-a")
        author = load_or_create_local_node(str(tmp_path / "author.json"), node_ref="node-b")
        k_author = Kawa(conn, identity=IdentityContext.from_local_node(author, actor_ref="w"), default_scope=None)
        claim = k_author.record_claim("cross-origin claim")
        obs = k_author.record_observation("probe", value_number=1.0, method="http_probe")
        k_linker = Kawa(conn, identity=IdentityContext.from_local_node(linker, actor_ref="l"), default_scope=None)
        k_linker.assert_link(obs.event_id, "supports", claim.event_id)

        keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
        trust = TrustRegistry(str(tmp_path / "trust.json"))
        for node_ref, cred in (("node-a", linker), ("node-b", author)):
            keys.register(cred.signing_key_ref, cred.public_pem())
            trust.enroll(node_ref, cred.signing_key_ref)

        wire = read_stream(conn, {})
        link_stream = [e for e in wire if e.origin_node == "node-a"]     # the link, alone
        content_stream = [e for e in wire if e.origin_node == "node-b"]  # its targets

        r1 = admit_batch(conn_b, link_stream, keys=keys, trust=trust)    # link-first: dangling
        assert r1.rejected == [] and len(r1.admitted) == 1               # admits cleanly
        with conn_b.cursor() as cur:
            cur.execute("SELECT resolved FROM event_links")
            assert cur.fetchone()[0] is False                            # unresolved, not crashed

        r2 = admit_batch(conn_b, content_stream, keys=keys, trust=trust)  # targets arrive
        assert r2.rejected == []
        with conn_b.cursor() as cur:
            cur.execute("SELECT resolved FROM event_links")
            assert cur.fetchone()[0] is True                             # deterministic backfill
            cur.execute("SELECT standing FROM current_claim_standing WHERE claim_event_id=%s",
                        (claim.event_id,))
            assert cur.fetchone()[0] == "grounded_supported"

        before = (_links_snapshot(conn_b), _standing_snapshot(conn_b))
        rebuild(conn_b)
        assert (_links_snapshot(conn_b), _standing_snapshot(conn_b)) == before  # rebuild == incremental
        # and the receiving-order permutation converges to the same projection as A's own
        assert _links_snapshot(conn_b) == _links_snapshot(conn)
        assert _standing_snapshot(conn_b) == _standing_snapshot(conn)
    finally:
        conn_b.close()


# ---- acceptance 3: self-link rejected deterministically ----

def test_self_link_rejected(k) -> None:  # type: ignore[no-untyped-def]
    c = k.record_claim("narcissus")
    with pytest.raises(Exception):
        LinkAsserted(source_ref=c.event_id, relation="supports", target_ref=c.event_id)
    with pytest.raises(Exception):
        k.assert_link(c.event_id, "supports", c.event_id)


# ---- acceptance 4: support cycles terminate and ground nothing ----

def test_claim_cycle_grounds_nothing(conn, k) -> None:  # type: ignore[no-untyped-def]
    a = k.record_claim("A"); b = k.record_claim("B")
    k.assert_link(a.event_id, "supports", b.event_id)
    k.assert_link(b.event_id, "supports", a.event_id)              # mutual bootstrap attempt
    assert k.claim_standing(a.event_id) == "unevaluated"
    assert k.claim_standing(b.event_id) == "unevaluated"
    obs = k.record_observation("probe", value_bool=True, method="command_exit")
    k.assert_link(obs.event_id, "supports", a.event_id)            # ground enters the cycle
    assert k.claim_standing(a.event_id) == "grounded_supported"
    assert k.claim_standing(b.event_id) == "grounded_supported"    # via non-cyclic path through A


# ---- acceptance 5 + binding constraint 2: supersedes is direct-only and unconditional ----

def test_supersedes_direct_only_and_unconditional(conn, k) -> None:  # type: ignore[no-untyped-def]
    a = k.record_claim("v1"); b = k.record_claim("v2"); c = k.record_claim("v3")
    k.assert_link(c.event_id, "supersedes", b.event_id)            # C retires B
    k.assert_link(b.event_id, "supersedes", a.event_id)            # B retires A — B itself superseded
    assert k.claim_standing(a.event_id) == "superseded"            # unconditional: B's standing irrelevant
    assert k.claim_standing(b.event_id) == "superseded"
    assert k.claim_standing(c.event_id) == "unevaluated"


# ---- binding constraint 1: supports paths prune superseded intermediates ----

def test_superseded_intermediate_claim_breaks_grounding(conn, k) -> None:  # type: ignore[no-untyped-def]
    obs = k.record_observation("m", value_number=1.5, method="metric_read")
    mid = k.record_claim("intermediate"); top = k.record_claim("conclusion")
    k.assert_link(obs.event_id, "supports", mid.event_id)
    k.assert_link(mid.event_id, "supports", top.event_id)
    assert k.claim_standing(top.event_id) == "grounded_supported"
    newer = k.record_claim("intermediate v2")
    k.assert_link(newer.event_id, "supersedes", mid.event_id)      # retire the middle of the path
    assert k.claim_standing(mid.event_id) == "superseded"
    assert k.claim_standing(top.event_id) == "unevaluated"         # pruned — grounding collapsed


# ---- acceptance 6: contradicts has no algebra ----

def test_contradicts_of_contradicts_is_not_support(conn, k) -> None:  # type: ignore[no-untyped-def]
    x = k.record_claim("X"); y = k.record_claim("Y attacks X"); z = k.record_claim("Z attacks Y")
    k.assert_link(y.event_id, "contradicts", x.event_id)
    k.assert_link(z.event_id, "contradicts", y.event_id)
    assert k.claim_standing(x.event_id) == "contradicted"          # Z's attack on Y changes nothing for X
    assert k.claim_standing(y.event_id) == "contradicted"
    obs = k.record_observation("e", value_text="seen", method="manual_human")
    k.assert_link(obs.event_id, "supports", x.event_id)
    assert k.claim_standing(x.event_id) == "contested"             # grounded AND contradicted


# ---- acceptance 7: observation value-type exclusivity, both layers ----

def test_observation_value_type_gate(k) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(Exception):
        ObservationRecorded(predicate="p", observation_method_class="http_probe")           # none
    with pytest.raises(Exception):
        ObservationRecorded(predicate="p", value_text="a", value_number=1.0,
                            observation_method_class="http_probe")                          # two
    # #98 §2 tuple coherence: partial snapshot tuple refused
    with pytest.raises(Exception):
        ObservationRecorded(predicate="p", value_text="a", observation_method_class="api_fetch",
                            source_ref="https://x", content_digest=None, fetched_at=None)
    ok = ObservationRecorded(predicate="p", value_text="a", observation_method_class="api_fetch",
                             source_ref="https://x", content_digest="sha256:aa", fetched_at="2026-08-12T12:00:00Z")
    assert ok.content_digest == "sha256:aa"


# ---- acceptance 8: LLM inference cannot enter as Observation ----

def test_nonfinite_numbers_rejected(k) -> None:  # type: ignore[no-untyped-def]
    """NaN/inf survive json.dumps as NON-canonical JSON — they would digest 'stably' while
    violating the JCS profile content addressing rests on. Refused at the payload gate."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(Exception):
            ObservationRecorded(predicate="p", value_number=bad,
                                observation_method_class="metric_read")


def test_model_inference_is_not_an_observation_method(k) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(Exception):
        ObservationRecorded(predicate="p", value_text="the service seems healthy",
                            observation_method_class="model_inference")   # not in the allowlist
    ev = k.record_claim("the service seems healthy", basis_note="LLM interpretation")
    assert k.claim_standing(ev.event_id) == "unevaluated"          # it lands as a Claim instead


# ---- acceptance 9 + #87: dependency predicate is order-independent; unknown defers ----

def _dep_state(conn, work_ref, dep_ref):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT dependency_state FROM current_work_dependency "
                    "WHERE work_ref=%s AND dependency_work_ref=%s", (work_ref, dep_ref))
        return cur.fetchone()[0]


def test_dependency_on_finished_work_resolves_at_declaration(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "dep order")
    k.derive_work("done", "p", "implement")
    k.record_result("done", "success", "r1")                       # result BEFORE declaration (#87)
    k.derive_work("late", "p", "implement")
    k.declare_dependency("late", "done", "ALL")
    assert _dep_state(conn, "late", "done") == "satisfied"         # no deadlock
    assert k.work_state("late") == "ready"


def test_dependency_on_execution_unknown_defers(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "unknown defers")
    k.derive_work("murky", "p", "implement")
    k.record_result("murky", "execution_unknown", "r?")
    k.derive_work("later", "p", "implement")
    k.declare_dependency("later", "murky", "ALL")
    assert _dep_state(conn, "later", "murky") == "pending"         # unknown NEVER satisfies
    assert k.work_state("later") == "blocked"


def test_dependency_replay_order_independence(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "replay")
    k.derive_work("dep", "p", "implement")
    k.record_result("dep", "success", "r1")
    k.derive_work("w", "p", "implement")
    k.declare_dependency("w", "dep", "ALL")                        # declaration AFTER result
    incremental = _dep_state(conn, "w", "dep")
    rebuild(conn)                                                  # replay = (origin, seq) order
    assert _dep_state(conn, "w", "dep") == incremental == "satisfied"


# ---- acceptance 10: event_links has no write path outside the reducer ----

def test_event_links_is_reducer_owned(conn, k) -> None:  # type: ignore[no-untyped-def]
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "kawa"
    offenders = [p for p in src.rglob("*.py")
                 if "INSERT INTO event_links" in p.read_text(encoding="utf-8")
                 and p.name != "reducers.py"]
    assert offenders == []                                         # only the reducer writes it
    # and it is disposable: dropping it entirely is repaired by rebuild
    c = k.record_claim("c"); o = k.record_observation("p", value_bool=True, method="http_probe")
    k.assert_link(o.event_id, "supports", c.event_id)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE event_links, current_claim_standing")
    conn.commit()
    rebuild(conn)
    assert k.claim_standing(c.event_id) == "grounded_supported"


# ---- links round-trip through the Phase-4C trust gate with standing intact ----

def test_links_replicate_through_trust_gate(conn, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
    from kawa.domain.trust import TrustRegistry
    from kawa.storage.replication import pull

    conn_b = _fresh("KAWA_TEST_DSN_B", "dbname=kawa_test_b")
    try:
        cred = load_or_create_local_node(str(tmp_path / "node-a.json"), node_ref="node-a")
        ka = Kawa(conn, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a"), default_scope=None)
        keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
        trust = TrustRegistry(str(tmp_path / "trust.json"))
        keys.register(cred.signing_key_ref, cred.public_pem())
        trust.enroll("node-a", cred.signing_key_ref)

        claim = ka.record_claim("replicated claim")
        obs = ka.record_observation("probe", value_number=1.0, method="http_probe")
        ka.assert_link(obs.event_id, "supports", claim.event_id)

        report = pull(conn_b, conn, keys=keys, trust=trust)
        assert report.rejected == [] and len(report.admitted) == 3
        with conn_b.cursor() as cur:
            cur.execute("SELECT standing FROM current_claim_standing WHERE claim_event_id=%s",
                        (claim.event_id,))
            assert cur.fetchone()[0] == "grounded_supported"       # standing re-derived on B
    finally:
        conn_b.close()
