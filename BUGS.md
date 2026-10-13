# Bugs

## B001 ? Sticky validation crashes on malformed settings

- **Kind:** bug
- **Observed/reported:** 2026-10-13. At frozen head `6beed087597e5575659dfa5bfcd035b4386c82e9`, an App Service workload carrying `sticky: []` with profile `settings: 7` raises `TypeError: int object is not iterable`; `settings: [["KEY"]]` raises `TypeError: unhashable type: list`. Removing `sticky` returns `profile.settings: must be an object`. Confirmed cause: `validate_sticky` builds sets from unvalidated settings values at `.agents/azure/utility/scaffold/scripts/scaffold.py:410`.
- **Impact and priority:** P2; malformed input violates the scaffold's validation/exit-2 contract and produces an uncaught traceback. Existing valid inputs are unaffected.
- **Source owner:** `tj-agents/azure`, scaffold profile validation.
- **State:** resolved
- **Repair link:** branch `Feature/StickySlotSettings`, `plans/actions-conformance/GOAL.md`, and `reviews/Feature-StickySlotSettings.md` in its active worktree.
- **Next action:** complete the independent branch review and delivery; the causal repair is verified.
- **Resolution condition:** malformed scalar/nested-list settings with `sticky` return validation errors without exceptions or writes, and the sticky validation/rendering suite remains green.
- **Verification evidence:** reproduced against the immutable exported review tree at the recorded head; the same malformed profiles without `sticky` returned the expected error.
- **Repair verification:** dictionary checks guard effective-key resolution; all nine sticky tests pass, including four malformed profile/workload cases returning exit 2 without writes. Generated outputs are current. Full branch validation and delivery remain owned by the active goal.

## B002 — Workflow commands address shortened resource names

- **Kind:** bug
- **Observed/reported:** 2026-10-13. At main881ac901118a953b6d6ef0d5651f483e02823bf8, scaffold deploy/readiness commands address `<name>-<workload>-func/app/ca` and `<name>-migrate-caj`. The Terraform resources use `local.instance_prefix`, which includes region, environment and any preview ID. Cause confirmed in `scripts/scaffold.py:1390–1508,1713–1717`, component instance resource templates and `foundation/files/infra/instance/locals.tf.in:10–11` under the canonical scaffold skill.
- **Impact and priority:** P2; generated deployment, migration and serving checks can address a different resource from the one Terraform created, across shared and preview environments. No live cloud failure is claimed by this observation.
- **Source owner:** `tj-agents/azure`, scaffold cloud-specific workflow steps and Terraform instance outputs.
- **State:** in-progress
- **Repair link:** `plans/actions-conformance/GOAL.md`; the existing standards-finish owner, item0 Azure workflow composition after the shared writer merges.
- **Next action:** expose actual resource names from instance outputs and use them in composed deployment, migration and readiness steps.
- **Resolution condition:** generated shared/production/preview commands consume the names of their actual Terraform resources; focused output/command and stubbed serving/migration tests pass.
- **Verification evidence:** read-only comparison of authored workflow commands with the instance-prefix/resource templates; the same existing owner is preparing the necessary adapter correction.

## B003 — Rollback deletes an identical pre-existing app profile

- **Kind:** bug
- **Observed/reported:** 2026-10-13. At main881ac901118a953b6d6ef0d5651f483e02823bf8, `cmd_new` permits an external profile identical to an existing `infra/app.json`, but retains that profile in the output map. `write_project` overwrites it and includes it in rollback. A temporary-only injected final metadata-write failure returns1 and deletes the previously existing profile. Cause confirmed in `scripts/scaffold.py:2259–2282,2337–2363` under the canonical scaffold skill.
- **Impact and priority:** P1; a failed scaffold operation can remove a caller-owned input file, violating preservation/rollback guarantees. The reproduction touched only a disposable temporary directory.
- **Source owner:** `tj-agents/azure`, scaffold outer write transaction.
- **State:** in-progress
- **Repair link:** `plans/actions-conformance/GOAL.md`; the existing standards-finish owner, item0 Azure workflow composition after the shared writer merges.
- **Next action:** omit identical existing profiles from the combined write set and use exclusive creation with safe conflict/redirect checks and rollback limited to new outputs.
- **Resolution condition:** external identical profiles and existing files survive injected write failures byte-for-byte; combined output conflicts, redirects and rollback tests pass without cloud execution.
- **Verification evidence:** temporary-directory pure Python reproduction patches only the final metadata write to raise OSError; scaffold exits1 and the existing profile is absent. No repository source or cloud resources were changed by the reproduction.
