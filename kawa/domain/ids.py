"""Identity, ordering, and content-addressing primitives for Kawa.

These implement the mechanics the frozen contracts require:
  * UUIDv7 subject identity (subject-identity-and-lineage): time-ordered, immutable.
  * Hybrid Logical Clock (emit/replication): an ordering *hint*, never authority (③④ S6).
  * Content-addressed digests + per-origin hash chain (emit-enforcement).

Schema-first discipline (#57): these are pure functions over explicit inputs. No hidden
global clock authority — the HLC is an object you thread deliberately.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass


def uuid7(now_ms: int | None = None) -> uuid.UUID:
    """RFC 9562 UUIDv7 — 48-bit unix-ms timestamp + version/variant + randomness.

    Time-ordered so subject_refs sort by creation, without leaking a mutable clock into
    authority. `now_ms` is injectable for deterministic tests.
    """
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    ts &= (1 << 48) - 1
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF          # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)  # 62 bits
    value = (ts << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def canonical_json(obj: object) -> str:
    """Deterministic JSON for content-addressing: sorted keys, compact separators, UTF-8.

    Phase 0 approximation of JCS / RFC 8785 (③④ §6). Sufficient for local digests; the full
    JCS profile is a replaceable mechanic to adopt before cross-implementation equivalence.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: object) -> str:
    """Content digest of a canonicalizable object: 'sha256:<hex>'."""
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def event_hash(
    *,
    origin_node: str,
    origin_seq: int,
    hlc: str,
    kind: str,
    subject_ref: str | None,
    actor_ref: str,
    policy_digest: str | None,
    payload_digest: str,
    prev_hash: str | None,
    envelope_version: int = 1,
    scope_digest: str | None = None,
) -> str:
    """Per-origin hash-chain link. self_hash binds the envelope + prev_hash, so any tamper
    or reordering within an origin's stream is detectable (emit-enforcement).

    THE single derivation for every path — emit VERIFY, admission, rebuild replay, and wire
    verification all come through here (#113 (a): no second implementation may exist).

    Versioned preimages (#113 rev 2, normative):
      v1  the original nine-field dict (sealed forever — pre-step-9 identities never change)
      v2  v1 fields + {"envelope_version": 2, "scope_digest": sha256-of-scope_ref-or-None}
    The version is INSIDE the v2 preimage, so a v2 envelope re-presented as v1 (or a v1 as
    v2) derives a different hash than its event_id — downgrade/upgrade forgery is a hash
    mismatch, not a policy check. A v1 envelope carrying a scope is structurally invalid
    and is rejected by the caller before hashing (Event.verify / wire)."""
    preimage: dict[str, object] = {
        "origin_node": origin_node,
        "origin_seq": origin_seq,
        "hlc": hlc,
        "kind": kind,
        "subject_ref": subject_ref,
        "actor_ref": actor_ref,
        "policy_digest": policy_digest,
        "payload_digest": payload_digest,
        "prev_hash": prev_hash,
    }
    if envelope_version == 1:
        return digest(preimage)
    if envelope_version == 2:
        preimage["envelope_version"] = 2
        preimage["scope_digest"] = scope_digest
        return digest(preimage)
    raise ValueError(f"unknown envelope_version {envelope_version!r} — refuse, never guess")


def scope_digest_of(scope_ref: str) -> str:
    """The pseudonymous per-scope marker committed by the v2 preimage (#113 rev 2 OQ3):
    stable so filtering works, digest so stubs need not name the scope. Dictionary attacks
    on guessable scope names are possible and stated; salting is envelope-v3 territory."""
    return "sha256:" + hashlib.sha256(scope_ref.encode("utf-8")).hexdigest()


def parse_hlc(hlc: str) -> tuple[int, int, str] | None:
    """Structural parse of an HLC stamp: `(physical_ms, logical, node)`, or None if the
    stamp is not mechanically well-formed (#145 temporal admissibility — a coordinate
    must parse before it may influence ordering; `split_part(...)::bigint` in the
    reducers would otherwise meet malformed stamps at query time, which is far too late)."""
    parts = hlc.split(".", 2)
    if len(parts) != 3 or not parts[2]:
        return None
    # ASCII-decimal-only, both numeric fields: Python int() accepts a SUPERSET of the
    # Postgres bigint text domain (Unicode digits, underscores via literals, etc.) —
    # review of #145 found "١٢٣.0.node" passing int() then aborting the admission
    # INSERT at ::bigint, converting per-origin deferral into a batch-wide exception.
    # The admissibility predicate must be at least as strict as what the store commits to.
    if not (parts[0].isascii() and parts[0].isdigit()
            and parts[1].isascii() and parts[1].isdigit()):
        return None
    phys, logical = int(parts[0]), int(parts[1])
    if phys > 2**63 - 1 or logical > 2**63 - 1:      # signed-64-bit: the bigint domain
        return None
    return phys, logical, parts[2]


