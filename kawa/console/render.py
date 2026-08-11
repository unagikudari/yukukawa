"""Render the Operator Console page from live projections (read-only).

Queries the disposable `current_*` tables on every call — never a cached/embedded snapshot. The
execution dimensions are shown separately (never collapsed to one health light); dependency routing
is drawn from `current_work_dependency`; projection freshness is surfaced, not hidden.
"""
from __future__ import annotations

import html

import psycopg

# execution state -> (label, css class). Kept separate; no single "health" rollup.
_EXEC = {
    "idle": ("idle", "mut"), "ready": ("READY", "ok"), "executing": ("executing", "ok"),
    "retryable": ("retryable", "warn"), "blocked": ("BLOCKED", "crit"),
    "execution_unknown": ("EXEC-UNKNOWN", "inc"), "result_recorded": ("result ✓", "warn"),
    "finished": ("finished", "done"),
}


def _fetch(conn: psycopg.Connection):
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref, objective, lifecycle FROM current_plans ORDER BY plan_ref")
        plans = cur.fetchall()
        cur.execute("SELECT work_ref, plan_ref, work_kind, role_requirement, execution, "
                    "dependency_total, dependency_satisfied, dependency_conflicted "
                    "FROM current_work ORDER BY plan_ref, work_ref")
        work = cur.fetchall()
        cur.execute("SELECT work_ref, dependency_work_ref, dependency_state "
                    "FROM current_work_dependency")
        deps = cur.fetchall()
        cur.execute("SELECT count(*) FROM events")
        events = cur.fetchone()[0]
        cur.execute("SELECT max(latest_recorded_at) FROM current_plans")
        fresh = cur.fetchone()[0]
    return plans, work, deps, events, fresh


def _dispatch_html(conn: psycopg.Connection) -> str:
    """Runtime & Dispatch — how Work was routed to runtimes/lanes, from work_dispatch (live)."""
    from collections import Counter
    with conn.cursor() as cur:
        cur.execute("SELECT work_ref, target_agent, transport, dispatch_state FROM work_dispatch "
                    "WHERE target_agent IS NOT NULL ORDER BY updated_at DESC NULLS LAST, work_ref")
        rows = cur.fetchall()
    if not rows:
        return ""
    tr = " · ".join(f"{html.escape(t)} {n}" for t, n in sorted(Counter(r[2] for r in rows).items()))
    trows = "".join(
        f'<tr><td class="wr">{html.escape(w)}</td><td class="meta">{html.escape(a)}</td>'
        f'<td><span class="pill">{html.escape(t)}</span></td>'
        f'<td><span class="st {"done" if st == "completed" else "mut"}">{html.escape(st)}</span></td></tr>'
        for w, a, t, st in rows)
    return ('<section class="plan"><h2>Runtime &amp; Dispatch</h2>'
            f'<p class="obj">how Work was routed to runtimes / lanes — from work_dispatch (live) · {tr}</p>'
            '<table class="disp"><thead><tr><th>work</th><th>lane (runtime)</th><th>transport</th>'
            f'<th>state</th></tr></thead><tbody>{trows}</tbody></table></section>')


def render_page(conn: psycopg.Connection) -> str:
    plans, work, deps, events, fresh = _fetch(conn)
    by_plan: dict[str, list] = {}
    for w in work:
        by_plan.setdefault(w[1], []).append(w)
    dep_by_work: dict[str, list] = {}
    for d in deps:
        dep_by_work.setdefault(d[0], []).append((d[1], d[2]))

    rows = []
    for plan_ref, objective, lifecycle in plans:
        rows.append(f'<section class="plan"><h2>{html.escape(plan_ref)} '
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
            rows.append(f'<div class="wi"><span class="wr">{html.escape(wref)}</span>'
                        f'<span class="meta">{html.escape(kind)}'
                        + (f' · {html.escape(role)}' if role else "") + "</span>"
                        f'<span class="st {cls}">{lbl}</span>{depnote}</div>')
        rows.append("</div></section>")

    fresh_s = html.escape(str(fresh)[:19]) if fresh else "—"
    body = ("\n".join(rows) or '<p class="mut">No plans yet.</p>') + _dispatch_html(conn)
    return _TEMPLATE.format(events=events, fresh=fresh_s, body=body, nplans=len(plans), nwork=len(work))


_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kawa Operator Console</title>
<style>
:root{{--bg:#0D1117;--panel:#161B22;--bd:#30363D;--tx:#E6EDF3;--mut:#8B949E;
--ok:#3FB950;--warn:#D29922;--crit:#F85149;--inc:#A371F7;--done:#2DD4BF}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 ui-sans-serif,system-ui,sans-serif}}
.top{{display:flex;gap:18px;align-items:baseline;padding:14px 20px;border-bottom:1px solid var(--bd)}}
.top h1{{font-size:16px;margin:0}}.top .live{{margin-left:auto;color:var(--mut);font-size:12px;font-family:ui-monospace,monospace}}
.wrap{{padding:16px 20px;max-width:1100px}}
.plan{{background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;margin-bottom:12px}}
.plan h2{{font-size:14px;margin:0 0 2px;font-family:ui-monospace,monospace}}
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
table.disp{{width:100%;border-collapse:collapse;font-size:12px;margin-top:4px}}
table.disp th{{text-align:left;color:var(--mut);font-weight:500;padding:3px 6px;border-bottom:1px solid var(--bd)}}
table.disp td{{padding:3px 6px;border-bottom:1px solid var(--bd)}}
</style></head><body>
<div class="top"><h1>Kawa Operator Console</h1>
<span class="mut" style="font-size:12px">live projection · reads current_* every request</span>
<span class="live">{nplans} plans · {nwork} work · {events} events · as-of {fresh}</span></div>
<div class="wrap">{body}</div></body></html>"""
