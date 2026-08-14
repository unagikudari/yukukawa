"""Step 10 PR 1 acceptance (#118 r2 split): the two authority event kinds are first-class,
replicated history — emit/load/wire round-trips, reducer-inertness, vocabulary registration.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.events import AuthorityConfiguration, AuthorityReceipt
from kawa.domain.identity import IdentityContext
from kawa.domain.ids import canonical_json, digest
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import pull, read_stream
from kawa.storage.wire import from_wire, to_wire

psycopg = pytest.importorskip("psycopg")

from tests.test_archive import conn_a, conn_b, _fresh  # noqa: E402,F401 (shared fixtures)


def _proof(cred, statement: str) -> str:
    return canonical_json({"signer_set": [cred.signing_key_ref],
                           "signatures": [cred.sign(statement)]})


@pytest.fixture()
def node_a(conn_a, tmp_path):  # type: ignore[no-untyped-def]
    cred = load_or_create_local_node(str(tmp_path / "a.json"), node_ref="node-a")
    kawa = Kawa(conn_a, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a"))
    keys = PublicKeyRegistry(str(tmp_path / "b-keys.json"))
    trust = TrustRegistry(str(tmp_path / "b-trust.json"))
    keys.register(cred.signing_key_ref, cred.public_pem())
    trust.enroll("node-a", cred.signing_key_ref)
    return kawa, cred, keys, trust


def test_authority_events_round_trip_and_replicate(conn_a, conn_b, node_a, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = node_a
    cfg_digest = digest({"authority_key": "authority:fork-resolution", "epoch": 0,
                         "members": [cred.signing_key_ref], "quorum": 1})
    cfg = kawa._emit_reduce(AuthorityConfiguration(
        authority_key="authority:fork-resolution", configuration_digest=cfg_digest,
        authority_epoch=0, members=[cred.signing_key_ref], quorum=1,
        succession_proof=_proof(cred, cfg_digest)))
    op_digest = digest({"op": "fork.resolve", "origin_node": "node-x", "origin_seq": 7,
                        "chosen_head": "sha256:aa", "configuration_digest": cfg_digest,
                        "policy_digest": None})
    rec = kawa._emit_reduce(AuthorityReceipt(
        authority_key="authority:fork-resolution", operation_digest=op_digest,
        configuration_digest=cfg_digest, authority_epoch=0,
        quorum_proof=_proof(cred, op_digest)))
    conn_a.commit()
    # emit → DB → load round-trip (payload tables + loader)
    events = read_stream(conn_a, {})
    by_id = {e.event_id: e for e in events}
    assert by_id[cfg.event_id].payload == cfg.payload
    assert by_id[rec.event_id].payload == rec.payload
    # wire round-trip through a JSON hop
    for ev in (cfg, rec):
        w = json.loads(json.dumps(to_wire(by_id[ev.event_id])))
        assert from_wire(w).payload == ev.payload
    # they are fleet-scoped v2 history (the rev-2 (a) resolution) and replicate through
    # the ordinary steps-8/9 pipeline
    assert cfg.envelope_version == 2 and cfg.scope_ref == "fleet"
    r = pull(conn_b, conn_a, keys=keys, trust=trust,
             source_trust=trust, puller_node="node-a", scopes=("fleet",))
    # (serving grants: reuse the same registry — node-a's enrollment granted fleet)
    assert {cfg.event_id, rec.event_id} <= set(r.admitted)
    # reducer-inert: no projection rows moved on either node (proof material, not Domain state)
    for conn in (conn_a, conn_b):
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM current_plans")
            assert cur.fetchone()[0] == 0


def test_vocabulary_and_taxonomy_registered() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    vocab = json.loads((root / "registry" / "vocabulary.json").read_text(encoding="utf-8"))
    assert {"authority.configuration", "authority.receipt"} <= set(vocab["event_types"])
    taxonomy = (root / "docs" / "event-taxonomy-v0.2.md").read_text(encoding="utf-8")
    assert "authority.configuration" in taxonomy and "authority.receipt" in taxonomy