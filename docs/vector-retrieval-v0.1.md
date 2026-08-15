# Kawa Vector Retrieval v0.1 — step-11B addendum

Status: current normative addendum — realized per the measured GO of the #122 recall gate
Realizes: `specification-v0.5.md` §10.6's conditional, 11B half (#122 rev 2 (f)-(i) + BC-4/BC-5)
Companions: `retrieval-recall-gate-v0.1.md` (the gate that authorized this), `kawa/retrieval.py` (§10 pipeline this joins), `kawa/embeddings.py` (the substrate)

> The gate said GO (evidence 0.077 / neighborhood 0.333 counted recall, double-blind-agreed
> semantic misses). This document is the mechanism that answered it — and the measured answer:
> **evidence +0.615, neighborhood +0.400, lexical +0.417, zero shadowing regressions.**

## 1. The substrate (`kawa/embeddings.py`, sql/0014)

- **Materialized-only**: extraction reads `events WHERE materialized` — a stub has no bytes to
  embed, so scoped-out records never leak through a similarity side-channel (BC-4).
- **Content identity**: `content_embedding` is keyed `(content_digest, model_identity)` — the
  digest of an event's canonical text. Identical text shares ONE embedding; the attributed
  records never collapse (the no-collapse test drives an anchored query through two
  same-proposition claims and requires both back).
- **model_identity is a behavior digest**: model name + dimensions + sha256 of the model's
  output on a pinned probe sentence. A renamed-but-identical model converges; a silently
  changed model diverges. Changing models re-embeds under the new identity without touching
  old rows.
- **Frontier**: `embedding_frontier` states materialized / with_content / no_content /
  embedded / missing. Kinds with no semantic bytes (links, dependencies, objective-less
  lifecycle, authority plumbing) are `no_content` — stated, never silently absent.

## 2. The indexer (`scripts/embed_index.py`)

Asynchronous, restartable, batch-committed; writes only the two §12.2 derived tables.
Event recording never waits (§10.6 MUST NOT block) — measured: median `record_observation`
0.98ms alone vs 1.03ms with the indexer looping (+0.05ms). `--watch N` keeps it resident.

## 3. The query class (`kawa/retrieval.py::_exec_vector`)

- **Planned LAST** for every plannable intent: structural and lexical classes keep their
  pre-11B budget precedence (Lock 3 remainder order) — vector adds reach, never outranks
  structure (§10.1). The zero-shadowing negative control asserts structural sections are
  byte-identical with and without the index (BC-5).
- **Anchored intents need no live model**: the query vector is the anchor's STORED embedding —
  pure SQL. Textual intents embed live; without an embedder the class is stated as skipped.
- **Provenance (§10.3)**: every record's path carries `vec:<model_identity> sim=<s>`; the
  bundle's `vector_frontier` states the answering model and index coverage; non-firing is
  always in `skipped_classes` with its reason; the row cap surfaces as a frontier entry.
- **Ref space**: results answer in the same domain refs as lexical (plan events → plan_ref,
  work.derived → work_ref, else event id), deduped nearest-first, deterministic tiebreak.
- **Non-epistemic (§10.4)**: similarity is presentation order. The executor reads; it never
  writes standing, links, or events (asserted by test).

## 4. The acceptance re-measurement (#122: the success claim is the delta)

`scripts/measure_vector_delta.py` re-runs the SAME frozen corpus (digest-checked against the
baseline report) through `measure()` with the vector class live, machine-checks zero
shadowing (every formerly-hit label still hit — a regression exits non-zero), and records
per-class `retrieval_recall_delta` Observations plus one `retrieval_vector_no_shadowing`
Observation, source-bound to the corpus digest with the model identity in provenance.

Measured on the dogfood log (corpus sha256:08aa4da1…, model fastembed/BAAI/bge-small-en-v1.5):

```text
class          before  after   delta
evidence        0.000  0.615  +0.615     (the GO class)
neighborhood    0.333  0.733  +0.400     (the GO class)
lexical         0.583  1.000  +0.417
anchor_lookup   0.900  0.900   0.000
standing        0.571  0.571   0.000
19 labels newly hit, 0 regressions (no_shadowing=true)
```

The residual gap (evidence still < bar: objective-less lifecycle events and some
observations sit outside the embeddable substrate) stays measured by the standing gate —
a future recall drop or improvement is a recorded Observation, not a feeling.

## 5. Realized mapping (§25)

```text
sql/0014_vector_retrieval.sql    event_content + content_embedding (§12.2 derived, no FK
                                 to events — projection-table precedent, rebuildable)
kawa/embeddings.py               Embedder profile, canonical text, behavior identity,
                                 extraction + batch embedding, frontier
kawa/retrieval.py                vector query class: planned last, anchored=stored vector,
                                 textual=live, provenance + stated skips
scripts/embed_index.py           restartable non-blocking indexer
scripts/measure_vector_delta.py  frozen-corpus paired delta + zero-shadowing control,
                                 recorded as Observations
tests/test_vector_retrieval.py   no-collapse, model-swap isolation, materialized-only,
                                 stated skips, zero-shadowing, read-only similarity,
                                 domain-ref mapping, deterministic order, honest frontier
```
