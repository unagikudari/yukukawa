# Publication gate record — 2026-08-18

Run by: the operator's implementer session on the primary dogfood node, under
the owner decision recorded on issue #152 (Strategy B: clean public mirror;
Strategy A rejected). This record satisfies SECURITY.md's requirement that the
completed gate run is repository-tracked evidence; the corresponding Kawa
events (plan `plan-publication-mirror`, its approval-digest Observation and
per-work Results) hold the same facts in typed state.

## Gate points and evidence

1. **Prohibited-content sweep over the tree AND the history to be published — PASS, mechanized.**
   The gate-1 linter (`scripts/lint_publication_boundary.py`, #152, PR #192,
   adversarially reviewed) reports **0 findings with an EMPTY baseline** on the
   tracked tree, and runs in CI alongside the drift lint. The history to be
   published is the mirror export, and `scripts/export_public_mirror.py`
   (PR #193, adversarially reviewed, zero findings) applies the same scanner to
   **every blob of the full exported history** and deletes the export on any
   finding — verified against the real repository (119 commits, clean). History
   rewrite was not used; the private repository is untouched (condition 1).

2. **Discussion-plane disposition — PASS, decided.**
   Owner decision on #152: clean mirror; the public Issue/PR corpus starts
   empty or from intentionally curated public-safe seeds; the historical
   private discussion plane is not mirrored. (Adversarial review of the
   readiness verdict, including the partial falsification of the
   operational-history claim, is recorded in the same thread.)

3. **Private Vulnerability Reporting live from the first public minute — TO BE
   EXECUTED AT PUBLICATION.** The publish step enables PVR on the mirror
   repository in the same operation that creates it, before any announcement.

4. **Security-model maturity markers current — PASS as of this change.**
   `docs/security-model-v0.1.md` §27 (added with this record) labels every
   section enforced / partial / designed / deferred, conservatively (the weaker
   label wherever enforcement is partial or unverified, per this policy's own
   over-claiming rule), each with its evidence anchor.

5. **Policy scope matches the model — PASS.** SECURITY.md's in/out-of-scope
   distinction (vulnerability = claimed-but-unenforced; known limitation =
   labeled designed/deferred) now binds directly to the §27 table, which is the
   authoritative label source.

## Standing conditions carried into the publish step

- Export identity: public noreply only (fail-closed env validation).
- `Source-Commit: <private-sha>` trailers on every public commit; internal
  Kawa events continue to pin private SHAs (condition 7).
- Runtime Situation Awareness / live event-log coordinates stay outside the
  export by design (condition 6).
- The final visibility action requires explicit owner confirmation.
