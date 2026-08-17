"""Render the Operator Console from live projections (read-only), with a shared sidebar shell.

Each screen is a page (Route, Dispatch, ...). The sidebar is generated ONCE from `SCREENS` and shared
across every page — add a screen by adding a row to `SCREENS`. Screens read the disposable `current_*`
projections on every request (no static snapshot). Reads only; never writes. Screens designed but not
yet implemented are shown in the sidebar marked `planned` and render an honest placeholder — never mock
data.
"""
from __future__ import annotations

import html
from collections import Counter

import psycopg

# execution state -> (label, css class). Kept separate; never collapsed to one health light.
_EXEC = {
    "idle": ("idle", "mut"), "ready": ("READY", "ok"), "executing": ("executing", "ok"),
    "retryable": ("retryable", "warn"), "blocked": ("BLOCKED", "crit"),
    "execution_unknown": ("EXEC-UNKNOWN", "inc"), "result_recorded": ("result ✓", "warn"),
    "finished": ("finished", "done"),
}


# ---- screens: each returns the inner HTML for its page (read-only) ----

# situation/fleet state -> css class. Five dimensions, four+1 states — never merged.
_DIM_CLS = {"OK": "ok", "ATTENTION": "warn", "CRIT": "crit", "UNKNOWN": "unk",
            "INCOMPLETE": "inc"}
_DIM_ORDER = ("AUTHORITY", "EXECUTION", "EPISTEMIC", "HEALTH_REACH", "PROJECTION_FRESHNESS")
_DIM_LABEL = {"AUTHORITY": "Authority", "EXECUTION": "Execution", "EPISTEMIC": "Epistemic",
              "HEALTH_REACH": "Health / Reach", "PROJECTION_FRESHNESS": "Projection freshness"}


def _screen_situation(conn: psycopg.Connection) -> str:
    """The landing sweep (screen-map §1): five dimension cards from situation_rollup —
    each its own card with its own freshness; no combined health exists to render."""
    with conn.cursor() as cur:
        cur.execute("SELECT dimension, state, gap, count_total, count_nongreen, residue, "
                    "source_projection, as_of FROM situation_rollup")
        rows = {r[0]: r[1:] for r in cur.fetchall()}
    if not rows:
        return ('<section class="card"><h2>Situation</h2><p class="obj">situation_rollup is '
                'empty — no refresh has run yet (rebuild or the supervisor populates it). '
                'Nothing is invented in its place.</p></section>')
    cards = []
    missing = [d for d in _DIM_ORDER if d not in rows]
    for dim in _DIM_ORDER:
        if dim not in rows:                                # partial rollup: absence is stated,
            continue                                       # never painted over with a state
        state, gap, total, nongreen, residue, src, as_of = rows[dim]
        cls = _DIM_CLS.get(state, "mut")
        body = []
        if gap:                                            # INCOMPLETE: named gap, never a spinner
            body.append(f'<p class="obj">gap: {html.escape(gap)}</p>')
        if total is not None:
            body.append(f'<p class="num">{nongreen}<span class="den">/{total} non-green</span></p>')
        if residue:                                        # counts travel WITH their residue
            body.append(f'<p class="obj resid">{html.escape(residue)}</p>')
        cards.append(
            f'<section class="card dim"><h2>{html.escape(_DIM_LABEL[dim])} '
            f'<span class="st {cls}">{html.escape(state)}</span></h2>'
            + "".join(body)
            + f'<p class="asof">from {html.escape(src)} · as-of {html.escape(str(as_of)[:19])}</p>'
            '</section>')
    head = ""
    if missing:
        head = ('<section class="card"><p class="obj">situation_rollup holds no row for: '
                + html.escape(", ".join(missing))
                + ' — refresh incomplete; nothing is invented in its place.</p></section>')
    return head + '<div class="cards">' + "".join(cards) + "</div>"


