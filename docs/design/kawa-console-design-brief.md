# Kawa Operator Console — Design Brief (semantic + visual invariants)

> Handoff for issue #63. North star, **not** pixel-perfect. Companion files:
> `kawa-console-north-star.svg` (visual north star), `kawa-console-screen-map.md`
> (screen → read-model contract). This brief states **what MUST hold** vs **what
> is free** for anyone building the console.
>
> Column-level read-model bindings are deliberately **not** fixed here — the
> read-model (#51) and the operation/effect-identity keystone are still settling.
> Bind to *dimensions*; leave columns TBD. See the screen map.

---

## 0. What the console is (character)

An **information-dense, local-first operator tool** with a **dark-console**
character. It is **scanned and operated, not read top-to-bottom**. The operator
lands, sweeps the surface for the one dimension that is off, and acts. Every
design decision serves fast triage under the perf contract (§6). Prose,
onboarding chrome, and marketing polish are out of scope.

---

## 1. The core invariant: FIVE dimensions, never collapsed

The console renders these as **separate, independently-legible dimensions**. They
are **never** merged into a single status/health bit, a single colour, or a
single "up/down" light. This is the whole point of the tool.

| # | Dimension | What it answers | Never conflated with |
|---|-----------|-----------------|----------------------|
| 1 | **Authority** | Is the operation permitted? (proof of standing) | reachability, skill |
| 2 | **Execution** | Is the work running / stalled / done? | authority, health |
| 3 | **Epistemic standing** | Do we *believe* the underlying claims? contested? stale-gold? | execution success |
| 4 | **Health / reachability** | Can we reach the node / is the process up? | everything below "online" |
| 5 | **Projection freshness / completeness** | How fresh & complete is *what we are looking at*? | truth of the data |

**MUST hold**

- Each dimension has its own visual slot on the Situation strip (the SVG shows
  five cards). A reader can name the state of any one dimension without reading
  the other four.
- No component computes a roll-up that hides a dimension. A green "overall" light
  is forbidden. If a summary number is shown, the non-green residue
  (e.g. `2 INCOMPLETE · 1 INVALID`) is shown beside it, in its own colour.
- **Health/reachability and Projection freshness are distinct.** When a node is
  unreachable (health = CRIT), its downstream projected cells read **STALE**
  (freshness), **not** red and **not** green. Unreachable means "we don't know",
  which is a freshness fact, not a failure of the other dimensions. The SVG's
  `node-e` row is the reference treatment.

**Free**

- Ordering of the five cards, their exact metric per card, sparklines/mini-trends,
  iconography. Whether a dimension also gets a dedicated full screen.

---

## 2. The standing axes & relations (operation/effect identity)

The Authority/Proof surface renders the stable standing axes as **separate
dimensions**, using these **exact axis names** (do not rename, do not merge):

- `authority_standing`
- `effect_standing`
- `consumption_standing`
- `epistemic_standing`

…and the relations, each shown as its own badge:

- `occurrence`
- `execution`
- `scope`
- `outcome`

**MUST hold**

- The four standing axes each get their own labelled row/cell with their own
  state. A focused operation can be `authority_standing:VALID` while
  `effect_standing:INCOMPLETE` — both must be visible simultaneously, never
  reduced to one verdict.
- The four relations are rendered as four distinct badges (`✓ / ? / —`), not a
  single "verified" flag.
- These names are contractual placeholders anchored to the keystone spec. If the
  keystone renames an axis, it is renamed **everywhere** — the console never
  invents a synonym.

**Free**

- Layout of the axis rows (grid vs stack), badge glyphs, how much proof detail
  (hop count, grant chain) is inlined vs behind a drill-in.

---

## 3. INCOMPLETE is first-class (three-state verifier)

The verifier is **three-state**: `VALID` / `INVALID` / `INCOMPLETE`
(per agent consistency-and-authority §5.1). `INCOMPLETE` means **the verifier
lacks inputs to decide** — it is a terminal, actionable state, **not** "still
loading".

**MUST hold**

- `INCOMPLETE` has its **own visual treatment**, distinct from both pass and fail
  **and** distinct from any progress/spinner affordance. In the north star it is
  a **violet diagonal-hatch** fill with a violet outline. A spinner for
  INCOMPLETE is a defect.
- The treatment must state **why** it is incomplete (which inputs are missing)
  and **how many** (`inputs 3/5`), because the operator's next action is to
  supply the missing input, not to retry.
- `INCOMPLETE` propagates: a fleet cell whose attestation cannot be evaluated is
  INCOMPLETE (hatch), not amber and not grey-stale. (Stale = we had an answer and
  it aged; Incomplete = we cannot answer yet. Keep them visually separate.)

**Free**

- The exact hatch angle/spacing, whether the count is a chip or inline, drill-in
  content for the missing-inputs list.

---

## 4. Graph is a projection, not truth

The Knowledge Graph / Decision-Lineage surface is a **projection**. Edges have
two provenances that MUST be visually distinct:

- **event-derived** edges — solid stroke (drawn from the event log / facts).
- **inferred** edges — dashed stroke (model inference over the projection).

**MUST hold**

- An operator can tell, per edge, whether it is grounded in an event or inferred.
  The two never share a style.
- Inferred nodes/edges carry the projection accent (violet family), never the
  severity palette, so inference is never mistaken for a health/authority signal.
- The surface is labelled as a projection ("projection, not truth") on the frame.

**Free**

- Layout algorithm, zoom/pan, node glyphs, how lineage depth is paged.

---

## 5. Growth / skill score is EVIDENCE, never authority

A growth or skill score describes what an agent *has demonstrated*. It **never**
widens what an agent may *do*.

**MUST hold**

- Skill/growth is rendered in the **neutral track colour** — never in the
  severity palette, never in the brand accent.
- The surface carries an explicit `evidence · not authority` marker.
- No code path reads a growth score as an input to an authority decision. The
  Authority/Proof panel is the *only* source of "may do". (If the read-model ever
  exposes a join between skill and grant, the console must not render it as
  permission.)

**Free**

- How growth is trended (per-agent bars, deltas, windows), sort order.

---

## 6. Local-first performance posture (perf contract, #50)

The console is **local-first and fast**. Freshness of the *view* is a
first-class, always-visible fact — not hidden latency.

**MUST hold**

- Read-model queries target **p95 < 40ms**, with a hard **< 1s ceiling** for any
  interaction. Anything slower degrades to showing *stale-but-labelled* data
  rather than blocking.
- The current perf posture is **always on screen** (top bar: `p95`, `<40ms ✓`,
  last-refresh age). Freshness is dimension #5 — it is shown, not assumed.
- Reads come from **projections** (materialised read-models), never live fan-out
  to nodes on the render path. A slow/unreachable node makes its projection
  STALE; it never stalls the console.
- No spinner substitutes for data on the primary surfaces. Prefer last-known +
  freshness label over a blocking load state.

**Free**

- Caching strategy, refresh cadence per surface, prefetch, virtualised tables.

---

## 7. Visual language (dark console)

### Palette — named hex

**Structure (neutral, non-semantic):**

| Token | Hex | Use |
|-------|-----|-----|
| `bg/canvas` | `#0B0E14` | app background |
| `bg/rail` | `#0A0D13` | left rail |
| `bg/panel` | `#0F141E` | panel body |
| `bg/card` | `#121722` | situation card / inset |
| `bg/inset` | `#101722` | rows, chips (neutral) |
| `line/hairline` | `#1B2333` | dividers |
| `line/border` | `#1F2838` / `#232B3A` | panel & control borders |

**Type:**

| Token | Hex | Use |
|-------|-----|-----|
| `text/primary` | `#E6EAF2` | values, headings |
| `text/secondary` | `#8A94A6` / `#9AA4B6` | labels |
| `text/muted` | `#5C6577` | captions, units |
| `text/faint` | `#4B5568` | footnotes, TBD notes |

**Brand accent — focus/navigation ONLY, carries no status meaning:**

| Token | Hex | Use |
|-------|-----|-----|
| `accent/brand` | `#2DD4BF` (teal) | brand mark, active-nav bar, focus ring |
| `accent/brand-lo` | `#5EEAD4` | active-nav glyph |

**Severity — its OWN scale, kept strictly separate from the accent:**

| State | Dot/fill | Text | Meaning |
|-------|----------|------|---------|
| ok | `#3FB950` | `#7FD79A` | healthy / VALID / current |
| warn | `#D29922` | `#E0B34A` | degraded / lag / backlog |
| crit | `#F85149` | `#F0837C` | failed / INVALID / unreachable |
| **incomplete** | `#A371F7` + hatch (`hatchIncomplete`) | `#CBB6F5` | verifier lacks inputs (first-class) |
| stale/unknown | `#6E7681` + dot-pattern (`hatchStale`) | `#8A94A6` | had an answer, aged out / no data |
| ineligible / n-a | `#6E7681` outline, no fill | `#8A94A6` | structurally not applicable |

**MUST hold:** the accent hue (teal) never encodes a state, and no severity hue
is ever used for branding or navigation. Green is *only* "ok"; violet-hatch is
*only* INCOMPLETE; the projection/inference accent lives in the violet family and
never in green/amber/red. This separation is what lets the operator trust colour.

### Type roles

- **Data / values / verdicts / node names:** monospace
  (`ui-monospace, Menlo, monospace`) — alignment and scanability.
- **Labels / headings / dimension names:** sans (`Inter, system-ui`),
  uppercase + letter-spacing for section eyebrows.
- Hierarchy by **weight and size**, not colour — colour is reserved for severity
  and accent. A big number is big; it is not coloured to mean "good".

**Free:** exact font choices (any comparable mono + grotesk), spacing scale,
corner radii, shadow depth, density knob.

---

## 8. Quick MUST / FREE recap

**MUST** — five dimensions never merged · the four standing-axis names verbatim ·
INCOMPLETE first-class & non-spinner · reachability ≠ freshness · graph edges
event-vs-inferred distinct · growth ≠ authority · severity palette separate from
accent · perf posture always visible, reads from projections.

**FREE** — layout, ordering, iconography, fonts, spacing, drill-in depth, trend
visuals, density, refresh cadence, table virtualisation, which dimensions earn a
dedicated screen.
