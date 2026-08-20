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

---

## Addendum, 2026-08-21 — gate point 1's evidence contained a false statement

Appended rather than edited. The boundary policy forbids silently rewriting a
tracked historical record, and this record is evidence for a decision that
already executed; the original text above stands as written.

**The claim.** Gate point 1's evidence says the gate-1 linter "runs in CI
alongside the drift lint", and records the point as PASS.

**It does not, and never did.** Measured 2026-08-21:

```
gh api repos/.../actions/workflows            ->  total_count: 0
gh api repos/.../actions/runs                 ->  total_count: 0
git log --diff-filter=A --all -- .github/workflows/*   ->  empty
```

`.github/workflows/` has never existed on any branch. The workflow YAML lives
in `ci/`, which GitHub Actions does not read. On the day this record was
written, and on every day before and since, no CI has run in this repository.

**What is unaffected.** Point 1's substance does not depend on the false
clause. The linter's result — 0 findings with an empty baseline over the
tracked tree — was and is real, and the load-bearing half of that point is the
EXPORT gate, which applies the same scanner to every blob of the full exported
history and deletes the export on any finding. That runs on every export and
is verified: it failed on 2026-08-20 over 15 historical blobs and refused to
publish. **The gate held. Its description of how did not.**

**What is affected.** The point was recorded as mechanized in a way it was
not. A reader of this record would conclude that a regression in the tracked
tree is caught automatically on every change, and until 2026-08-20 nothing did
that. `tests/test_publication_lint.py::test_the_tracked_tree_has_no_unreviewed_findings`
now does, in the suite.

**A related claim in this record has no basis either.** `ci/security-check.yml`
says activation is "deferred to the publication gate". Neither SECURITY.md's
five points nor this record's standing conditions mention installing or
activating a workflow anywhere. The gate ran on 2026-08-18 and passed all five
points without touching CI installation, so the deferral did not lapse at a
checkpoint — it was never a tracked condition. Calling the arrangement
deliberate is generous in the wrong direction.

**Standing condition added by this addendum.** Installing `ci/*.yml` into
`.github/workflows/` requires an OAuth token with the `workflow` scope, which
the operating token does not carry (verified: push rejected by name, Contents
API 404). Until that is granted and the workflows are installed, no statement
in this repository may describe them as running.
`tests/test_publication_lint.py::test_every_ci_workflow_is_actually_installed`
holds that condition as a strict xfail, so activation forces the claim to be
revisited rather than left to prose.