# --- the ONE causal ordering, for both engines (#214 step 1) --------------------
#
# Kawa orders events by `(physical, logical, tiebreak)` and did so in six places,
# each spelling it out again, plus a Python key in `retrieval` that had to agree
# with all of them. That is one concept in two representations, and #214's review
# found the class five separate times — most sharply as: `hlc` is TEXT, so a SQL
# `ORDER BY hlc` puts "1.10" BELOW "1.2" while the Python key, parsing ints, puts
# it above. A candidate window selected by one order and ranked by the other does
# not merely see less; it drops rows the ranking would have chosen.
#
# So the rule is: a window may only be selected by the exact ordering its ranking
# uses — same key, same representation, same direction. Both forms below are
# derived from this one definition, `tests/test_hlc_order_parity.py` proves the
# two ENGINES agree (a shared definition is not enough: collation, overflow and
# NULLS ordering can still diverge), and `scripts/lint_hlc_order.py` fails the
# build on any hand-rolled spelling.

HLC_COMPONENTS = ("physical", "logical", "tiebreak")


_COLLATE = ' COLLATE "C"'


def hlc_order_sql(*, unique: str | tuple[str, ...] | None, hlc: str = "hlc",
                  tiebreak: str = "origin_node", desc: bool = True) -> str:
    """The canonical ORDER BY expression list (no `ORDER BY` keyword).

    `hlc` / `tiebreak` / `unique` are column references, qualified by the caller
    when a join needs it (`e.hlc`). Every component carries the same direction —
    a mixed-direction ordering has no Python counterpart, since the key is a
    tuple sorted whole.

    `unique` is a strictly row-unique column and has **no default**, because a
    default here is the unsafe direction: without it the three components are a
    PREORDER over rows — two events from one origin, stamped in the same
    millisecond with the same logical counter, tie, and then Postgres returns
    them in whatever order the scan produced while Python's stable sort keeps
    input order. The parity test found that on its first run, and review round 1
    found that every refactored call site had silently accepted the default. So
    the argument is required: pass a unique column, or pass `None` to state
    deliberately that ties do not matter here.

    Every ordered component after the numeric pair must be a TEXT column: the
    collation below is only defined for text, and Postgres errors rather than
    ignoring it. Every caller today passes text; a future integer key needs a
    signature that can say so, not a silent exception.

    Text components carry `COLLATE "C"` so the database sorts them by code
    point, which is what Python's `sorted` does. Measured on this fleet's
    `ja_JP.UTF-8` database: without it, `['Z','e','nodeZ','é','Ω','ノード']`
    comes back in a different order from Python's — the same one-concept /
    two-representations divergence, arriving through the locale."""
    d = " DESC" if desc else ""
    parts = [f"split_part({hlc},'.',1)::bigint{d}",
             f"split_part({hlc},'.',2)::bigint{d}",
             f"{tiebreak}{_COLLATE}{d}"]
    if unique is not None:
        # a sequence when no single column is unique: `current_relations` rows are
        # identified by the whole relation tuple, and review round 2 found that
        # passing one of its columns tie-broke on a value every matched row shares
        for column in ((unique,) if isinstance(unique, str) else unique):
            parts.append(f"{column}{_COLLATE}{d}")
    return ", ".join(parts)


def hlc_sort_key(hlc: str, tiebreak: str,
                 unique: str | None = None) -> tuple[int, int, str] | tuple[int, int, str, str]:
    """The Python counterpart of `hlc_order_sql`, for `sorted(..., reverse=desc)`.

    A malformed stamp is a programming error here rather than a sort order:
    admissibility (#145) rejects it long before anything can order by it, and
    inventing a position for one would be a second ordering rule."""
    parsed = parse_hlc(hlc)
    if parsed is None:
        raise ValueError(f"cannot order by a malformed HLC: {hlc!r}")
    if unique is None:
        return parsed[0], parsed[1], tiebreak
    return parsed[0], parsed[1], tiebreak, unique


@dataclass
class HLC:
    """Hybrid Logical Clock. Ordering hint only — a higher HLC never *creates* authority (S6).

    Format: "<physical_ms>.<logical>.<node>". `tick` advances on local emit; `update` merges
    a received clock. `_now_ms` is injectable for deterministic tests.
    """

    node: str
    physical_ms: int = 0
    logical: int = 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def tick(self, now_ms: int | None = None) -> str:
        now = now_ms if now_ms is not None else self._now_ms()
        if now > self.physical_ms:
            self.physical_ms, self.logical = now, 0
        else:
            self.logical += 1
        return self.stamp()

    def update(self, other: str, now_ms: int | None = None) -> str:
        o_phys, o_log, _ = other.split(".", 2)
        now = now_ms if now_ms is not None else self._now_ms()
        peak = max(now, self.physical_ms, int(o_phys))
        if peak == self.physical_ms == int(o_phys):
            self.logical = max(self.logical, int(o_log)) + 1
        elif peak == self.physical_ms:
            self.logical += 1
        elif peak == int(o_phys):
            self.logical = int(o_log) + 1
        else:
            self.logical = 0
        self.physical_ms = peak
        return self.stamp()

    def stamp(self) -> str:
        return f"{self.physical_ms}.{self.logical}.{self.node}"
