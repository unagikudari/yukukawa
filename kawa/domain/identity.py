"""IdentityContext — the trusted-identity boundary for the write path (#72 Phase 1, #73 Phase A).

The Domain caller supplies *intent*; it does NOT supply trusted provenance. Trusted identity is
established once, at the runtime boundary, as an `IdentityContext`, and Emit stamps `origin_node` /
`actor_ref` from it. A caller of the Kawa service surface cannot choose `origin_node`.

`assurance` states HOW the identity is established — and is honest about the Phase-0 reality:
`unattested` means the local runtime asserted it with no credential check yet. Phase 4A replaces the
factory body with real credential / JWT / TPM verification WITHOUT changing the Emit path — the point
of routing all identity through this single seam.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Assurance = Literal["unattested", "software_key", "tpm"]


@dataclass(frozen=True)
class IdentityContext:
    """Trusted write-path identity. Immutable; established at the runtime boundary, not per call."""

    node_ref: str
    actor_ref: str
    assurance: Assurance = "unattested"

    def __post_init__(self) -> None:
        if not self.node_ref or not self.actor_ref:
            raise ValueError("IdentityContext requires a non-empty node_ref and actor_ref")

    @classmethod
    def from_local_runtime(
        cls, *, node_ref: str, actor_ref: str, assurance: Assurance = "unattested"
    ) -> "IdentityContext":
        """Establish identity from the trusted local runtime.

        Phase 0.x: the deployer/runtime declares the node identity; `assurance='unattested'` is the
        honest level — there is no credential verification yet. This is the ONLY seam Phase 4A must
        change to introduce attested Node identity (credential/JWT/TPM); the Emit path stays fixed.
        """
        return cls(node_ref=node_ref, actor_ref=actor_ref, assurance=assurance)
