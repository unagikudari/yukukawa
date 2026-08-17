"""Fleet telemetry predicate registry — the code mirror of #189 rev 3 §A.

FROZEN by plan review (two adversarial rounds): adding a predicate is a
registry row + this entry, as a reviewed change; the fleet_node_facet schema
never changes for it. Reducers and collectors MUST consult this registry —
an Observation whose predicate is not here is not a telemetry facet and is
never projected into fleet_node_facet (no vocabulary drift by accident).

qualifier kinds:
  none   — unqualified; the projection stores the '' sentinel
  mount  — filesystem mount path
  gpu    — gpu index (stringified)
  label  — operator-assigned probe label (#189 §D: URLs never appear here)
"""
from __future__ import annotations

from typing import NamedTuple


class Facet(NamedTuple):
    value_column: str            # value_number | value_text | value_bool
    unit: str | None
    qualifier_kind: str          # none | mount | gpu | label
    method: str                  # ObservationMethod the collector must use


REGISTRY: dict[str, Facet] = {
    "node_cpu_load1":            Facet("value_number", "load",  "none",  "metric_read"),
    "node_cpu_cores":            Facet("value_number", "count", "none",  "metric_read"),
    "node_mem_total_bytes":      Facet("value_number", "bytes", "none",  "metric_read"),
    "node_mem_available_bytes":  Facet("value_number", "bytes", "none",  "metric_read"),
    "node_disk_total_bytes":     Facet("value_number", "bytes", "mount", "command_exit"),
    "node_disk_free_bytes":      Facet("value_number", "bytes", "mount", "command_exit"),
    "node_gpu_model":            Facet("value_text",   None,    "gpu",   "command_exit"),
    "node_gpu_vram_total_bytes": Facet("value_number", "bytes", "gpu",   "command_exit"),
    "node_gpu_vram_used_bytes":  Facet("value_number", "bytes", "gpu",   "command_exit"),
    "node_reachable":            Facet("value_bool",   None,    "label", "http_probe"),
}