def _screen_fleet(conn: psycopg.Connection) -> str:
    """Per-node, per-dimension chips (screen-map §4). A node is never one light.
    STALE is derived HERE, at render: measured reachability=CRIT makes sibling
    cells STALE (their stored, still-measured values shown as stale freshness),
    while UNKNOWN reachability stales nothing."""
    with conn.cursor() as cur:
        cur.execute("SELECT node_ref, reachability, reachability_source, reachability_as_of, "
                    "workload, workload_detail, workload_as_of, "
                    "replication, replication_lag, replication_as_of, "
                    "last_origin_seq, last_event_at, updated_at "
                    "FROM fleet_node ORDER BY node_ref")
        rows = cur.fetchall()
    if not rows:
        return ('<section class="card"><h2>Fleet</h2><p class="obj">fleet_node is empty — '
                'no refresh has run yet. Nothing is invented in its place.</p></section>')

    def chip(label: str, state: str, as_of, extra: str = "", stale: bool = False) -> str:
        if stale and state != "UNKNOWN":
            # freshness statement, not a health verdict: the measured value is shown, marked stale
            asof = f' · {html.escape(str(as_of)[:19])}' if as_of else ""
            return (f'<span class="st stale">{html.escape(label)} STALE '
                    f'(last {html.escape(state)}{extra}{asof})</span>')
        cls = {"OK": "ok", "ATTENTION": "warn", "CRIT": "crit", "CURRENT": "ok",
               "LAGGING": "warn", "UNKNOWN": "unk"}.get(state, "mut")
        asof = f' · {html.escape(str(as_of)[:19])}' if as_of else ""
        return f'<span class="st {cls}">{html.escape(label)} {html.escape(state)}{extra}{asof}</span>'

    out = ['<section class="card"><p class="obj">per-node × per-dimension — online ≠ workload '
           'healthy ≠ replication current. attestation / trust / cell / quorum: deferred with '
           'their read-models (no cell is invented). UNKNOWN = no admissible source event yet.</p>'
           '<div class="work">']
    for (node, rch, rch_src, rch_at, wkl, wkl_d, wkl_at,
         rep, rep_lag, rep_at, seq, last_at, upd) in rows:
        siblings_stale = rch == "CRIT"                    # the ONLY stale trigger (never UNKNOWN)
        chips = [
            chip("RCH", rch, rch_at, f' ({html.escape(rch_src)})' if rch_src else ""),
            chip("WKL", wkl, wkl_at, f' ({html.escape(wkl_d)})' if wkl_d else "", stale=siblings_stale),
            chip("REP", rep, rep_at, f' lag={rep_lag}' if rep_lag is not None else "", stale=siblings_stale),
        ]
        out.append(f'<div class="wi fleet"><span class="wr">{html.escape(node)}</span>'
                   f'<span class="meta">seq {seq} · last event {html.escape(str(last_at)[:19])}</span>'
                   f'<span class="chips">{"".join(chips)}</span></div>')
    out.append(f'</div><p class="asof">as-of {html.escape(str(rows[0][12])[:19])} · '
               'refreshed by rebuild / supervisor tick</p></section>')
    return "\n".join(out)


