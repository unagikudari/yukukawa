"""Phase 0 domain-layer tests — run without a database.

Covers the mechanics the frozen contracts require: UUIDv7 identity, HLC ordering, content
digests, and the per-origin hash chain via Event.verify().
"""
from __future__ import annotations

from kawa.domain.events import Event, PlanCreated, WorkDerived
from kawa.domain.ids import HLC, digest, event_hash, uuid7


def test_uuid7_is_version7_and_time_ordered() -> None:
    a = uuid7(now_ms=1_000)
    b = uuid7(now_ms=2_000)
    assert a.version == 7 and b.version == 7
    assert (a.int >> 62) & 0b11 == 0b10  # RFC 9562 variant
    assert a < b  # earlier timestamp sorts first


def test_hlc_tick_is_monotone() -> None:
    c = HLC(node="node-a")
    s1 = c.tick(now_ms=100)
    s2 = c.tick(now_ms=100)  # same physical ms -> logical increments
    s3 = c.tick(now_ms=101)  # advancing physical ms -> logical resets
    assert s1 == "100.0.node-a"
    assert s2 == "100.1.node-a"
    assert s3 == "101.0.node-a"


def test_hlc_update_merges_remote() -> None:
    c = HLC(node="a", physical_ms=100, logical=0)
    merged = c.update("105.3.b", now_ms=100)  # remote ahead
    assert merged == "105.4.a"


def test_digest_is_deterministic_and_key_order_independent() -> None:
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})
    assert digest({"a": 1}).startswith("sha256:")


def test_typed_payload_rejects_extra_fields() -> None:
    import pytest

    with pytest.raises(Exception):
        WorkDerived(work_ref="w1", plan_ref="p1", work_kind="implement", bogus=1)  # type: ignore[call-arg]


def _build_event(payload: object, *, origin_seq: int, prev_hash: str | None) -> Event:
    """Mirror of what storage.emit will do — construct a self-consistent committed Event."""
    p = payload  # typed payload model
    pd = digest(p.model_dump(mode="json"))  # type: ignore[attr-defined]
    sh = event_hash(
        origin_node="node-a",
        origin_seq=origin_seq,
        hlc=f"{origin_seq}.0.node-a",
        kind=p.kind.value,  # type: ignore[attr-defined]
        subject_ref=None,
        actor_ref="vendor-local",
        policy_digest=None,
        payload_digest=pd,
        prev_hash=prev_hash,
    )
    return Event(
        event_id=sh,
        origin_node="node-a",
        origin_seq=origin_seq,
        hlc=f"{origin_seq}.0.node-a",
        kind=p.kind,  # type: ignore[attr-defined]
        subject_ref=None,
        actor_ref="vendor-local",
        policy_digest=None,
        payload_digest=pd,
        prev_hash=prev_hash,
        self_hash=sh,
        payload=p,  # type: ignore[arg-type]
    )


def test_event_verifies_and_chains() -> None:
    e1 = _build_event(
        PlanCreated(plan_ref="p1", project_ref="kawa", objective="dogfood"),
        origin_seq=1,
        prev_hash=None,
    )
    e2 = _build_event(
        WorkDerived(work_ref="w1", plan_ref="p1", work_kind="implement"),
        origin_seq=2,
        prev_hash=e1.self_hash,  # chain link
    )
    assert e1.verify()
    assert e2.verify()
    assert e2.prev_hash == e1.self_hash


def test_tamper_breaks_verification() -> None:
    e = _build_event(
        PlanCreated(plan_ref="p1", project_ref="kawa", objective="dogfood"),
        origin_seq=1,
        prev_hash=None,
    )
    tampered = e.model_copy(update={"actor_ref": "someone-else"})
    assert not tampered.verify()  # self_hash no longer matches the envelope
