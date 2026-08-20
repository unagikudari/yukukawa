"""Step 12B — the standing replica pull cycle (#129 rev 3 F6 + deviation review).

Fixtures encode the 2026-08-15 PRODUCTION measurement, not an invented scenario:
the dogfood log's events were 100% unsigned and a trust-gated pull admitted 0/230
(unsigned at each origin head, predecessor_rejected downstream). The unsigned test
here is that measurement in miniature; the signed test is the state the backfill
produces. Requires the fenced test DBs (see test_replication.py).
"""
from __future__ import annotations

import json
import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import load_or_create_local_node
from kawa.domain.identity import IdentityContext
from scripts.replica_pull import run_cycle

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "content_embedding, event_content, events, event_links, event_link, event_observation, event_claim, event_plan, "
    "event_work, event_work_dependency, event_work_retired, event_result, current_claim_standing, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy, work_dispatch, "
    "security_fork_evidence, result_occurrence_quarantine, security_archive_segment, "
    "event_authority_configuration, event_authority_receipt, policy_lineage, "
    "security_authority_conflict"
)


def _fresh(dsn_env: str, default: str):  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get(dsn_env, default), autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"replica pull test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    return c


@pytest.fixture()
def source():  # type: ignore[no-untyped-def]
    c = _fresh("KAWA_TEST_DSN_A", "dbname=kawa_test_a")
    yield c
    c.close()


@pytest.fixture()
def dest():  # type: ignore[no-untyped-def]
    c = _fresh("KAWA_TEST_DSN_B", "dbname=kawa_test_b")
    yield c
    c.close()


def _cycle(dest, source, tmp_path, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        node_ref="replica-b", actor_ref="replica-pull",
        keys_path=str(tmp_path / "keys.json"), trust_path=str(tmp_path / "trust.json"),
        credential_path=str(tmp_path / "replica-cred.json"),
        status_file=str(tmp_path / "replica-pull.status"), scopes=("fleet",))
    defaults.update(kw)
    return defaults, run_cycle(dest, source, **defaults)


def _observations(conn, predicate):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT value_number, source_revision FROM event_observation "
                    "WHERE predicate=%s", (predicate,))
        return cur.fetchall()


def test_unsigned_source_rejects_loudly(source, dest, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The production measurement, encoded: an unsigned stream admits NOTHING, and the
    cycle says so — ok=false, reject Observation, positive lag. Never a green replica."""
    k = Kawa(source, identity=IdentityContext.from_local_runtime(
        node_ref="node-a", actor_ref="unsigned-legacy"))
    k.create_plan("p1", "kawa", "unsigned history")
    k.derive_work("w1", "p1", "implement")
    source.commit()

    cfg, status = _cycle(dest, source, tmp_path)
    assert status["ok"] is False
    assert status["admitted"] == 0 and status["rejected"] == 2
    assert "unsigned" in status["reject_reasons"]
    assert status["lag"] == {"node-a": 2}          # the gap is visible, not silent

    assert _observations(dest, "replication_frontier_lag")
    rej = _observations(dest, "replication_admission_reject")
    assert len(rej) == 1 and rej[0][0] == 2.0 and "unsigned" in rej[0][1]
    saved = json.loads(open(cfg["status_file"]).read())
    assert saved["ok"] is False and "unsigned" in saved["reject_reasons"]


def test_signed_source_replicates_and_lag_is_zero(source, dest, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The post-backfill PRODUCTION shape: a signed, fleet-scoped-v2 stream (the service
    default since step 10) crosses the full gate WITH the source-trust mirror — payloads
    materialize, lag returns to 0, and the cycle's own Observation is signed at birth."""
    cred = load_or_create_local_node(str(tmp_path / "node-a.json"), node_ref="node-a")
    k = Kawa(source, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a"))
    k.create_plan("p1", "kawa", "signed")
    k.derive_work("w1", "p1", "implement")
    k.record_result("w1", "success", "r1")
    source.commit()

    from kawa.domain.credential import PublicKeyRegistry
    from kawa.domain.trust import TrustRegistry
    cfg = dict(keys_path=str(tmp_path / "keys.json"), trust_path=str(tmp_path / "trust.json"),
               source_trust_path=str(tmp_path / "source-trust.json"), puller_node="replica-b")
    PublicKeyRegistry(cfg["keys_path"]).register(cred.signing_key_ref, cred.public_pem())
    TrustRegistry(cfg["trust_path"]).enroll("node-a", cred.signing_key_ref)
    # the source's serving registry (mirrored replica-side): the fleet grant for the puller
    rcred = load_or_create_local_node(str(tmp_path / "replica-cred.json"), node_ref="replica-b")
    TrustRegistry(cfg["source_trust_path"]).enroll("replica-b", rcred.signing_key_ref)

    _, status = _cycle(dest, source, tmp_path, **cfg)
    assert status["ok"] is True
    assert status["admitted"] == 3 and status["rejected"] == 0
    assert status["lag"] == {"node-a": 0}
    assert status["materialized"] >= 3 and status["stubs"] == 0

    with dest.cursor() as cur:                     # the cycle's own record is signed
        cur.execute("SELECT signing_key_ref FROM events WHERE origin_node='replica-b'")
        assert all(r[0] is not None for r in cur.fetchall())

    _, again = _cycle(dest, source, tmp_path, **cfg)
    assert again["admitted"] == 0 and again["ok"] is True   # idempotent re-pull


def test_scoped_payload_crosses_as_stub_and_is_counted(source, dest, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No serving context => v2 payloads cross as stubs (least-visible, #113 9b) and the
    status splits materialized/stubs (finding 4) — a stub is visible, never a silent gap."""
    cred = load_or_create_local_node(str(tmp_path / "node-a.json"), node_ref="node-a")
    k = Kawa(source, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a"),
             default_scope=None)
    k.create_plan("p1", "kawa", "open")
    source.commit()
    # emit one scoped event through the Emitter directly (the service default is v1)
    from kawa.domain.events import ClaimRecorded
    from kawa.storage.emit import Emitter
    Emitter(source, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a")).emit(
        ClaimRecorded(proposition="scoped", basis_note=None), scope_ref="fleet")
    source.commit()

    cfg = dict(keys_path=str(tmp_path / "keys.json"), trust_path=str(tmp_path / "trust.json"))
    from kawa.domain.credential import PublicKeyRegistry
    from kawa.domain.trust import TrustRegistry
    PublicKeyRegistry(cfg["keys_path"]).register(cred.signing_key_ref, cred.public_pem())
    TrustRegistry(cfg["trust_path"]).enroll("node-a", cred.signing_key_ref)

    _, status = _cycle(dest, source, tmp_path, **cfg)
    assert status["ok"] is True                    # a withheld stub is not a reject
    assert status["stubs"] == 1
    assert status["lag"] == {"node-a": 0}          # the envelope advanced the frontier
