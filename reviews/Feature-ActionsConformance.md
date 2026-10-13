# Code review — Feature/ActionsConformance

**Review status:** `complete`
**Reviewed up to commit:** `caaa1430d8d96cb9ddf6cae94911368e41784f05`  `(2026-10-13)`
**Security-reviewed up to commit:** `caaa1430d8d96cb9ddf6cae94911368e41784f05`  `(2026-10-13)`
**Judgment:** `approved`

## Review pass 1 — 2026-10-13 — full, at `4a93d4b`

Independent review lens covering security, correctness, leftovers and tests.

- [x] P1: pr.yml's cancel-in-progress could cancel a preview apply. Fixed with per-environment concurrency
  groups and an `if: cancelled()` lease-break job.
- [x] P1: PR-authored Terraform ran with OIDC credentials and no trust gate. Fixed: a `deploy-preview`
  label opt-in, plus a fork guard on every job that signs in.
- [x] P2: the ephemeral id was truncated to 4 digits, so ids collided across PRs. Fixed: no truncation,
  and an over-budget id fails with `::error::`.
- [x] P2: prod.yml and pr-closed.yml had no concurrency. Fixed.
- [x] P2: the migration command reached HCL unescaped. Fixed: `hcl_list` escapes every item.

## Conformance pass — 2026-10-13

The coordinator found that the goal's Design had missed github-actions `errors` and `domain-design`.
Every rule of the eight github-actions contracts and the azure contracts is now listed in GOAL.md's
Conformance section, and the gaps were fixed:
- timeouts, deploy-dependent `if:` and the `ci`/`preview`/`prod` environments;
- the semver tag plus release-commit assertion, digest-pinned images and serving checks;
- grouped dependabot updates;
- `azure:security` now points at `github-actions:security`.

## Review pass 2 — 2026-10-13 — rule by rule, at `9a98f9e`

- [x] P1: the deploy readiness loops never failed the job. Fixed, with execution tests against stub
  `az`/`curl`.
- [x] P2: probe paths skipped the `${{` ban. Fixed.
- [x] P2: this repository's `terraform.yml` had no concurrency or timeouts. Fixed.
- [x] Annotated-tag dereference in `assert-release-commit`. Fixed, with node execution tests for both tag
  kinds.

## Review pass 3 — 2026-10-13 — focused, at `4739700`

- [x] P2: `probes.readiness` was interpolated into a `run:` body that has Azure credentials, and only a
  leading `/` was validated. Fixed in `1507eea`: it goes through env, and paths must use the URL-path
  character set.
- [x] P2: the Windows stub `PATH` mixed separators. CI later proved this real (kit's Windows verify job
  timed out). Fixed in `43f962a`.

`1507eea`, `43f962a` and `caaa143` (the goal record) were reviewed by the coordinator. CI is green on
`43f962a`: Actions Lint, Scaffold on ubuntu and windows, and kit verify on ubuntu and windows.
