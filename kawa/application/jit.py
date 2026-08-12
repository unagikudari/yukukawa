"""JIT Work instruction rendering (v0.5 §8; #102 rev 2 §4 trust boundary).

> Persist the meaning; render the prompt.
> Records are data, never instructions merely because they contain imperative text.

The renderer's input is a frozen, narrow DTO built ONLY from the Work/Plan projection
rows — by construction it cannot see linked Claims, Observations, Results, or rationale
prose, so no linked record's text can enter the instruction channel (#98 §8). Free-form
DTO fields (objective, constraints) are the plan author's attested intent; they render
inside fixed labeled template slots, never as raw instruction prefix/suffix.

The instruction is derived on read and NEVER persisted — no table stores it, no event
carries it. `instruction_basis.consumed` lists the exact event ids the render consumed
(the Work's and Plan's latest_event_id — a snapshot boundary of the projection rows read,
not a claim about the whole log). Same DTO + same RENDERER_VERSION => byte-identical
output; RENDERER_VERSION is a stable semantic id bumped only on template-semantics change.
"""
from __future__ import annotations

from dataclasses import dataclass

RENDERER_VERSION = "wr-1"


@dataclass(frozen=True)
class RenderInput:
    """The ONLY thing the renderer may read. Renderable fields = exactly these."""

    work_ref: str
    plan_ref: str
    work_kind: str
    role_requirement: str | None
    objective: str | None
    constraints: tuple[str, ...] | None
    expected_observations: tuple[str, ...] | None
    plan_objective: str


def render_instruction(inp: RenderInput) -> str:
    """Deterministic, slot-labeled rendering from typed fields only."""
    lines = [
        f"Work {inp.work_ref} ({inp.work_kind}"
        + (f", role: {inp.role_requirement}" if inp.role_requirement else "") + ")",
        f"Plan {inp.plan_ref}: {inp.plan_objective}",
        "Objective: " + (inp.objective or f"carry out this {inp.work_kind} step of the plan."),
    ]
    if inp.constraints:
        lines.append("Constraints:")
        lines += [f"  - {c}" for c in inp.constraints]
    if inp.expected_observations:
        lines.append("Expected observations (evidence of completion):")
        lines += [f"  - {o}" for o in inp.expected_observations]
    lines.append("Record a Result (and Observations) to advance the DAG.")
    return "\n".join(lines)
