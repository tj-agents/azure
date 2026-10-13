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
