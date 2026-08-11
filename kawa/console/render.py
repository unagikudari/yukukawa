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


# path, sidebar label, screen fn, implemented? — the ONE place screens & sidebar are defined.
SCREENS = [
    ("/", "Route", _screen_route, True),
    ("/dispatch", "Dispatch", _screen_dispatch, True),
    ("/fleet", "Fleet", _screen_planned, False),
    ("/graph", "Graph", _screen_planned, False),
    ("/authority", "Authority", _screen_planned, False),
]
_BY_PATH = {p: (label, fn, impl) for p, label, fn, impl in SCREENS}


def _header_stats(conn: psycopg.Connection):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        events = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM current_plans")
        nplans = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM current_work")
        nwork = cur.fetchone()[0]
        cur.execute("SELECT max(latest_recorded_at) FROM current_plans")
        fresh = cur.fetchone()[0]
    return events, nplans, nwork, (html.escape(str(fresh)[:19]) if fresh else "—")


def _sidebar(active_path: str) -> str:
    items = []
    for p, label, _fn, impl in SCREENS:
        cls = "nav-item" + (" active" if p == active_path else "") + ("" if impl else " planned")
        tag = "" if impl else '<span class="soon">planned</span>'
        items.append(f'<a class="{cls}" href="{p}">{html.escape(label)}{tag}</a>')
    return '<nav class="side"><div class="brand">Kawa</div>' + "".join(items) + "</nav>"


def render(conn: psycopg.Connection, path: str = "/") -> str | None:
    """Render the page for `path`, or None if the path is not a known screen (404)."""
    entry = _BY_PATH.get(path)
    if entry is None:
        return None
    label, fn, _impl = entry
    events, nplans, nwork, fresh = _header_stats(conn)
    return _TEMPLATE.format(active=html.escape(label), sidebar=_sidebar(path), content=fn(conn),
                            nplans=nplans, nwork=nwork, events=events, fresh=fresh)


def render_page(conn: psycopg.Connection) -> str:
    """Back-compatible entry: the default (Route) screen."""
    return render(conn, "/")  # type: ignore[return-value]


_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kawa Console — {active}</title>
<style>
:root{{--bg:#0D1117;--side:#0A0D13;--panel:#161B22;--bd:#30363D;--tx:#E6EDF3;--mut:#8B949E;
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
.soon{{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
border:1px solid var(--bd);border-radius:8px;padding:0 5px}}
.main{{flex:1;min-width:0;display:flex;flex-direction:column}}
.top{{display:flex;gap:16px;align-items:baseline;padding:13px 20px;border-bottom:1px solid var(--bd)}}
.top h1{{font-size:15px;margin:0}}
.top .live{{margin-left:auto;color:var(--mut);font-size:12px;font-family:ui-monospace,monospace}}
.wrap{{padding:16px 20px;max-width:1000px}}
.card{{background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;margin-bottom:12px}}
.card h2{{font-size:14px;margin:0 0 2px;font-family:ui-monospace,monospace}}
.obj{{margin:0 0 8px;color:var(--mut);font-size:12px}}
.pill{{font-size:11px;color:var(--mut);border:1px solid var(--bd);border-radius:10px;padding:1px 8px}}
.wi{{display:grid;grid-template-columns:220px 1fr auto;gap:10px;align-items:center;padding:5px 0;border-top:1px solid var(--bd)}}
.wr{{font-family:ui-monospace,monospace;font-size:12px}}.meta{{color:var(--mut);font-size:12px}}
.dep{{grid-column:1/-1;color:var(--mut);font-size:11px;font-family:ui-monospace,monospace}}
.st{{font-size:11px;font-weight:600;font-family:ui-monospace,monospace;justify-self:end;padding:1px 8px;border-radius:10px;border:1px solid}}
.st.ok{{color:var(--ok);border-color:var(--ok)}}.st.warn{{color:var(--warn);border-color:var(--warn)}}
.st.crit{{color:var(--crit);border-color:var(--crit)}}.st.inc{{color:var(--inc);border-color:var(--inc)}}
.st.done{{color:var(--done);border-color:var(--done)}}.st.mut{{color:var(--mut);border-color:var(--bd)}}
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
