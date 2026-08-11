"""Node credential + cryptographic provenance (Phase 4A — attested Event origin).

**Cryptographic provenance != current trust standing.** A valid signature proves only that the Event's
canonical `content_hash` cryptographically corresponds to a particular origin credential ("bound to
this key"). Whether that key is *currently trusted* — policy, revocation, key lineage — is a SEPARATE
evaluation (③④), deliberately NOT done here. `signature valid` never means `trusted`.

Boundaries honoured (owner-set):
- The signed object is fixed to the canonical `content_hash` (= the Event's self_hash), never arbitrary
  bytes and never the Event identity itself (identity stays `event_id = content_hash`).
- The mechanism is a replaceable **profile**: Ed25519 software key is the first assurance level;
  TPM / HSM / SPIFFE swap in behind `NodeCredential` without touching Emit. `signature_scheme` is a
  mechanics string, never a Core Domain enum.
- `signing_key_ref` is stored per Event so a key **registry** resolves the historical public key —
  rotation / revocation of the current key does not break verification of past Events.
- Verification failure returns False; the caller (admission / replication) MUST fail closed — an
  invalid or unknown-scheme signature is never accepted as "unknown".
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


@runtime_checkable
class NodeCredential(Protocol):
    """A replaceable signing profile. Swap Ed25519-software for TPM/HSM/SPIFFE without touching Emit."""

    node_ref: str
    signing_key_ref: str
    signature_scheme: str

    def sign(self, content_hash: str) -> str:
        """Hex signature over the canonical `content_hash` (the Event self_hash)."""
        ...


def _fingerprint(pub: Ed25519PublicKey) -> str:
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "ed25519:" + hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class Ed25519NodeCredential:
    """First assurance profile: a software-held Ed25519 key. Maps to IdentityContext assurance='software_key'."""

    node_ref: str
    signing_key_ref: str
    _private: Ed25519PrivateKey
    signature_scheme: str = "ed25519"

    def sign(self, content_hash: str) -> str:
        return self._private.sign(content_hash.encode("utf-8")).hex()

    def public_pem(self) -> str:
        return self._private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()


def load_or_create_local_node(path: str, node_ref: str | None = None) -> Ed25519NodeCredential:
    """Load the node's local credential, generating one on first run.

    The private key is OPERATOR data — a mode-0600 local file, git-ignored, never the repo
    (publication-boundary). `node_ref` binds to the key's fingerprint when not explicitly enrolled.
    """
    if os.path.exists(path):
        data = json.loads(open(path, encoding="utf-8").read())
        priv = serialization.load_pem_private_key(data["private_pem"].encode(), password=None)
        assert isinstance(priv, Ed25519PrivateKey)
        return Ed25519NodeCredential(node_ref=data["node_ref"], signing_key_ref=data["signing_key_ref"],
                                     _private=priv)
    priv = Ed25519PrivateKey.generate()
    kref = _fingerprint(priv.public_key())
    nref = node_ref or kref
    priv_pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"node_ref": nref, "signing_key_ref": kref, "private_pem": priv_pem}, f)
    os.chmod(path, 0o600)
    cred = Ed25519NodeCredential(node_ref=nref, signing_key_ref=kref, _private=priv)
    return cred


class PublicKeyRegistry:
    """signing_key_ref -> public_pem, append-only and retaining rotated/revoked keys.

    Retention is what makes past Events verifiable after key rotation: an Event carries the
    `signing_key_ref` that signed it, and the registry still holds that key's public half even once the
    node has rotated to a new key. Registry membership is provenance-resolution ONLY — it is NOT a
    trust list (a revoked key stays here so old Events verify; *current trust* is evaluated elsewhere).
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._map: dict[str, str] = {}
        if os.path.exists(path):
            self._map = json.loads(open(path, encoding="utf-8").read())

    def register(self, signing_key_ref: str, public_pem: str) -> None:
        if self._map.get(signing_key_ref) == public_pem:
            return
        self._map[signing_key_ref] = public_pem  # append-only; never drop a historical key
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._map, f)

    def public_pem(self, signing_key_ref: str) -> str | None:
        return self._map.get(signing_key_ref)


def verify_provenance(content_hash: str, signature_hex: str, public_pem: str,
                      signature_scheme: str = "ed25519") -> bool:
    """CRYPTOGRAPHIC PROVENANCE only: True iff `signature` corresponds to `content_hash` under the key.

    True means "bound to this key", NOT "trusted". Trust standing (policy / revocation / key lineage)
    is a separate evaluation, not performed here. Any failure — bad signature, unknown scheme, malformed
    input — returns False; the caller MUST fail closed, never accept it as unknown.
    """
    if signature_scheme != "ed25519":
        return False  # unknown scheme -> fail closed; do not guess a verifier
    try:
        pub = serialization.load_pem_public_key(public_pem.encode())
        if not isinstance(pub, Ed25519PublicKey):
            return False
        pub.verify(bytes.fromhex(signature_hex), content_hash.encode("utf-8"))
        return True
    except Exception:
        return False  # InvalidSignature / malformed / anything -> fail closed


def resolve_and_verify_provenance(content_hash: str, signature_hex: str, signing_key_ref: str,
                                  signature_scheme: str, registry: PublicKeyRegistry) -> bool:
    """Rotation-safe provenance: resolve the historical public key by `signing_key_ref`, then verify.

    Still provenance, not trust. A missing key_ref -> False (fail closed)."""
    pem = registry.public_pem(signing_key_ref)
    if pem is None:
        return False
    return verify_provenance(content_hash, signature_hex, pem, signature_scheme)
