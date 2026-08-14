"""Step 8C acceptance (#111): authenticated pull over a real wire, byte-preserving format.

Server A serves kawa_test_a over loopback HTTP; client B (own DB, own credential, own
registries) pulls. Skips when the test DBs are absent, like test_replication.py.
"""
from __future__ import annotations

import json
import os
import threading
import time
import unicodedata
import urllib.error
import urllib.request

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import frontier, read_stream
from kawa.storage.wire import WireVerificationError, from_wire, to_wire
from kawa.adapters.replication_http import (
    PullAuthorizer,
    _request_digest,
    pull_http,
    serve,
)

psycopg = pytest.importorskip("psycopg")

from tests.test_replication import _ALL  # noqa: E402


def _fresh(dsn_env: str, default: str):  # type: ignore[no-untyped-def]
    dsn = os.environ.get(dsn_env, default)
    try:
        c = psycopg.connect(dsn, autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"replication test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    return c, dsn


@pytest.fixture()
def mesh(tmp_path):  # type: ignore[no-untyped-def]
    """Two REAL nodes: A (server) and B (client), each with its own DB, credential, and
    receiver-local registries; each has the other enrolled."""
    conn_a, dsn_a = _fresh("KAWA_TEST_DSN_A", "dbname=kawa_test_a")
    conn_b, _ = _fresh("KAWA_TEST_DSN_B", "dbname=kawa_test_b")
    cred_a = load_or_create_local_node(str(tmp_path / "a.json"), node_ref="node-a")
    cred_b = load_or_create_local_node(str(tmp_path / "b.json"), node_ref="node-b")
    kawa_a = Kawa(conn_a, identity=IdentityContext.from_local_node(cred_a, actor_ref="agent-a"), default_scope=None)
    # A's server-side registries judge PULL CLIENTS; B's registries judge admitted EVENTS.
    keys_a = PublicKeyRegistry(str(tmp_path / "a-keys.json"))
    trust_a = TrustRegistry(str(tmp_path / "a-trust.json"))
    keys_a.register(cred_b.signing_key_ref, cred_b.public_pem())
    trust_a.enroll("node-b", cred_b.signing_key_ref)
    keys_b = PublicKeyRegistry(str(tmp_path / "b-keys.json"))
    trust_b = TrustRegistry(str(tmp_path / "b-trust.json"))
    keys_b.register(cred_a.signing_key_ref, cred_a.public_pem())
    trust_b.enroll("node-a", cred_a.signing_key_ref)

    authorizer = PullAuthorizer(keys_a, trust_a)
    httpd = serve(dsn_a, authorizer)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield dict(conn_a=conn_a, conn_b=conn_b, kawa_a=kawa_a, cred_a=cred_a, cred_b=cred_b,
               keys_b=keys_b, trust_b=trust_b, authorizer=authorizer, base=base)
    httpd.shutdown()
    conn_a.close()
    conn_b.close()


def _events(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT event_id FROM events ORDER BY origin_node, origin_seq")
        return [r[0] for r in cur.fetchall()]


def test_two_node_catchup_identical_set_churn_o1(mesh) -> None:  # type: ignore[no-untyped-def]
    m = mesh
    m["kawa_a"].create_plan("p1", "kawa", "one")
    m["kawa_a"].create_plan("p2", "kawa", "two — with unicode: 川は流れる")
    m["conn_a"].commit()
    report = pull_http(m["conn_b"], m["base"], credential=m["cred_b"],
                       keys=m["keys_b"], trust=m["trust_b"])
    assert report.rejected == [] and len(report.admitted) == 2
    assert _events(m["conn_b"]) == _events(m["conn_a"])       # identical event set
    # churn O(1): catching up again is a no-op and A reconfigured nothing to serve it
    again = pull_http(m["conn_b"], m["base"], credential=m["cred_b"],
                      keys=m["keys_b"], trust=m["trust_b"])
    assert again.admitted == [] and again.rejected == []


def test_admission_matrix_holds_over_the_wire(mesh) -> None:  # type: ignore[no-untyped-def]
    m = mesh
    m["kawa_a"].create_plan("p1", "kawa", "pre-revocation trunk")
    m["conn_a"].commit()
    assert pull_http(m["conn_b"], m["base"], credential=m["cred_b"],
                     keys=m["keys_b"], trust=m["trust_b"]).admitted != []
    # the RECEIVER's local judgement gates admission, exactly as in-process (#90):
    m["trust_b"].revoke(m["cred_a"].signing_key_ref)
    m["kawa_a"].create_plan("p2", "kawa", "post-revocation")
    m["conn_a"].commit()
    r = pull_http(m["conn_b"], m["base"], credential=m["cred_b"],
                  keys=m["keys_b"], trust=m["trust_b"])
    assert r.admitted == [] and {x.reason for x in r.rejected} == {"trust_revoked"}


def test_unauthorized_pull_refused_with_no_data(mesh, tmp_path) -> None:  # type: ignore[no-untyped-def]
    m = mesh
    m["kawa_a"].create_plan("p1", "kawa", "protected")
    m["conn_a"].commit()
    a = m["authorizer"]

    def _signed(cred, *, nonce=None, sent_at=None, frontier_map=None, node=None):  # type: ignore[no-untyped-def]
        nonce = a.challenge() if nonce is None else nonce
        sent_at = time.time() if sent_at is None else sent_at
        frontier_map = frontier_map or {}
        dg = _request_digest(nonce, frontier_map, sent_at, ["fleet"], {})
        return {"node": node or cred.node_ref, "key_ref": cred.signing_key_ref,
                "scheme": cred.signature_scheme, "nonce": nonce, "sent_at": sent_at,
                "frontier": frontier_map, "scopes": ["fleet"], "signature": cred.sign(dg)}

    # (1) unsigned / malformed
    assert a.authorize({"node": "node-b"}) == "malformed"
    # (2a) a key the server cannot even resolve: provenance fails first (fail-closed ordering)
    stranger = load_or_create_local_node(str(tmp_path / "s.json"), node_ref="node-s")
    assert a.authorize(_signed(stranger)) == "provenance_invalid"
    # (2b) resolvable but never TRUSTED: provenance passes, standing gates
    a.keys.register(stranger.signing_key_ref, stranger.public_pem())
    assert a.authorize(_signed(stranger)) == "trust_unknown"
    # (3) revoked key
    ok = _signed(m["cred_b"])                    # while still active…
    m["authorizer"].trust.revoke(m["cred_b"].signing_key_ref)
    assert a.authorize(_signed(m["cred_b"])) == "trust_revoked"
    m["authorizer"].trust._m[m["cred_b"].signing_key_ref]["standing"] = "active"  # restore for (4-5)
    # (4) stale timestamp
    assert a.authorize(_signed(m["cred_b"], sent_at=time.time() - 3600)) == "stale"
    # (5) replayed nonce: the first submission consumes it; the byte-identical replay is refused
    assert a.authorize(ok) is None
    assert a.authorize(ok) == "nonce_unknown_or_replayed"
    # (6) a trusted key may not pull as another node
    assert a.authorize(_signed(m["cred_b"], node="node-x")) == "forged_origin"
    # and over the real wire, a refusal is 403 with no data
    req = urllib.request.Request(m["base"] + "/replication/pull",
                                 data=json.dumps({"node": "node-b"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 403
    # an enrolled-active peer still succeeds (the positive control)
    assert pull_http(m["conn_b"], m["base"], credential=m["cred_b"],
                     keys=m["keys_b"], trust=m["trust_b"]).admitted != []


def test_nonce_cache_is_bounded_and_expiring(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # PR #112 review finding 3: challenge-spam must not grow memory without bound, and
    # eviction/expiry always fails CLOSED (unknown nonce -> re-challenge, never acceptance)
    keys = PublicKeyRegistry(str(tmp_path / "k.json"))
    trust = TrustRegistry(str(tmp_path / "t.json"))
    a = PullAuthorizer(keys, trust, window_seconds=60)
    expired = a.challenge(now=0.0)
    for i in range(a.MAX_PENDING_NONCES + 50):           # spam within the window
        a.challenge(now=1.0)
    assert len(a._nonces) <= a.MAX_PENDING_NONCES
    # the expired nonce is gone; a request presenting it is refused
    assert a.authorize({"node": "x", "key_ref": "k", "scheme": "ed25519", "nonce": expired,
                        "sent_at": 61.0, "frontier": {}, "scopes": [], "signature": "s"},
                       now=61.0) == "nonce_unknown_or_replayed"


def test_pull_refusal_is_typed_not_a_raw_crash(mesh) -> None:  # type: ignore[no-untyped-def]
    # PR #112 review finding 4: a refused/unreachable pull surfaces as PullRefused with the
    # server's typed reason — never a raw urllib exception, never a fake empty report
    from kawa.adapters.replication_http import PullRefused
    m = mesh
    m["authorizer"].trust.revoke(m["cred_b"].signing_key_ref)
    with pytest.raises(PullRefused) as err:
        pull_http(m["conn_b"], m["base"], credential=m["cred_b"],
                  keys=m["keys_b"], trust=m["trust_b"])
    assert err.value.reason == "trust_revoked"
    with pytest.raises(PullRefused) as err2:
        pull_http(m["conn_b"], "http://127.0.0.1:1", credential=m["cred_b"],
                  keys=m["keys_b"], trust=m["trust_b"])
    assert err2.value.reason == "unreachable"


def test_wire_verify_before_parse_negative_controls(mesh) -> None:  # type: ignore[no-untyped-def]
    m = mesh
    # NFD payload text: byte-preserving transport keeps it verifiable end-to-end…
    nfd = unicodedata.normalize("NFD", "ガギグゲゴ川")
    m["kawa_a"].create_plan("p-nfd", "kawa", nfd)
    m["conn_a"].commit()
    [event] = read_stream(m["conn_a"], {})
    w = to_wire(event)
    assert from_wire(json.loads(json.dumps(w))).self_hash == event.self_hash
    # …but an NFC-normalized re-rendering of the same text is a DIFFERENT byte sequence,
    # and a re-serialization with different whitespace is too: both FAIL, never re-interpreted
    tampered_nfc = dict(w, payload_canonical=unicodedata.normalize("NFC", w["payload_canonical"]))
    with pytest.raises(WireVerificationError):
        from_wire(tampered_nfc)
    reser = dict(w, payload_canonical=json.dumps(json.loads(w["payload_canonical"]), indent=1))
    with pytest.raises(WireVerificationError):
        from_wire(reser)
    # envelope tamper: self_hash recomputation over received bytes catches it
    lied = dict(w, envelope=dict(w["envelope"], origin_seq=99))
    with pytest.raises(WireVerificationError):
        from_wire(lied)
