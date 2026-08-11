# Kawa Operator Console — UI Policy

Status: **Normative UI direction for the implemented Console (`kawa/console/`).**
Companion to `kawa-console-design-brief.md` (semantic + visual invariants) and
`kawa-console-north-star.svg` (visual target). This file fixes *how the implementation grows* so the
Console stays coherent as screens are added.

## Direction (owner-confirmed)

1. **Data-driven, incremental.** The implementation aims at the design-brief / north-star, but **a
   screen ships only when its underlying projection carries real data.** No screen is built ahead of
   the data it would show. `Route` and `Dispatch` are live; `Fleet` (needs Phase 4A runtime/attestation),
   `Authority` (needs ⑤ ③④ implemented), and `Graph` (dep-DAG + inferred edges) are `planned` and render
   an honest placeholder — **never mock data.** The pragmatic path is canonical; the brief/north-star are
   the target it converges on.
2. **Dark-only operator tool.** Not theme-aware. A single committed dark operator surface (north-star
   palette below); no light mode. It is an instrument, scanned and operated, not a marketing page.
3. **Converge on the north-star's richer visual.** Move the implementation *toward* the north-star's
   graphical language (cards, severity chips, the INCOMPLETE hatch, an always-visible freshness posture),
   not just plain dense text — while staying information-dense (triage-first, brief §0).

## Binding principles (from the design-brief, enforced in code)

- **Dimensions are never collapsed to one health light.** Execution / authority / epistemic /
  reachability / freshness stay separately legible. No combined green "overall". Severity palette is a
  scale of its own, **never the accent**.
- **INCOMPLETE / unknown is first-class**, with its **own** treatment (violet diagonal **hatch**),
  distinct from pass, fail, *and* from any spinner. Unknown is neither false nor authority.
- **Live + read-only + no mock.** Every screen reads the disposable `current_*` / projection tables per
  request; drop-and-rebuild changes nothing it depends on; planned screens show a placeholder, not fake
  rows. The Console never writes the Domain (DB-enforced reader role is the eventual backstop).
- **Provenance is honest.** event-derived = solid, inferred = dashed; a graph is "a projection, not
  truth". Growth/skill is evidence, never authority (neutral colour, never severity/accent).
- **Freshness is always visible** (perf posture / as-of in the top bar). Reachability ≠ freshness: an
  unreachable node's projected cells read STALE, not red and not green.
- **One shell, one registry.** The sidebar and routes are generated from a single `SCREENS` registry
  (`path, label, fn, implemented?`); adding a screen is one row. Consistent shell across every page.

## North-star palette (dark; tokens the implementation uses)

```text
canvas/panel/card  #0D1117 / #161B22 / #121722      border  #1F2838 / #30363D
text primary/sec/muted/faint  #E6EAF2 / #8A94A6 / #6E7A8C / #4B5568
accent (focus / active-nav ONLY, no status meaning)  #2DD4BF
severity — its own scale, separate from the accent:
  ok        #3FB950 (text #7FD79A)
  warn      #D29922
  crit      #F85149
  INCOMPLETE #A371F7 + hatchIncomplete (diagonal hatch)   text #CBB6F5
  stale     #6E7681 + hatchStale (dot pattern)
  n/a       #6E7681 outline, no fill
```

Type: **monospace** for data / refs / verdicts / node names (alignment, scanability); **sans** for
labels / headings / eyebrows. Hierarchy by weight & size, **not colour** — colour is reserved for
severity and the accent.

## What is deliberately free

Layout, ordering of screens, exact spacing / radii, sparkline styling, which planned screen is built
next — all free, provided the binding principles hold. When a screen graduates from `planned`, it must
bind to real projection columns (`spec/console-binding`), not invent data.

## Onboarding overlay (reconciling #80 with brief §0)

The design-brief keeps onboarding chrome *out of the operator surface*. Proposal #80 wants a first-run
guided tour. Both hold under one rule: **onboarding is an optional, dismissible overlay over the real
dashboard — never persistent chrome, never a separate mock tutorial.**

- Off by default once dismissed; **reopenable from a single persistent `?` / Help control.**
- It **highlights real UI elements on the live dashboard** (dim background → arrow → concise callout →
  Back / Next / Skip). No tutorial mock, no fabricated data (same no-mock rule as every screen).
- It **grows with the screens** (data-driven policy): the tour steps a screen only once that screen is
  implemented with real data — a `planned` screen is not toured.
- Its copy translates correctness mechanisms into user outcomes ("Change the agent. Keep the work." /
  "Show continuity first, explain metaphysics second."), but the operator surface underneath stays
  dense and unchanged. The overlay adds nothing to the DOM weight of normal operation.