def _screen_route(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref, objective, lifecycle FROM current_plans ORDER BY plan_ref")
        plans = cur.fetchall()
        cur.execute("SELECT work_ref, plan_ref, work_kind, role_requirement, execution, "
                    "dependency_total, dependency_satisfied, dependency_conflicted "
                    "FROM current_work ORDER BY plan_ref, work_ref")
        work = cur.fetchall()
        cur.execute("SELECT work_ref, dependency_work_ref, dependency_state FROM current_work_dependency")
        deps = cur.fetchall()
    by_plan: dict = {}
    for w in work:
        by_plan.setdefault(w[1], []).append(w)
    dep_by_work: dict = {}
    for d in deps:
        dep_by_work.setdefault(d[0], []).append((d[1], d[2]))
    out = []
    for plan_ref, objective, lifecycle in plans:
        out.append(f'<section class="card"><h2>{html.escape(plan_ref)} '
                   f'<span class="pill">{html.escape(lifecycle)}</span></h2>'
                   f'<p class="obj">{html.escape(objective or "")}</p><div class="work">')
        for w in by_plan.get(plan_ref, []):
            wref, _, kind, role, execu, dt, ds, dc = w
            lbl, cls = _EXEC.get(execu, (execu, "mut"))
            depnote = ""
            if dt:
                dstates = ", ".join(f"{html.escape(dw)}:{html.escape(st)}" for dw, st in dep_by_work.get(wref, []))
                depnote = (f'<div class="dep">deps {ds}/{dt}'
                           + (f' · <span class="conf">{dc} conflicted</span>' if dc else "")
                           + (f' · {dstates}' if dstates else "") + "</div>")
            out.append(f'<div class="wi"><span class="wr">{html.escape(wref)}</span>'
                       f'<span class="meta">{html.escape(kind)}'
                       + (f' · {html.escape(role)}' if role else "") + "</span>"
                       f'<span class="st {cls}">{lbl}</span>{depnote}</div>')
        out.append("</div></section>")
    return "\n".join(out) or '<p class="mut">No plans yet.</p>'


def _screen_dispatch(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT work_ref, target_agent, transport, dispatch_state FROM work_dispatch "
                    "WHERE target_agent IS NOT NULL ORDER BY updated_at DESC NULLS LAST, work_ref")
        rows = cur.fetchall()
    if not rows:
        return '<p class="mut">No dispatches yet.</p>'
    tr = " · ".join(f"{html.escape(t)} {n}" for t, n in sorted(Counter(r[2] for r in rows).items()))
    trows = "".join(
        f'<tr><td class="wr">{html.escape(w)}</td><td class="meta">{html.escape(a)}</td>'
        f'<td><span class="pill">{html.escape(t)}</span></td>'
        f'<td><span class="st {"done" if st == "completed" else "mut"}">{html.escape(st)}</span></td></tr>'
        for w, a, t, st in rows)
    return ('<section class="card"><p class="obj">how Work was routed to runtimes / lanes — '
            f'from work_dispatch (live) · {tr}</p>'
            '<table class="disp"><thead><tr><th>work</th><th>lane (runtime)</th><th>transport</th>'
            f'<th>state</th></tr></thead><tbody>{trows}</tbody></table></section>')


def _screen_planned(conn: psycopg.Connection) -> str:
    return ('<section class="card"><h2>Planned</h2><p class="obj">This screen is designed but not '
            'implemented yet — see <code>docs/design/</code> and <code>spec/console-binding</code>. '
            'It will render from the live read-model when built; no mock data is shown.</p></section>')


def _screen_search(conn: psycopg.Connection, query: dict | None = None) -> str:
    """Narrow harness over kawa.retrieval.retrieve (#100): renders the bundle WITH its
    provenance and frontiers. No stable external schema — the step-6 MCP surface replaces
    this with no compatibility promise."""
    from kawa.retrieval import FLEET_SCOPES, Intent, retrieve
    q = (query or {})
    about, text = q.get("about", [""])[0].strip(), q.get("q", [""])[0].strip()
    form = ('<section class="card"><h2>Search (SQL-first, structure before similarity)</h2>'
            '<form method="get" action="/search">'
            f'<input name="about" placeholder="ref (event id / plan_ref / work_ref)" value="{html.escape(about)}" size="44"> '
            f'<input name="q" placeholder="text terms" value="{html.escape(text)}" size="28"> '
            '<button type="submit">retrieve</button></form></section>')
    if not about and not text:
        return form
    # console is a local read harness answering under fleet visibility — explicit, not defaulted
    bundle = retrieve(conn, Intent(about=about or None, text_terms=text or None),
                      viewer_scopes=FLEET_SCOPES)
    p = bundle.plan
    parts = [form, '<section class="card"><h2>Retrieval plan (provenance header)</h2><p class="obj">'
             + html.escape(f"anchor={p.bound.anchor_kind or '—'} textual={p.bound.unbound_text is not None} | "
                           + ", ".join(f"{qc.class_id}[{qc.backend} b={qc.budget}"
                                       + (f" {qc.fts_reason}" if qc.fts_reason else "") + "]"
                                       for qc in p.query_classes)) + "</p></section>"]
    for class_id, records in bundle.sections.items():
        rows = "".join(
            f"<tr><td>{html.escape(r.kind)}</td><td>{html.escape(r.summary[:100])}</td>"
            f"<td>{html.escape(r.standing or '—')}</td><td class=\"obj\">{html.escape(r.path[:110])}"
            + (" <b>[truncated]</b>" if r.path_truncated else "") + "</td></tr>"
            for r in records)
        parts.append(f'<section class="card"><h2>{html.escape(class_id)} ({len(records)})</h2>'
                     '<table class="disp"><thead><tr><th>kind</th><th>record</th><th>standing</th>'
                     f'<th>via</th></tr></thead><tbody>{rows}</tbody></table></section>')
    notes = []
    if bundle.empty_classes:
        notes.append("ran empty: " + ", ".join(bundle.empty_classes))
    if bundle.skipped_classes:
        notes.append("deferred, not fired: " + ", ".join(bundle.skipped_classes))
    if bundle.traversal_frontier:
        reasons = Counter(f.reason for f in bundle.traversal_frontier)
        notes.append("not expanded: " + ", ".join(f"{k}×{v}" for k, v in sorted(reasons.items())))
    if bundle.unresolved_frontier:
        notes.append(f"unresolved links (targets not held): {len(bundle.unresolved_frontier)}")
    if bundle.fts_queries:
        notes.append("fts: " + "; ".join(bundle.fts_queries))
    if notes:
        parts.append('<section class="card"><h2>Not retrieved / how retrieved</h2><p class="obj">'
                     + html.escape(" · ".join(notes)) + "</p></section>")
    return "".join(parts)


# path, sidebar label, screen fn, implemented? — the ONE place screens & sidebar are defined.
def _screen_archive(conn: psycopg.Connection) -> str:
    """#113 9c: custody evidence + the age of the latest restore proof — §12.3's "commitment
    existence does not prove durability" made visible. Staleness gates nothing (no GC
    exists); it is operator information."""
    with conn.cursor() as cur:
        cur.execute("SELECT origin_node, from_seq, to_seq, detached, verified_at, "
                    "archiver_key_ref FROM security_archive_segment "
                    "ORDER BY origin_node, from_seq")
        segments = cur.fetchall()
        cur.execute(
            "SELECT eo.value_bool, eo.source_ref, eo.fetched_at, "
            "clock_timestamp() - eo.fetched_at::timestamptz AS age "
            "FROM event_observation eo JOIN events e ON e.event_id = eo.event_id "
            "WHERE eo.predicate = 'archive_restore_ok' AND e.materialized "
            "ORDER BY eo.fetched_at DESC LIMIT 10")
        proofs = cur.fetchall()
    out = ['<section class="card"><h2>Segment commitments</h2><div class="work">']
    for onode, lo, hi, detached, vat, kref in segments:
        state = ('<span class="st mut">detached</span>' if detached
                 else '<span class="st ok">chained</span>')
        out.append(f'<div class="wi"><span class="wr">{html.escape(onode)} [{lo}..{hi}]</span>'
                   f'<span class="meta">verified {html.escape(str(vat)[:19])} · '
                   f'{html.escape(kref or "")}</span>{state}</div>')
    if not segments:
        out.append('<p class="mut">No archived segments.</p>')
    out.append('</div></section><section class="card"><h2>Restore proofs</h2><div class="work">')
    for ok, src, fat, age in proofs:
        cls = "ok" if ok else "crit"
        out.append(f'<div class="wi"><span class="wr">{html.escape(src or "")}</span>'
                   f'<span class="meta">proof age {html.escape(str(age).split(".")[0])}</span>'
                   f'<span class="st {cls}">{"ok" if ok else "FAILED"}</span></div>')
    if not proofs:
        out.append('<p class="mut">No restore proofs recorded — durability is an assumption '
                   'until scripts/archive_verify.py records one.</p>')
    out.append("</div></section>")
    return "\n".join(out)


SCREENS = [
    ("/", "Situation", _screen_situation, True),
    ("/route", "Route", _screen_route, True),
    ("/fleet", "Fleet", _screen_fleet, True),
    ("/dispatch", "Dispatch", _screen_dispatch, True),
    ("/search", "Search", _screen_search, True),
    ("/archive", "Archive", _screen_archive, True),
    ("/graph", "Graph", _screen_planned, False),
    ("/authority", "Authority", _screen_planned, False),
]
_BY_PATH = {p: (label, fn, impl) for p, label, fn, impl in SCREENS}


def _header_stats(conn: psycopg.Connection):
    """Shell tallies from projections ONLY (w-console3 constraint: screens never read
    raw event tables). The event tally is the fleet ledger position — sum of per-origin
    last_origin_seq from fleet_node — shown as absence ('—') until a refresh has run."""
    with conn.cursor() as cur:
        cur.execute("SELECT sum(last_origin_seq) FROM fleet_node")
        events = cur.fetchone()[0]
        if events is None:
            events = "—"
        cur.execute("SELECT count(*) FROM current_plans")
        nplans = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM current_work")
        nwork = cur.fetchone()[0]
        cur.execute("SELECT max(latest_recorded_at) FROM current_plans")
        fresh = cur.fetchone()[0]
    return events, nplans, nwork, (html.escape(str(fresh)[:19]) if fresh else "—")


# Sibling operator surfaces on the same node, keyed by port — the console is one
# pane of the operator picture; the broker UI (plans / wiki / fleet) is the other.
# The hostname comes from the request Host header at render time, so the links
# resolve to whichever node is serving this console (nothing node-specific here).
SURFACES = [
    ("Broker", 8000, "/dashboards"),
    ("Plans", 8000, "/w/plans"),
    ("Wiki", 8000, "/w"),
]


def _sidebar(active_path: str, host: str | None = None) -> str:
    items = []
    for p, label, _fn, impl in SCREENS:
        cls = "nav-item" + (" active" if p == active_path else "") + ("" if impl else " planned")
        tag = "" if impl else '<span class="soon">planned</span>'
        items.append(f'<a class="{cls}" href="{p}">{html.escape(label)}{tag}</a>')
    hostname = (host or "").split(":")[0] or "127.0.0.1"
    items.append('<div class="side-sec">surfaces</div>')
    for label, port, p in SURFACES:
        href = f"http://{html.escape(hostname)}:{port}{p}"
        items.append(f'<a class="nav-item" href="{href}">{html.escape(label)}'
                     '<span class="soon">↗</span></a>')
    return '<nav class="side"><div class="brand">Kawa</div>' + "".join(items) + "</nav>"


def render(conn: psycopg.Connection, path: str = "/", query: dict | None = None,
           host: str | None = None) -> str | None:
    """Render the page for `path`, or None if the path is not a known screen (404)."""
    entry = _BY_PATH.get(path)
    if entry is None:
        return None
    label, fn, _impl = entry
    events, nplans, nwork, fresh = _header_stats(conn)
    content = fn(conn, query) if fn is _screen_search else fn(conn)
    return _TEMPLATE.format(active=html.escape(label), sidebar=_sidebar(path, host), content=content,
                            nplans=nplans, nwork=nwork, events=events, fresh=fresh)


def render_page(conn: psycopg.Connection) -> str:
    """Back-compatible entry: the landing screen (Situation — screen-map §1's sweep)."""
    return render(conn, "/")  # type: ignore[return-value]


_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kawa Console — {active}</title>
<style>
:root{{--bg:#0D1117;--side:#0A0D13;--panel:#161B22;--card:#121722;--bd:#30363D;--tx:#E6EDF3;--mut:#8B949E;
--ok:#3FB950;--warn:#D29922;--crit:#F85149;--inc:#A371F7;--done:#2DD4BF;--accent:#2DD4BF}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 ui-sans-serif,system-ui,sans-serif;display:flex;min-height:100vh}}
.side{{width:184px;flex:0 0 184px;background:var(--side);border-right:1px solid var(--bd);
padding:14px 10px;display:flex;flex-direction:column;gap:3px}}
.side .brand{{font-weight:700;font-size:15px;padding:4px 10px 12px;letter-spacing:.02em}}
.nav-item{{display:flex;align-items:center;justify-content:space-between;color:var(--mut);
text-decoration:none;padding:6px 10px;border-radius:6px;font-size:13px}}
.nav-item:hover{{background:var(--panel);color:var(--tx)}}
.nav-item.active{{background:var(--panel);color:var(--tx);box-shadow:inset 2px 0 0 var(--accent)}}
.nav-item.planned{{opacity:.5}}
.side-sec{{margin-top:auto;padding:12px 10px 4px;font-size:10px;text-transform:uppercase;
letter-spacing:.08em;color:var(--mut);border-top:1px solid var(--bd)}}
.soon{{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
border:1px solid var(--bd);border-radius:8px;padding:0 5px}}
.main{{flex:1;min-width:0;display:flex;flex-direction:column}}
.top{{display:flex;gap:16px;align-items:baseline;padding:13px 20px;border-bottom:1px solid var(--bd)}}
.top h1{{font-size:15px;margin:0}}
.top .live{{margin-left:auto;color:var(--mut);font-size:12px;font-family:ui-monospace,monospace}}
.wrap{{padding:16px 20px;max-width:1000px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;margin-bottom:12px}}
.card h2{{font-size:14px;margin:0 0 2px;font-family:ui-monospace,monospace}}
.obj{{margin:0 0 8px;color:var(--mut);font-size:12px}}
.pill{{font-size:11px;color:var(--mut);border:1px solid var(--bd);border-radius:10px;padding:1px 8px}}
.wi{{display:grid;grid-template-columns:220px 1fr auto;gap:10px;align-items:center;padding:5px 0;border-top:1px solid var(--bd)}}
.wr{{font-family:ui-monospace,monospace;font-size:12px}}.meta{{color:var(--mut);font-size:12px}}
.dep{{grid-column:1/-1;color:var(--mut);font-size:11px;font-family:ui-monospace,monospace}}
.st{{font-size:11px;font-weight:600;font-family:ui-monospace,monospace;justify-self:end;padding:1px 8px;border-radius:10px;border:1px solid}}
.st.ok{{color:#7FD79A;border-color:var(--ok);background:rgba(63,185,80,.10)}}
.st.warn{{color:#E0B34A;border-color:var(--warn);background:rgba(210,153,34,.10)}}
.st.crit{{color:#F0837C;border-color:var(--crit);background:rgba(248,81,73,.10)}}
/* INCOMPLETE is first-class: violet diagonal HATCH (north-star hatchIncomplete), never a plain fill or spinner */
.st.inc{{color:#CBB6F5;border-color:var(--inc);background:repeating-linear-gradient(45deg,rgba(163,113,247,.20),rgba(163,113,247,.20) 3px,transparent 3px,transparent 7px)}}
.st.done{{color:var(--done);border-color:var(--done);background:rgba(45,212,191,.10)}}
.st.mut{{color:var(--mut);border-color:var(--bd)}}
/* UNKNOWN = absence-of-evidence: neutral, DASHED — never green, never red */
.st.unk{{color:var(--mut);border:1px dashed var(--mut);background:transparent}}
/* STALE = a freshness statement (render-derived from reachability CRIT), not a verdict */
.st.stale{{color:var(--mut);border:1px solid var(--bd);background:repeating-linear-gradient(-45deg,rgba(139,148,158,.12),rgba(139,148,158,.12) 3px,transparent 3px,transparent 7px);font-style:italic}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.card.dim h2{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.num{{font-size:22px;margin:6px 0 2px;font-family:ui-monospace,monospace}}
.num .den{{font-size:11px;color:var(--mut);margin-left:4px}}
.resid{{word-break:break-all}}
.asof{{margin:8px 0 0;color:var(--mut);font-size:10px;font-family:ui-monospace,monospace}}
.wi.fleet{{grid-template-columns:120px 1fr;grid-auto-rows:auto}}
.chips{{grid-column:1/-1;display:flex;gap:6px;flex-wrap:wrap;justify-self:start;padding:2px 0 4px}}
.conf{{color:var(--crit)}}.mut{{color:var(--mut)}}
table.disp{{width:100%;border-collapse:collapse;font-size:12px}}
table.disp th{{text-align:left;color:var(--mut);font-weight:500;padding:3px 6px;border-bottom:1px solid var(--bd)}}
table.disp td{{padding:3px 6px;border-bottom:1px solid var(--bd)}}
code{{font-family:ui-monospace,monospace;font-size:12px}}
</style></head><body>
{sidebar}
<div class="main"><div class="top"><h1>{active}</h1>
<span class="mut" style="font-size:12px">live projection · reads current_* every request</span>
<span class="live">{nplans} plans · {nwork} work · {events} events · as-of {fresh}</span></div>
<div class="wrap">{content}</div></div></body></html>"""
