"""Step 11B — the #122 acceptance invariants as literal tests (kawa_test_a; #92
isolation): no-collapse, model-swap isolation, materialized-only, stated skips,
zero-shadowing, read-only similarity, domain-ref mapping, deterministic order."""
from __future__ import annotations

import hashlib
import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.embeddings import (behavior_identity, content_digest_of, embed_missing,
                             embedding_frontier, extract_missing_content)
from kawa.retrieval import Intent, retrieve

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "content_embedding, event_content, events, event_links, event_link, event_observation, "
    "event_claim, event_plan, event_work, event_work_dependency, event_work_retired, "
    "event_result, current_claim_standing, current_plans, current_work, "
    "current_work_dependency, runtime_work_occupancy, work_dispatch"
)


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"),
                            autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def k(conn):  # type: ignore[no-untyped-def]
    return Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest"))


class FakeEmbedder:
    """Deterministic test profile: explicit vectors per text, hash fallback.
    A test double for the INTERFACE — the semantic model itself is exercised by
    the acceptance re-measurement, not by unit tests."""

    def __init__(self, table: dict[str, list[float]] | None = None,
                 name: str = "fake-model") -> None:
        self._table = dict(table or {})
        self._identity = behavior_identity(name, self._vec("__probe__"))

    def _vec(self, text: str) -> list[float]:
        if text in self._table:
            return self._table[text]
        h = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in h[:8]]

    @property
    def model_identity(self) -> str:
        return self._identity

    def embed(self, texts):  # type: ignore[no-untyped-def]
        return [self._vec(t) for t in texts]


def _index(conn, embedder):  # type: ignore[no-untyped-def]
    extract_missing_content(conn)
    embed_missing(conn, embedder)


def _vector_records(bundle):  # type: ignore[no-untyped-def]
    return [r for cid, recs in bundle.sections.items() if "vector" in cid for r in recs]


NEAR = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
CLOSE = [0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
MID = [0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
FAR = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_no_collapse_identical_text_two_records(conn, k):  # type: ignore[no-untyped-def]
    """#122 (g): identical embedding content never collapses distinct attributed
    records — one embedding row, BOTH records retrieved."""
    anchor = k.record_observation("anchor_probe", value_text="anchor", method="manual_human")
    c1 = k.record_claim("the replication mesh is healthy")
    c2 = k.record_claim("the replication mesh is healthy")
    conn.commit()
    emb = FakeEmbedder({
        "anchor_probe = anchor": NEAR,
        "the replication mesh is healthy": CLOSE,
    })
    _index(conn, emb)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM content_embedding WHERE content_digest=%s",
                    (content_digest_of("the replication mesh is healthy"),))
        assert cur.fetchone()[0] == 1                      # one embedding...
    bundle = retrieve(conn, Intent(about=anchor.event_id))
    refs = {r.ref for r in _vector_records(bundle)}
    assert {c1.event_id, c2.event_id} <= refs              # ...both records


def test_model_swap_reembeds_without_touching_old_rows(conn, k):  # type: ignore[no-untyped-def]
    k.record_claim("alpha")
    k.record_claim("beta")
    conn.commit()
    v1 = FakeEmbedder(name="fake-v1")
    _index(conn, v1)
    with conn.cursor() as cur:
        cur.execute("SELECT model_identity, computed_at FROM content_embedding "
                    "WHERE model_identity=%s ORDER BY content_digest", (v1.model_identity,))
        before = cur.fetchall()
    v2 = FakeEmbedder(name="fake-v2")
    embed_missing(conn, v2)
    with conn.cursor() as cur:
        cur.execute("SELECT model_identity, computed_at FROM content_embedding "
                    "WHERE model_identity=%s ORDER BY content_digest", (v1.model_identity,))
        assert cur.fetchall() == before                    # old rows untouched
        cur.execute("SELECT count(*) FROM content_embedding WHERE model_identity=%s",
                    (v2.model_identity,))
        assert cur.fetchone()[0] == len(before)            # re-embedded under new identity


def test_materialized_only_stub_has_no_bytes(conn, k):  # type: ignore[no-untyped-def]
    """#122 BC-4: a stub (materialized=false) never enters the substrate — no
    side-channel. The frontier states it as outside with_content."""
    k.record_claim("real materialized claim")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO events (event_id, origin_node, origin_seq, hlc, kind, actor_ref, "
            " payload_digest, self_hash, materialized) "
            "VALUES ('sha256:stub', 'test', 999999, '99.0.test', 'claim.recorded', "
            " 'pytest', 'sha256:x', 'sha256:x', false)")
        cur.execute("INSERT INTO event_claim (event_id, proposition) "
                    "VALUES ('sha256:stub', 'stub text that must never be embedded')")
    conn.commit()
    _index(conn, FakeEmbedder())
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM event_content WHERE event_id='sha256:stub'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM content_embedding WHERE content_digest=%s",
                    (content_digest_of("stub text that must never be embedded"),))
        assert cur.fetchone()[0] == 0


