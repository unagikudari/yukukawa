"""Step 11B embedding substrate (#122 rev 2 (f)-(i) — built on the measured GO).

Design commitments, in the order the plan pinned them:

  materialized-only   a stub has no bytes to embed — extraction reads ONLY
                      events WHERE materialized (no side-channel substrate)
  content identity    embeddings are keyed (content_digest, model_identity),
                      never by event: identical text shares one embedding,
                      attributed records never collapse
  replaceable profile the `Embedder` Protocol is the commitment; the Phase-0
                      binding (fastembed, local model on the GPU node) is a
                      profile, like Ed25519-vs-TPM
  model_identity      a canonical digest of the model's BEHAVIOR: the model
                      name + dimensions + a digest of its output on a pinned
                      probe sentence — a renamed-but-identical model converges,
                      a silently-changed model diverges, both honestly
  inspectable         `embedding_frontier` states embedded / missing /
                      no_content over the materialized log — freshness is a
                      surfaced number, never a feeling (§10.6)

The indexer (scripts/embed_index.py) and the vector query class
(kawa/retrieval.py) both sit on this module. Nothing here writes Domain
events; both tables are §12.2 derived materializations.
"""
from __future__ import annotations

import hashlib
from typing import Protocol, Sequence

import psycopg

# The pinned probe: model_identity digests the model's output on this sentence.
_IDENTITY_PROBE = "kawa model identity probe v1: the river remembers what mattered"


class Embedder(Protocol):
    """The replaceable model profile (#122 (i)): interface, not model."""

    @property
    def model_identity(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def content_digest_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def behavior_identity(name: str, vector: Sequence[float]) -> str:
    """Canonical model_identity: name + dims + digest of the probe output."""
    payload = ",".join(f"{v:.6f}" for v in vector).encode("ascii")
    return f"{name}:{len(vector)}d:probe-sha256:{hashlib.sha256(payload).hexdigest()[:16]}"


# ---- canonical text (what an event MEANS, for embedding purposes) ----

def canonical_text(cur: psycopg.Cursor, event_id: str, kind: str) -> str | None:
    """The embeddable text of one event, or None when the kind carries no
    semantic bytes (structural links, dependency edges, authority plumbing,
    objective-less lifecycle rows). None is reported as no_content in the
    frontier — absence is stated, never silent."""
    if kind == "claim.recorded":
        cur.execute("SELECT proposition FROM event_claim WHERE event_id=%s", (event_id,))
        row = cur.fetchone()
        return row[0] if row else None
    if kind in ("plan.created", "plan.lifecycle_changed"):
        cur.execute("SELECT plan_ref, objective FROM event_plan WHERE event_id=%s "
                    "AND objective IS NOT NULL", (event_id,))
        row = cur.fetchone()
        return f"{row[0]}: {row[1]}" if row else None
    if kind == "work.derived":
        cur.execute("SELECT work_ref, plan_ref, work_kind FROM event_work WHERE event_id=%s",
                    (event_id,))
        row = cur.fetchone()
        return f"{row[0]} ({row[2]} of {row[1]})" if row else None
    if kind == "result.recorded":
        cur.execute("SELECT work_ref, outcome, coalesce(summary,'') FROM event_result "
                    "WHERE event_id=%s", (event_id,))
        row = cur.fetchone()
        if row is None:
            return None
        text = f"{row[0]}: {row[1]}" + (f". {row[2]}" if row[2] else "")
        return text
    if kind == "observation.recorded":
        cur.execute("SELECT predicate, coalesce(value_text, value_number::text, "
                    "value_bool::text, value_time) FROM event_observation WHERE event_id=%s",
                    (event_id,))
        row = cur.fetchone()
        return f"{row[0]} = {row[1]}" if row else None
    return None


# ---- extraction + indexing (restartable; each call commits its own work) ----

def extract_missing_content(conn: psycopg.Connection) -> int:
    """Materialize event_content rows for MATERIALIZED events that lack one.
    Idempotent and restartable; returns how many rows were added."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.event_id, e.kind FROM events e "
            "WHERE e.materialized AND NOT EXISTS "
            "  (SELECT 1 FROM event_content c WHERE c.event_id = e.event_id) "
            "ORDER BY e.origin_node, e.origin_seq")
        pending = cur.fetchall()
        added = 0
        for event_id, kind in pending:
            text = canonical_text(cur, event_id, kind)
            if text is None:
                continue
            cur.execute(
                "INSERT INTO event_content (event_id, content_digest, canonical_text) "
                "VALUES (%s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                (event_id, content_digest_of(text), text))
            added += cur.rowcount
    conn.commit()
    return added


def _vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def embed_missing(conn: psycopg.Connection, embedder: Embedder, batch: int = 64) -> int:
    """Embed every distinct content_digest lacking a row under this model.
    Batch-committed => restartable mid-pass (#122 (h)); never touches events."""
    model = embedder.model_identity
    total = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT c.content_digest, min(c.canonical_text) FROM event_content c "
                "WHERE NOT EXISTS (SELECT 1 FROM content_embedding e "
                "  WHERE e.content_digest = c.content_digest AND e.model_identity = %s) "
                "GROUP BY c.content_digest ORDER BY c.content_digest LIMIT %s",
                (model, batch))
            chunk = cur.fetchall()
            if not chunk:
                return total
            vectors = embedder.embed([text for _, text in chunk])
            for (digest, _), vec in zip(chunk, vectors):
                cur.execute(
                    "INSERT INTO content_embedding (content_digest, model_identity, embedding) "
                    "VALUES (%s, %s, %s::vector) ON CONFLICT DO NOTHING",
                    (digest, model, _vector_literal(vec)))
                total += cur.rowcount
        conn.commit()


def embedding_frontier(conn: psycopg.Connection, model_identity: str | None = None) -> dict[str, int]:
    """Freshness, inspectable (#122 (h)): how much of the materialized log the
    index covers under `model_identity` (default: the best-covered model)."""
    with conn.cursor() as cur:
        if model_identity is None:
            cur.execute("SELECT model_identity FROM content_embedding "
                        "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1")
            row = cur.fetchone()
            model_identity = row[0] if row else None
        cur.execute("SELECT count(*) FROM events WHERE materialized")
        materialized = int((cur.fetchone() or (0,))[0])
        cur.execute("SELECT count(*) FROM event_content")
        with_content = int((cur.fetchone() or (0,))[0])
        embedded = 0
        if model_identity is not None:
            cur.execute(
                "SELECT count(*) FROM event_content c WHERE EXISTS "
                "  (SELECT 1 FROM content_embedding e WHERE e.content_digest = c.content_digest "
                "   AND e.model_identity = %s)", (model_identity,))
            embedded = int((cur.fetchone() or (0,))[0])
    return {
        "materialized_events": materialized,
        "with_content": with_content,
        "no_content": materialized - with_content,
        "embedded": embedded,
        "missing": with_content - embedded,
    }


# ---- Phase-0 profile: fastembed, local model on the GPU node ----

class FastembedEmbedder:
    """Phase-0 binding of the Embedder profile (#122 (i)): a local ONNX model
    via fastembed. Deterministic on CPU; the GPU node hosts it, the interface
    outlives it. Import cost is paid lazily and only by callers that actually
    embed (the anchored vector query path needs NO live model)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name=model_name)
        probe = [list(v) for v in self._model.embed([_IDENTITY_PROBE])][0]
        self._identity = behavior_identity(f"fastembed/{model_name}", probe)

    @property
    def model_identity(self) -> str:
        return self._identity

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(x) for x in vec] for vec in self._model.embed(list(texts))]
