"""#140 V1 — kawa version <subject> (plan-version-read rev 4 acceptance).

Grammar/containment/taxonomy are pure and tested directly; DB facets run against the fenced
test DB; git interaction is tested by substituting the _git seam (never by dirtying the real
working tree from a test)."""
from __future__ import annotations

import os

import psycopg
import pytest

from kawa import version_read as vr
from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE content_embedding, event_content, events, event_links, "
                    "event_link, event_observation, event_claim, event_plan, event_work, "
                    "event_work_dependency, event_work_retired, event_result, "
                    "current_claim_standing, current_plans, current_work, "
                    "current_work_dependency, runtime_work_occupancy, work_dispatch")
    c.commit()
    yield c
    c.close()


# ---- subject grammar & taxonomy (rev 4 §1/§2/§5) ----

def _err(arg):  # type: ignore[no-untyped-def]
    with pytest.raises(vr.SubjectError) as e:
        vr.parse_subject(arg) if ":" not in (arg or "") or arg.startswith(("node", "schema", "package")) \
            else None
    return e.value


def test_no_argument_is_the_local_node_explicitly() -> None:
    assert vr.parse_subject(None) == ("node", None)


def test_malformed_subject_exits_2_and_names_the_vocabulary() -> None:
    with pytest.raises(vr.SubjectError) as e:
        vr.parse_subject("bogus")
    assert e.value.exit_code == vr.EXIT_MALFORMED and "vocabulary" in str(e.value)
    for bad in ("schema:x", "package:x", "doc:", "policy:"):
        with pytest.raises(vr.SubjectError) as e:
            vr.parse_subject(bad)
        assert e.value.exit_code == vr.EXIT_MALFORMED


def test_doc_containment_rejects_escape_as_malformed() -> None:
    for bad in ("../CLAUDE.md", "/etc/passwd", "a\\b.md"):
        with pytest.raises(vr.SubjectError) as e:
            vr._resolve_doc(bad)
        assert e.value.exit_code == vr.EXIT_MALFORMED


def test_missing_doc_is_unknown_subject_not_malformed() -> None:
    with pytest.raises(vr.SubjectError) as e:
        vr._resolve_doc("no-such-doc-ever.md")
    assert e.value.exit_code == vr.EXIT_UNKNOWN_SUBJECT


def test_unknown_policy_lists_the_registry() -> None:
    with pytest.raises(vr.SubjectError) as e:
        vr.version_read("policy:nope")
    assert e.value.exit_code == vr.EXIT_UNKNOWN_SUBJECT and "durability" in str(e.value)


def test_non_local_node_is_unknown_in_v1() -> None:
    with pytest.raises(vr.SubjectError) as e:
        vr.version_read("node:definitely-not-this-node")
    assert e.value.exit_code == vr.EXIT_UNKNOWN_SUBJECT and "local node only" in str(e.value)


# ---- closed schemas & vocabularies (rev 4 §3, #142 invariants) ----

def test_subject_facet_registry_is_exact() -> None:
    """The exactness pin: a facet added in code without a registry decision fails HERE."""
    assert vr.SUBJECT_FACETS == {
        "node": ("build", "event_frontier", "logical_time", "policy_context", "schema_revision"),
        "doc": ("build", "document_currency"),
        "policy": ("build", "policy_context"),
        "schema": ("schema_revision",),
        "package": ("build", "package"),
    }
    assert set(vr.SUBJECT_FACETS) == set(vr.SUBJECT_KINDS)
    assert "contract_versions" not in {f for fs in vr.SUBJECT_FACETS.values() for f in fs}


def test_every_facet_speaks_the_closed_vocabulary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KAWA_DSN", os.environ["KAWA_TEST_DSN_A"])
    read = vr.version_read(None)
    assert set(read["facets"]) == set(vr.SUBJECT_FACETS["node"])   # in-schema facets ALL present
    for f in read["facets"].values():
        assert f["status"] in ("known", "unknown")
        assert f["basis_kind"] in vr.BASIS_KINDS
        if f["status"] == "unknown":
            assert f["reason"] in vr.REASON_CODES


# ---- loud unknowns (rev 4 §4/§5) ----

def test_unreachable_db_is_a_loud_typed_unknown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KAWA_DSN", "dbname=definitely_no_such_db host=127.0.0.1 connect_timeout=1")
    read = vr.version_read(None)
    assert read["facets"]["event_frontier"] == {"status": "unknown", "basis_kind": "unknown",
                                                "reason": "db_unreachable"}
    line = vr.render_compact(read)
    assert "frontier:UNKNOWN(db_unreachable)" in line               # loud WITHOUT --json
    assert vr.exit_code_for(read) == vr.EXIT_AUTHORITY_FAILURE      # loud at process level


def test_dirty_tree_is_never_claimed_clean(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(vr, "_git", lambda args: " M docs/x.md" if "status" in args else "")
    assert vr._tree_state("/x") == "worktree_dirty"
    monkeypatch.setattr(vr, "_git", lambda args: "")
    assert vr._tree_state("/x") == "git_clean_blob"
    def boom(args):  # type: ignore[no-untyped-def]
        raise RuntimeError("git broken")
    monkeypatch.setattr(vr, "_git", boom)
    assert vr._tree_state("/x") == "worktree_dirty"                 # cannot prove clean => not clean


# ---- rebuild invariance & external purity (rev 4 §—, split criterion) ----

def test_log_derived_facets_are_rebuild_invariant(conn, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from kawa.projections.reducers import rebuild
    # seed BEFORE KAWA_DSN is set: sign-at-birth refuses unattested emits against a live
    # target (the 12A fence — the refusal below is the mechanism working, not a bug)
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="vtest", actor_ref="pytest"))
    k.create_plan("pv", "kawa", "version fixture")
    k.derive_work("wv", "pv", "implement")
    monkeypatch.setenv("KAWA_DSN", os.environ["KAWA_TEST_DSN_A"])
    before = vr.version_read(None)["facets"]
    rebuild(conn)
    conn.commit()
    after = vr.version_read(None)["facets"]
    for f in ("event_frontier", "logical_time", "schema_revision"):
        assert before[f] == after[f]                                # bit-identical after rebuild


def test_the_read_never_writes(conn, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="vtest", actor_ref="pytest"))
    k.create_plan("pv2", "kawa", "purity fixture")
    monkeypatch.setenv("KAWA_DSN", os.environ["KAWA_TEST_DSN_A"])
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        n0 = cur.fetchone()[0]
    vr.version_read(None)
    vr.version_read("schema")
    vr.version_read("package")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        assert cur.fetchone()[0] == n0                              # derived read: zero events emitted


def test_package_facet_is_annotated_non_system() -> None:
    read = vr.version_read("package")
    pkg = read["facets"]["package"]
    if pkg["status"] == "known":
        assert "NOT the system version" in pkg["value"]["note"]
    assert "(non-system)" in vr.render_compact(read) or "UNKNOWN" in vr.render_compact(read)