def test_empty_index_is_stated_never_silent(conn, k):  # type: ignore[no-untyped-def]
    c = k.record_claim("lonely claim")
    conn.commit()
    bundle = retrieve(conn, Intent(about=c.event_id))
    assert any("vector index empty" in s for s in bundle.skipped_classes)
    assert not _vector_records(bundle)


def test_zero_shadowing_structural_sections_unchanged(conn, k):  # type: ignore[no-untyped-def]
    """#122 BC-5 negative control: vector may only ADD reach — the structural
    classes return byte-identical record sets with and without the index."""
    claim = k.record_claim("service X is healthy")
    for i in range(3):
        o = k.record_observation(f"probe_{i}", value_bool=True, method="http_probe")
        k.assert_link(o.event_id, "supports", claim.event_id)
    conn.commit()

    def structural(bundle):  # type: ignore[no-untyped-def]
        return {cid: [r.ref for r in recs] for cid, recs in bundle.sections.items()
                if "vector" not in cid}

    before = structural(retrieve(conn, Intent(about=claim.event_id)))
    _index(conn, FakeEmbedder())
    after_bundle = retrieve(conn, Intent(about=claim.event_id))
    assert structural(after_bundle) == before


def test_similarity_is_read_only(conn, k):  # type: ignore[no-untyped-def]
    """§10.4: similarity is never a relation, never standing — retrieve() with
    the vector class writes NOTHING."""
    c = k.record_claim("read only probe")
    conn.commit()
    _index(conn, FakeEmbedder())
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        events_before = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM event_links")
        links_before = cur.fetchone()[0]
    retrieve(conn, Intent(about=c.event_id))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        assert cur.fetchone()[0] == events_before
        cur.execute("SELECT count(*) FROM event_links")
        assert cur.fetchone()[0] == links_before


def test_anchored_order_and_domain_ref_mapping(conn, k):  # type: ignore[no-untyped-def]
    """Nearest-first deterministic order; work events answer as work_ref, plan
    events as plan_ref (the corpus label space / lexical precedent)."""
    anchor = k.record_claim("anchor topic")
    k.create_plan("plan-x", "proj", "about the anchor topic broadly")
    k.derive_work("w-x", "plan-x", "implement")
    conn.commit()
    emb = FakeEmbedder({
        "anchor topic": NEAR,
        "plan-x: about the anchor topic broadly": MID,
        "w-x (implement of plan-x)": CLOSE,
    })
    _index(conn, emb)
    bundle = retrieve(conn, Intent(about=anchor.event_id))
    recs = _vector_records(bundle)
    refs = [r.ref for r in recs]
    assert refs[0] == "w-x"                                # CLOSE beats MID
    assert "plan-x" in refs
    sims = [float(r.path.rsplit("sim=", 1)[1]) for r in recs]
    assert sims == sorted(sims, reverse=True)


def test_textual_vector_stated_without_embedder_fires_with_one(conn, k):  # type: ignore[no-untyped-def]
    k.record_claim("the mesh replication lag is rising")
    conn.commit()
    emb = FakeEmbedder({
        "the mesh replication lag is rising": NEAR,
        "replication lag?": CLOSE,
    })
    _index(conn, emb)
    without = retrieve(conn, Intent(text_terms="replication lag?"))
    assert any("needs a live embedder" in s for s in without.skipped_classes)
    with_model = retrieve(conn, Intent(text_terms="replication lag?"), embedder=emb)
    assert _vector_records(with_model), "textual vector should fire with a live embedder"
    mismatched = FakeEmbedder(name="other-model")
    stated = retrieve(conn, Intent(text_terms="replication lag?"), embedder=mismatched)
    assert any("!= indexed model" in s for s in stated.skipped_classes)


def test_plan_revisions_dedup_to_one_domain_ref(conn, k):  # type: ignore[no-untyped-def]
    anchor = k.record_claim("dedup probe")
    k.create_plan("plan-d", "proj", "first objective wording")
    k.create_plan("plan-d", "proj", "second objective wording")   # revision event
    conn.commit()
    emb = FakeEmbedder({
        "dedup probe": NEAR,
        "plan-d: first objective wording": CLOSE,
        "plan-d: second objective wording": MID,
    })
    _index(conn, emb)
    bundle = retrieve(conn, Intent(about=anchor.event_id))
    plan_refs = [r.ref for r in _vector_records(bundle) if r.ref == "plan-d"]
    assert plan_refs == ["plan-d"]                          # nearest revision only


def test_frontier_counts_are_honest(conn, k):  # type: ignore[no-untyped-def]
    k.record_claim("counted")
    k.assert_link("sha256:nowhere-a", "supports", "sha256:nowhere-b")   # no-content kind
    conn.commit()
    emb = FakeEmbedder()
    _index(conn, emb)
    f = embedding_frontier(conn, emb.model_identity)
    assert f["with_content"] == f["embedded"] == 1
    assert f["no_content"] == f["materialized_events"] - 1
    assert f["missing"] == 0
