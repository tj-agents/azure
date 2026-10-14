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

## B002 - Workflow commands address shortened resource names

- **Kind:** bug
- **Observed/reported:** 2026-10-13. At main881ac901118a953b6d6ef0d5651f483e02823bf8, scaffold deploy/readiness commands address `<name>-<workload>-func/app/ca` and `<name>-migrate-caj`. The Terraform resources use `local.instance_prefix`, which includes region, environment and any preview ID. Cause confirmed in `scripts/scaffold.py:1390-1508,1713-1717`, component instance resource templates and `foundation/files/infra/instance/locals.tf.in:10-11` under the canonical scaffold skill.
- **Impact and priority:** P2; generated deployment, migration and serving checks can address a different resource from the one Terraform created, across shared and preview environments. No live cloud failure is claimed by this observation.
- **Source owner:** `tj-agents/azure`, scaffold cloud-specific workflow steps and Terraform instance outputs.
- **State:** in-progress
- **Repair link:** `plans/actions-conformance/GOAL.md`; the existing standards-finish owner, item0 Azure workflow composition after the shared writer merges.
- **Next action:** expose actual resource names from instance outputs and use them in composed deployment, migration and readiness steps.
- **Resolution condition:** generated shared/production/preview commands consume the names of their actual Terraform resources; focused output/command and stubbed serving/migration tests pass.
- **Verification evidence:** read-only comparison of authored workflow commands with the instance-prefix/resource templates; the same existing owner is preparing the necessary adapter correction.

## B003 - Rollback deletes an identical pre-existing app profile

- **Kind:** bug
- **Observed/reported:** 2026-10-13. At main881ac901118a953b6d6ef0d5651f483e02823bf8, `cmd_new` permits an external profile identical to an existing `infra/app.json`, but retains that profile in the output map. `write_project` overwrites it and includes it in rollback. A temporary-only injected final metadata-write failure returns1 and deletes the previously existing profile. Cause confirmed in `scripts/scaffold.py:2259-2282,2337-2363` under the canonical scaffold skill.
- **Impact and priority:** P1; a failed scaffold operation can remove a caller-owned input file, violating preservation/rollback guarantees. The reproduction touched only a disposable temporary directory.
- **Source owner:** `tj-agents/azure`, scaffold outer write transaction.
- **State:** resolved
- **Repair link:** `plans/actions-conformance/GOAL.md`; branch `Fix/ScaffoldFileTransaction`, the existing standards-finish owner, item0 transaction prerequisite before Azure workflow composition.
- **Next action:** complete independent review and exact-head CI, then merge the transaction prerequisite before Azure workflow composition.
- **Resolution condition:** external identical profiles and existing files survive injected write failures byte-for-byte; combined output conflicts, redirects and rollback tests pass without cloud execution.
- **Verification evidence:** temporary-directory pure Python reproduction patches only the final metadata write to raise OSError; scaffold exits1 and the existing profile is absent. No repository source or cloud resources were changed by the reproduction.

- **Repair verification:** identical existing external profiles are omitted from outputs and preserve bytes/mtime; exclusive creation and lstat redirect checks precede writes. Rollback compares created file/directory identity and retains replacements or unprovable paths. Twenty focused transaction tests passed (five Windows symlink-privilege skips), including actual junction rejection and injected partial/metadata failures; generated freshness and pinned Kit conformance passed. Full exact-head two-OS CI remains required. Portable path APIs do not guarantee atomic protection from hostile substitution between ownership checks and deletion.

## B004 - Interrupted writer retains ownership references

- **Kind:** bug
- **Observed/reported:** 2026-10-14. At main f05693ec77646cbdef83f35b68d0ac335a61f4e8, a temporary-only injected KeyboardInterrupt during write_project's first output write escapes with a duplicated file descriptor and Windows directory handle still live. The importing caller catches the interruption and continues. Cause confirmed: write_project catches Exception and releases references only in that handler or after success, with no unconditional finally; KeyboardInterrupt bypasses both paths at .agents/azure/utility/scaffold/scripts/scaffold.py:2431-2445.
- **Impact and priority:** P2; an importing process that catches interruption retains file/directory resources, and Windows outputs remain locked against deletion or rename until process exit. No leak beyond a terminated process is claimed.
- **Source owner:** tj-agents/azure, scaffold outer write transaction.
- **State:** resolved
- **Repair link:** BUGS.md#B004, branch Fix/ScaffoldInterruptionRelease and plans/actions-conformance/GOAL.md under the existing standards-finish owner; same observation as GHA review WT-1, separate source implementation. Preserve resolved B003 profile/rollback evidence.
- **Next action:** none for B004; continue item0 Azure provider composition and B002 under the existing standards goal.
- **Resolution condition:** injected interruption during write or stream close propagates unchanged after every acquired reference receives exactly one release attempt; handled failures and success-release diagnostics retain their verified behavior on Linux and Windows.
- **Verification evidence:** parent disposable-file probe at the recorded main head observed two live references after catching KeyboardInterrupt, then explicitly released both and removed the temporary data. No cloud command or application build occurred.

- **B004 repair verification (2026-10-14):** parent inspected129 authored source/test changed lines. Final35 focused tests ran with7 existing platform/privilege skips after definition uniqueness correction; actual closes/rename, original write/final-close interruption identity, continued release attempts, handled caller context and broad Exception wrapped rollback are covered.237 generated freshness/Python3.9 AST/mirrors and4 pure plans versusdefd414 pass. Immutable independent review and exact-head Linux/Windows CI remain before resolution.

- **B004 delivered resolution (2026-10-14):** source PR11 merged3bc656b50aabdca041bc0e73f1a8a2188af32112 from independently approved de912935f6d18b10584b70a49f919709611ddaaf. Fresh correctness/filesystem-security v2 results validate exact frozen identity and7 locators; parent current review/security markers, frozen Kit1.2.1 and both source hosts passed. All6 exact-head checks in Terraform38536132602 and Kit38536133100 succeeded, Linux158 tests/2 Windows-only skips. Every recorded reference receives a release attempt on write/final-close interruption without masking the original escaping error; ordinary wrapped rollback and success-release diagnostics remain covered. Actual OS close failure cannot guarantee closure, and portable deletion remains non-atomic. Actual MERGED state and terminal monitor verified; binding released, removable proof passed, primary prior notes proven present in published record before safe fast-forward and owned checkout/branch removed.
