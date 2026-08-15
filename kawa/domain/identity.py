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

from dataclasses import dataclass, field
from typing import Literal

Assurance = Literal["unattested", "software_key", "tpm"]


@dataclass(frozen=True)
class IdentityContext:
    """Trusted write-path identity. Immutable; established at the runtime boundary, not per call."""

    node_ref: str
    actor_ref: str
    assurance: Assurance = "unattested"
    credential: "object | None" = field(default=None, compare=False, repr=False)

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

    @classmethod
    def from_credential_file(cls, *, actor_ref: str,
                             path: str | None = None) -> "IdentityContext":
        """The production identity (#129 12B): attested from the node credential file.

        Every production entrypoint that EMITS uses this — `emit()` refuses an
        unattested identity once a live target is named (`KAWA_DSN`), so the unsigned
        drift the 2026-08-15 measurement exposed cannot recur silently. The path
        default is the 12A-pinned `~/.kawa/node_credential.json`; `KAWA_CREDENTIAL`
        overrides. `origin_node` comes from the credential, never the caller.
        """
        import os

        from kawa.domain.credential import load_or_create_local_node

        p = path or os.environ.get("KAWA_CREDENTIAL") or "~/.kawa/node_credential.json"
        node = os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0]
        return cls.from_local_node(
            load_or_create_local_node(os.path.expanduser(p), node_ref=node),
            actor_ref=actor_ref)

    @classmethod
    def from_local_node(cls, credential: "object", *, actor_ref: str) -> "IdentityContext":
        """Establish identity from an attested local Node credential (Phase 4A, first assurance profile).

        `origin_node` is bound to the credential (`credential.node_ref`), never caller-declared;
        `assurance='software_key'`. Emit signs each Event's canonical `content_hash` with this
        credential — that is **cryptographic provenance, not current trust standing** (trust is
        evaluated separately: policy / revocation / key lineage). The credential is a replaceable
        profile (Ed25519 software key now; TPM/HSM/SPIFFE later) — the Emit path never changes.
        """
        return cls(node_ref=credential.node_ref, actor_ref=actor_ref, assurance="software_key",
                   credential=credential)
