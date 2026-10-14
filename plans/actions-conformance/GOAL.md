# azure:scaffold's generated workflows follow the github-actions contracts

Status: in progress (2026-10-13). Requested by Tommy, relayed by sandbox-dotnet-17, whose regenerated
`infra.yml` is the first consumer.

Cause: the azure package goal said "`github-actions` owns the YAML conventions those files follow. Read its
Decisions before writing workflow templates". The app-profile goal (Phase A) never did, so `infra.yml` is
hand-rolled. tj-agents/github-actions `706a817` is now merged with agreed structure, style, security,
testing and build contracts. Its scaffold writes nothing: "Workflows come from the scaffold that owns the
deploy target, such as `azure:scaffold`, and follow this package's contracts."

## Outcome

Every workflow file `azure:scaffold` generates obeys the github-actions contracts at `706a817` or later.
`actionlint` and `zizmor` pass on every generated preset in this repository's CI.

## Design

The rule each item follows is in brackets.
- **Files** [`github-actions:structure`]: a deployed service gets
  - `.github/workflows/pr.yml`: verify, then plan, then an optional preview instance with
    `ephemeral_id = pr<N>`;
  - `ci.yml`: push to `main`, verify, then deploy the shared non-production environment `ci`;
  - `prod.yml`: tag-triggered production deploy through a protected `prod` environment;
  - `pr-closed.yml`: preview teardown, a `terraform destroy` of the `pr<N>` instance;
  - reusable workflows `deploy-environment.yml` and `destroy-preview.yml` (`on: workflow_call`), and
    composite actions under `.github/actions/<kebab-name>/action.yml` for steps repeated across jobs
    (Azure sign-in plus Terraform init, the image build loop);
  - `.github/dependabot.yml` for the `github-actions` ecosystem;
  - `infra/env/prod.tfvars` alongside `ci.tfvars`.
- **App verification**: the profile gains an optional top-level `verify` (a list of shell commands, for
  example the app stack's restore, build and test). pr.yml and ci.yml run them in a sentence-named job
  before any Azure job. With azure owning `ci.yml`, an app stack with `cloud=azure` doesn't emit its own
  (the dotnet change follows in that repository).
- **Security** [`github-actions:security`]:
  - top-level `permissions: contents: read`, with jobs re-declaring their full list;
  - `id-token: write` only on jobs that sign in;
  - every remote `uses:` pinned to a full commit SHA with `# vX.Y.Z`, resolved at implementation time from
    each action's release tag (actions/checkout, actions/upload-artifact if used, hashicorp/setup-terraform,
    azure/login);
  - `persist-credentials: false` on every checkout;
  - no `${{ }}` of untrusted values in `run:`; secrets only in step `env:`; IDs as `vars`.
- **Style** [`github-actions:style`]: Title Case `name:` (`PR`, `CI`, `Release (Production)`, `PR Closed`),
  kebab-case job ids, a sentence-case job `name:`, named steps, and `set -euo pipefail` at the top of every
  multi-line `run:`. Comments only for a why.
- **Testing** [`github-actions:testing`]:
  - a `ci-complete` aggregator job in pr.yml and ci.yml (`if: always()`, `needs:` every gate);
  - artifacts set `if-no-files-found` and `retention-days`;
  - this repository's own CI runs `actionlint` and `zizmor` over every generated preset's `.github/`.
- **Build** [`github-actions:build`]: `ubuntu-latest`; Terraform's version is pinned exactly (it already is).

- **Ignore rules** (sandbox-dotnet evidence: `git add -A` after `terraform init` staged provider binaries):
  generate `infra/.gitignore` with `.terraform/`, `*.tfstate*`, plan files, crash logs and override files.
  `.terraform.lock.hcl` stays tracked, per `azure:build`.

- **Database schemas** (sandbox-dotnet evidence: an EF model in schema `sandbox` gave permission denied for
  every workload): add an optional profile field `database.schemas`, defaulting to `["public"]`.
  - The init script creates each schema (`IF NOT EXISTS`, owned by the migrator), because it runs before
    the migration.
  - It grants each workload usage and table/sequence rights plus default privileges on every declared
    schema.

- **Service Bus filters** (sandbox-dotnet evidence: the empty global `service_bus_handled_types` rendered
  an invalid `sql_filter`):
  - `messaging.consumes` entries become `{topic, types}`, with types required, non-empty and safe-charset;
  - each subscription renders `[type] IN (...)`, and the global variable is removed;
  - the deploy workflow deletes `$Default` per subscription after apply, so the filter takes effect.

- **Concurrency and cancellation** [`github-actions:errors`]: `pr.yml` keeps the verification group
  `${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}` (`cancel-in-progress:
  true`); `ci.yml`, `prod.yml` and the preview apply/`pr-closed.yml` pair use fixed groups per environment
  (`ci`, `prod`, `preview-<instance id>`) with `cancel-in-progress: false`; a `break-preview-lock` job,
  `if: cancelled()`, breaks the preview's leaked blob lease over OIDC; every job sets `timeout-minutes`
  (except a job that only `uses:` a reusable workflow, which GitHub does not allow it on — the timeout lives
  in that workflow's own job instead); a job that depends on a deploy uses
  `if: ${{ !cancelled() && needs.<job>.result == 'success' }}`; failures annotate with `::error file=…::`.
- **Environments and preview opt-in** [`github-actions:domain-design`]: one GitHub environment per deploy
  target — `ci`, `preview`, `prod` — each with its own vars, secrets and protection rules (`preview` and
  `prod` should require review); the old `dev` name is retired everywhere it named that target, including
  `env/dev.tfvars` → `env/ci.tfvars`. A pull request's preview is opt-in through a `deploy-preview` label
  (triage access is the trust gate a fork can't bypass), triggering `pr.yml` on `opened`, `synchronize`,
  `reopened` and `labeled`; `pr-closed.yml` destroys the preview only if it carried the label. `plan`, the
  preview apply, the lock release and the destroy additionally check
  `github.event.pull_request.head.repo.full_name == github.repository` as defense in depth. The ephemeral id
  stays the full `pr<N>` — truncating it let two PRs 10000 apart collide — so an id over the 6-character
  budget fails the job loudly instead.
- **Release rule** [`github-actions:domain-design`, `github-actions:libraries`]: this rule is unconditional,
  not optional. `prod.yml` triggers only on `v[0-9]+.[0-9]+.[0-9]+` (not `v*`). Its first job,
  `assert-release-commit`, uses `actions/github-script` (per `github-actions:libraries`: GitHub API calls go
  through `github-script`, not a raw `gh`/curl call) to compare the tag's commit against the `headSha` of
  the latest successful `ci.yml` run on `main`, failing with `core.setFailed` (`::error::`) on a mismatch;
  every later job needs it. `prod` is documented as allowing deploys from `v*` tags only (the actual trigger
  is the stricter semver pattern; the environment protection rule is coarser because GitHub environment
  deployment-branch-and-tag rules don't support the full glob). Every deploy also references an immutable
  artifact: `build-workload` resolves the pushed image's digest via `docker inspect`'s `RepoDigests` and
  deploys that, never the mutable `$TAG`; a deploy job waits for the new code to be serving (Container Apps
  `Healthy`, Flex Consumption `Running`, App Service's production slot re-checked after the swap), not
  merely for the deploy command to return. The region matrix (`environment: <region>-prod`,
  `fail-fast: false`) is conditional on several regions; azure:scaffold deploys one region per profile, so
  it's N/A here.

## Conformance

Full pass against every rule in `github-actions`'s contracts (`build`, `domain-design`, `errors`,
`libraries`, `security`, `structure`, `style`, `testing`) and `azure`'s own (`build`, `deployment`,
`domain-design`, `errors`, `libraries`, `security`, `structure`, `style`, `testing`), read in full. One line
per rule: where it's satisfied (file:job), or why it's N/A.

`github-actions:structure`:
- A deployed service gets pr.yml/ci.yml/prod.yml/pr-closed.yml (library/tool N/A, azure only scaffolds
  services): all four generated by `scaffold.py`'s `plan()`.
- A repeated step sequence is a composite action, a job reused by several callers is a reusable workflow:
  `.github/actions/{terraform-login-init,build-workload}`; `deploy-environment.yml`/`destroy-preview.yml`.
- Shared cross-repository workflows live in one pipeline repo: N/A, azure's reusable workflows are same-repo.
- File structure tree: matches `azure:structure`'s File structure section and the `EMPTY_WORKLOADS_FILES`/
  `SANDBOX_FILES` test sets, both updated this slice.

`github-actions:style`:
- Title Case workflow name, kebab job id, sentence-case job name, named steps, `set -euo pipefail` first in
  multi-line `run:`: verified by a scripted sweep over every generated preset — no unnamed step, no
  multi-line `run:` missing the line.
- kebab inputs/outputs, SCREAMING_SNAKE secrets/env: every composite/reusable-workflow input is kebab; every
  env var SCREAMING_SNAKE.
- Comments only for a why: the only prose comment is the two-line ephemeral-id note in pr.yml/pr-closed.yml;
  `# vX.Y.Z` pin comments are `github-actions:security`'s own separate, mandated convention.

`github-actions:security`:
- Top-level `permissions: contents: read`, jobs re-declare in full, aggregator `permissions: {}`: all 6
  generated workflow files; `ci-complete` sets `{}`.
- `id-token: write` only on a job that signs in: `plan`, `break-preview-lock`, and the reusable workflows'
  own `deploy`/`destroy` jobs sign in; the `uses:`-calling jobs (`deploy-ci`, `deploy-prod`, `deploy-preview`,
  pr-closed.yml's `destroy-preview`) re-grant it only because GitHub cannot pass a called workflow more
  permission than its caller holds — no job outside that chain carries it.
- Every remote `uses:` pinned to a full SHA with `# vX.Y.Z`, local `./` exempt: `actions/checkout`,
  `hashicorp/setup-terraform`, `azure/login`, `actions/upload-artifact`, `actions/github-script`; verified by
  `test_every_remote_uses_is_pinned_to_a_full_sha_with_a_version_comment`.
- `persist-credentials: false` on every checkout: verified by test.
- No `${{ }}` of untrusted values in `run:`/`script:`, secrets only in step `env:`, IDs as `vars`: swept
  every generated preset's `run:` bodies for `github.event`/`github.head_ref`/`inputs.`/`needs.` — none
  found; `assert-release-commit`'s `github-script` reads `process.env.TAG_SHA`, never inlines it.
- No `pull_request_target` checking out PR code: N/A, unused.
- Secrets in step `env:`, not workflow-level; IDs as `vars`: no custom `secrets.*` anywhere (the one GitHub
  API call uses `github-script`'s own ambient token); every identifier is `vars.*`.
- Cloud sign-in is OIDC (`azure/login`, `vars.*`), `id-token: write` only on the signing-in job: satisfied;
  the `ci` foundation's deploy identity also federates the `environment:preview` subject (`main.tf.in`), or
  `preview` could never sign in.

`github-actions:testing`:
- actionlint + zizmor in CI: `.github/workflows/terraform.yml`'s `actions-lint` job.
- `ci-complete` aggregator, `if: always()`, needs every gate, fails unless all succeeded: pr.yml/ci.yml;
  verified by test.
- Test jobs split by layer, unit/integration on the PR, e2e against a deployed environment: N/A/delegated —
  azure's `verify` job runs whatever layered commands the app stack's `profile.verify` supplies (each its
  own named step); splitting those into separate jobs by layer is the app stack's call, not azure's.
- Uploaded artifacts set `if-no-files-found` and `retention-days`: the one upload (`plan`'s Terraform plan)
  sets both.

`github-actions:build`:
- `ubuntu-latest` by default, `windows-latest` only for cross-platform: every job is `ubuntu-latest`.
- Toolchain versions from repo files, never duplicated in YAML, except Terraform pinned exactly: N/A/
  satisfied — Terraform is azure's only toolchain, explicitly exempted by this same contract.
- Caching via setup actions' built-in `cache:`: N/A, azure installs no language toolchain of its own.

`github-actions:errors`:
- Verification/fixed/shared concurrency groups: satisfied, see Design above; verified by tests.
- `if: cancelled()` lock-release job: `break-preview-lock`.
- Every job sets `timeout-minutes`: satisfied except a job that only `uses:` a reusable workflow, where
  GitHub's schema forbids the key (confirmed with actionlint); the timeout lives in the called workflow's
  own job instead.
- `if: ${{ !cancelled() && needs.<job>.result == 'success' }}` on a job depending on a deploy: `deploy-ci`,
  `deploy-prod`, `deploy-preview`, `destroy-preview`.
- `::error file=…::` annotations: the ephemeral-id budget check, `ci-complete`, the migration job's two
  failure paths, the Service Bus `$Default`-removal step, and `assert-release-commit` (`core.setFailed`).

`github-actions:domain-design`:
- Immutable artifact, digest or versioned package, never a mutable tag: satisfied — image builds resolve and
  deploy the pushed digest; package builds have no separate mutable reference.
- Production released by semver tag, first job asserts the latest green `ci.yml`: `assert-release-commit`.
- One GitHub environment per deploy target with its own vars/secrets/protection, production allows `v*`-tag
  deploys only: `ci`/`preview`/`prod`, documented in README.md.in/SKILL.md.
- Region matrix for several regions: N/A, azure deploys one region per profile — see the Design bullet above.
- Preview opt-in via a `deploy-preview` label, named `pr-<number>`, own state key, `pr-closed.yml` destroys
  regardless of merge: satisfied in spirit; naming differs (`ephemeral_id` is `pr<N>`, no hyphen) because
  `azure:structure`'s pre-existing 6-character, alphanumeric-only convention predates this slice and a
  hyphen both shortens the budget and isn't in the Terraform variable's validated charset; `pr-closed.yml`
  runs on `types: [closed]` regardless of merged/closed.
- A deploy succeeds only once serving, not merely ready: Container Apps wait for `Healthy`, Flex Consumption
  for `Running`, App Service rechecked against production after the slot swap — and, found false on the
  second review (every wait exited 0 on timeout, so a deploy could "succeed" without ever becoming ready),
  now genuinely true: each loop fails the job with `::error file=.github/workflows/deploy-environment.yml::
  <workload> did not become <state> within 5 minutes` on a timeout, and the App Service swap runs only
  after staging returned 200. Executed with stub `az`/`curl` in both the ready and never-ready cases.

`github-actions:libraries`:
- Prefer first-party/vendor actions, a community action needs a reason, a short script beats a
  single-purpose marketplace action: satisfied — `actions/checkout`, `hashicorp/setup-terraform`,
  `azure/login`, `actions/upload-artifact`, `actions/github-script`; no community action.
- Use `actions/github-script` for GitHub API calls: **fixed this pass** — `assert-release-commit` (the only
  GitHub API call azure generates) was drafted around a raw `gh run list` call; now uses `github-script`.
- `.github/dependabot.yml` updates `github-actions` weekly in one grouped PR: **fixed this pass** — the prior
  `dependabot.yml` had no `groups:`, so each action bump would have opened its own PR; added
  `groups: github-actions: patterns: ["*"]`.

`azure:structure`: two Terraform roots, `instance` reads `foundation` via `data` sources, flat per-root
files, `ephemeral_id` suffixes names/keys, Service Bus topics-in-foundation/subscriptions-in-instance, File
structure tree — pre-existing and out of this slice's touch, except the additions (ci.tfvars, .gitignore,
the new `.github` tree, the `deploy_preview` federated credential) already reflected, none changing the
two-root model.

`azure:security`: one identity per workload plus migrator, data-plane roles only, no stored secrets,
`time_sleep` after identity/role, OIDC federated credential never a client secret, immutable subject format,
**workflow top-level permissions**, `private_networking` default true — all pre-existing and unaffected
except the bolded clause, which was stale (said `permissions: {}` at the top, contradicting
`github-actions:security`'s `contents: read`, and predating this goal); **fixed this pass** in
`azure/contract/security/SKILL.md` to match what the generated workflows actually do. The new
`environment:preview` federated credential follows the same immutable-subject rule.

`azure:style`: resource naming, region-code map, `local.tags`, `terraform fmt`, every variable
typed/described/validated — pre-existing, unaffected (no new variables; the new federated-credential
resource reuses existing locals/vars).

`azure:build`: Terraform/provider pins, `.terraform.lock.hcl` committed, provider block, fmt→init→validate
gate, a new workload starts from `azure:scaffold` — pre-existing, unaffected, except `.terraform.lock.hcl`
staying tracked is now also explicit in `infra/.gitignore` (an earlier slice-1 fix).

`azure:deployment`: environment/region as variables, remote state key `<name>/<root>[.<ephemeral_id>]
.tfstate`, shared-key auth disabled, foundation bootstraps with local state then migrates, CI flow (PR
plans, main applies through a protected environment, `cancel-in-progress: false`), observability — state
keys unchanged (environments select the backend target via different `vars`, not a different key pattern);
`cancel-in-progress: false` on ci/prod/preview; "main applies through a protected environment" is extended
this pass by prod's additional release-commit assertion.

`azure:testing`, `azure:errors`, `azure:libraries`, `azure:domain-design`: "_Nothing yet._" in each — N/A,
no rules recorded to conform to.

### Slice 3 re-check (sticky settings)

Re-read `azure:security`, `azure:style` and `azure:structure` in full against this slice before finishing
it. This slice touches no workflow, so the `github-actions` contracts don't apply here.

- `azure:security`: no new identity, role assignment, secret, `time_sleep` or network setting — `sticky`
  only adds a nested `sticky_settings` block to the existing `azurerm_linux_web_app.main` resource, naming
  settings that already exist. N/A/unaffected.
- `azure:style`: no new resource (so no new name to build, no new `local.tags` to attach) and no new
  Terraform `variable` (so no `type`/`description`/`validation` to add) — `sticky` is a `locals.workloads[id]`
  field, sourced from the profile, like `publishes`/`consumes` already are. N/A/unaffected. The one new
  comment-free HCL addition (`dynamic "sticky_settings"`) adds no comments.
- `azure:structure`: no new file, no new root, no change to how `instance` reads `foundation` — `sticky`
  renders inside the existing `app-service.tf`, which `azure:structure`'s File structure section already
  lists. N/A/unaffected.

## Slices

1. azure (this branch, PR to tj-agents/azure): everything above, with tests and actionlint/zizmor in CI.
3. azure follow-up (after slice 1 merges), from sandbox-dotnet evidence: App Service slot-sticky settings.
   The staging slot exists, but no setting is sticky, so a swap carries every setting across. Add an
   optional per-workload profile field `sticky`: a list of setting names, each of which must be a key of
   that workload's effective settings, or exit 2. Render it into the web app's
   `sticky_settings { app_setting_names = [...] }`, with tests.
2. dotnet (after its slice A merges): with `cloud=azure`, write the profile's `verify` commands and stop
   emitting `.github/workflows/ci.yml`. Added to tj-agents/dotnet
   `plans/single-standard-conventions/GOAL.md`.

## Lanes

- L4 implementation by one in-session lane agent, the only writer in this worktree.
- An independent review lens.

## Progress

- 2026-10-13: assessment done (gaps listed above). Goal written on `Feature/ActionsConformance` from main
  `682acf7`.

- 2026-10-13: slice 1 implemented (`73c7efc`, `52894b9`): pr/ci/prod/pr-closed workflows with pinned SHAs,
  `verify`, `database.schemas`, per-type `messaging.consumes`, `infra/.gitignore`, and an `actions-lint` CI
  job. 83 unittests and the 5-case Terraform gate pass. actionlint 1.7.12 and zizmor 1.30.1 are clean on
  every preset; zizmor's `self-repository` audit is ignored for generated files only, because actionlint
  rejects the `$/` syntax it asks for. This repository's own `terraform.yml` is pinned and linted too.

- 2026-10-13: PR #5 (`tj-agents/azure#5`) review fixes: pinned/linted `terraform.yml` (`4a93d4b`) and made
  the retry loops shellcheck-clean plus an App Service lint preset (`2c883a5`), both already pushed. Added
  the `errors` and `domain-design` contracts to the Design above and closed every item the review raised:
  verification/fixed/shared concurrency groups, a `break-preview-lock` job, `timeout-minutes` on every job
  that can carry it, `!cancelled() && needs.<job>.result == 'success'` on deploy-dependent jobs,
  `::error file=…::` annotations, the `dev` → `ci`/`preview`/`prod` environment rename (including
  `env/dev.tfvars` → `env/ci.tfvars`), the `deploy-preview` label gate replacing `AZURE_PREVIEW_ENABLED`
  with fork-safety checks on every signing-in PR job, an untruncated `pr<N>` ephemeral id that fails loudly
  over budget, `hcl_list` escaping every item through `escape_hcl_string`, and dropping the unnecessary
  checkout from the ephemeral-id job. Since `preview` is now its own GitHub environment distinct from `ci`,
  the `ci` foundation's deploy identity also gets a federated credential for the `environment:preview` OIDC
  subject (only on the `ci` foundation, `count = var.env == "ci" ? 1 : 0`), or a preview deploy could never
  sign in. 98 unittests and the 5-case Terraform gate pass; actionlint (with shellcheck) and zizmor are
  clean on all four lint presets and on `terraform.yml`.

- 2026-10-13: the release rule and a full conformance pass. `prod.yml` now triggers only on
  `v[0-9]+.[0-9]+.[0-9]+`, gated by a new `assert-release-commit` job (`actions/github-script`, per
  `github-actions:libraries`) that fails every later job unless the tag's commit is the latest green
  `ci.yml` run on `main`; the region matrix is N/A for one region. Read every `github-actions` and `azure`
  contract in full and recorded a Conformance section above; fixed what didn't conform: image builds now
  deploy the pushed digest (`docker inspect`'s `RepoDigests`), never the mutable `$TAG`; App Service
  rechecks readiness against production after the slot swap and Flex Consumption waits for `Running`;
  `dependabot.yml` groups every `github-actions` bump into one PR; and `azure:security`'s stale
  `permissions: {}`-at-the-top line (predating this goal, contradicting `github-actions:security`) is
  corrected to match what every generated workflow actually does. 103 unittests and the 5-case Terraform
  gate pass; actionlint (with shellcheck) and zizmor are clean on all four lint presets and on
  `terraform.yml`.

- 2026-10-13: PR #5's second review (at `9a98f9e`, which pointed `azure:security` at
  `github-actions:security`) found four more defects. Fixed all four:
  1. Every deploy readiness loop in `render_deploy_steps` (Flex Consumption `Running`, App Service
     pre-swap and post-swap readiness, Container Apps `Healthy`) exited 0 on a timeout regardless of
     whether the workload ever became ready, and the App Service swap ran even if staging never returned
     200. Each loop now tracks whether it broke out ready and, if not, fails with
     `::error file=.github/workflows/deploy-environment.yml::<workload> did not become <state> within 5
     minutes`; the swap only runs after a successful pre-swap check. Tested by extracting each loop and
     executing it with stub `az`/`curl` on `PATH` (sleep stubbed to return instantly), for both a
     never-ready and a ready stub, including the swap-gating behaviour. Corrected the "serving, not merely
     ready" claim in this file, `SKILL.md` and `README.md.in` to describe the now-true behaviour.
  2. `validate_profile` never ran `_has_actions_expression` on `probes.liveness`/`probes.readiness`, even
     though `readiness` is spliced directly into the generated `run:` body (not routed through a step
     `env:`, unlike `build.*`). Added the check and a test.
  3. This repository's own `terraform.yml` had no `concurrency:` or `timeout-minutes`. Added the
     verification group (`cancel-in-progress: true`) and `timeout-minutes` on both jobs; re-linted clean.
  4. `assert-release-commit` compared `github.sha`, which GitHub already dereferences for a tag push, but
     assumed rather than confirmed that. The script now resolves the pushed ref itself
     (`git.getRef` on `tags/<name>`) and, only if `object.type === "tag"` (an annotated tag), dereferences
     it with `git.getTag` before comparing. Tested by extracting the `script:` block and running it under
     real `node` with a mocked `github`/`context`/`core`, covering a lightweight tag, an annotated tag
     (confirming the dereferenced commit is compared, not the tag object's own sha), both tags mismatching,
     and no successful `ci.yml` run.

  113 unittests (5 Terraform-gate skipped outside `AZURE_SCAFFOLD_TERRAFORM`) and the 5-case Terraform gate
  pass, run one Terraform process at a time; actionlint (with shellcheck) and zizmor are clean on all four
  lint presets and on `terraform.yml`.

- 2026-10-13: the third review (at `4739700`) found two problems.
  - `probes.readiness` was still spliced into the App Service `run:` body, guarded only by a leading `/`,
    so `/a$(cmd)` would run a command. It now goes through a step `env:` (`READINESS_PATH`), and probe
    paths must match a URL-path character set (`1507eea`).
  - The stub harness prepended a POSIX stub directory onto the Windows `PATH`. Under kit's Windows verify
    job, bash never saw it, the real `sleep` ran, and the loops timed out. Stubs now go on `PATH` inside
    the script, with `/usr/bin` and `/bin` (`43f962a`).
  - 114 unittests pass locally under both bash and PowerShell, and CI is green on every job at `43f962a`.

- 2026-10-13: slice 3 (sticky slot settings), on `Feature/StickySlotSettings` from `main` `f3a44b0`. Added
  an optional per-workload profile field `sticky`: a list of setting names. Valid only when the workload's
  host is App Service (`validate_sticky`; any other host exits 2); each name must be a key of that
  workload's effective settings (profile-level merged with its own), or exit 2 naming the bad key. An
  absent or empty list is valid and renders nothing. `render_workloads_tf` carries the list into
  `locals.workloads[id].sticky` through `hcl_list` (so each name is escaped through `escape_hcl_string`,
  same as every other HCL list); `app-service.tf.in` renders it as a `dynamic "sticky_settings"` block on
  the main `azurerm_linux_web_app` resource only (not the staging slot), `for_each` only emitting the block
  when the list is non-empty. Added the Slice 3 re-check above (`azure:security`, `azure:style`,
  `azure:structure`; no workflow touched, so the `github-actions` contracts don't apply). Updated SKILL.md
  (the workload-field list, the `locals.workloads` shape) and README.md.in; no `options.json` or structure-
  contract change needed (`sticky` is a profile field, not a CLI option, and `azure:structure` describes file
  layout, not the profile schema). 8 new tests cover validation (wrong host on both `background` and `http`
  kinds, an unknown name, a non-list value, the empty and absent cases) and rendering (profile- and
  workload-level settings both counting, the block landing on the main resource and not the slot); the
  existing App Service Terraform-gate case gained a `sticky` entry. 122 unittests (5 Terraform-gate skipped
  outside `AZURE_SCAFFOLD_TERRAFORM`) and the 5-case Terraform gate pass, run one Terraform process at a
  time; actionlint (with shellcheck) and zizmor are clean on all four lint presets.

## Next Steps

1. Repair B004 unconditional ownership-reference release on interruption; independent review, exact-head CI and merge.
2. Compose the github-actions pure renderer; Azure owns its provider steps and B002 uses actual Terraform resource outputs.
3. Dotnet one-shape0b, then identity/dependencies/typed dispatcher/Terraform/Core in the canonical handoff order.

## Codex pickup - 2026-10-13

The successor owns this branch and the canonical handoff at `C:\Users\TommySeery\.claude\plans\tj-agents\HANDOFF_STANDARDS_REMAINING.md`. Tommy authorized available models for implementation and independent review on 2026-10-13, superseding the earlier model-capacity gate. Phase lane: L4, a specified local repair with regression coverage, executed by the current owner under that override.

B001 is repaired by guarding effective-settings key resolution with dictionary checks. Nine sticky tests pass, including a regression with four malformed top-level/workload cases that verifies exit 2 without writes. Generated outputs are current. Full Azure/Terraform validation and independent review are next; no subscription plan/apply/az command is authorized.

## Delivery checkpoint - 2026-10-13

Sticky settings and B001 merged in PR 7 at 881ac901118a953b6d6ef0d5651f483e02823bf8. Reviewed head ccf7bf13b403d923b59f3004479738f4d7604761 has independent general and contract approval, including the test cleanup delta. Final-head CI passed the complete scaffold/Terraform suite on both operating systems, Actions lint and host verification. Nine focused tests, generated freshness and pinned kit conformance passed locally; local Terraform attempts failed on workstation disk exhaustion. Primary main is current and clean; the merged branch and worktree are removed. No publish workflow exists.

Current goal order follows the canonical standards handoff: finish and merge dotnet snippet gate, then github-actions whole-workflow ownership, then the single Web API boilerplate scaffold. No additional native CLI sessions.


## Item 0 delivery map - 2026-10-14

The dotnet snippet gate merged as PR52 at08f9c41275105e6eac01009301cbbb9edfc8acdf.
The shared writer merged as github-actions PR3 ata2e04b07cee412b27f8c626120973ea318f6c69f;
independent review approved230fee5 and exact-head runs38481844145/38481844550 passed.
Next Azure composes that pure file-map renderer and owns only cloud steps.

Sequential delivery boundaries:
1. Fix/ScaffoldFileTransaction, base0645dd5a1ce3c0df98bcde01f81b7cac83ebc04b, PR: not opened.
   Repair B003 before the combined writer increases the transaction's output set. Include exclusive
   output creation, preservation of an existing identical external app profile, redirect/conflict
   rejection and rollback of only files created by this invocation. Expected under400 substantive
   source/test lines; independent correctness/security review and Linux/Windows scaffold CI.
   Retains current workflow generation; no writer API, action catalog or cloud behavior changes.
2. Compose the merged github-actions writer from the updated Azure base. Replace generic workflow
   envelopes, pins, aggregation, Dependabot and emitted lint with the external writer's pure render API.
   Azure provides structured cloud phases and literal OIDC environments; explicit writer source input
   precedes output creation. Include build-once archive/manifest promotion and B002 resource names from
   Terraform outputs. This atomic provider/caller cutover is expected to remove about1000 generic lines
   and add cloud phase/tests; reassess its measured size before review. A detached manifest-only API
   would be unused until the same caller cutover; no dead preparatory API is planned.
3. The single Web API boilerplate scaffold follows in dotnet; identity, published dependency mechanism,
   transactional dispatcher, Terraform ownership and Core repairs remain later separate deliveries.

Current execution phase: L4 specified transaction repair with temporary-filesystem regression tests;
one bounded available-model writer owns source/tests. Parent owns goal, Git, acceptance, deny-list,
independent review and delivery. No local Terraform/C# build or live Azure commands.
Execution checkout: C:\Users\TommySeery\source\repos\tj-agents\azure\.worktrees\Fix-ScaffoldFileTransaction.
Next action: repair and verify B003, then commit a frozen candidate for independent review.
### B003 candidate acceptance - 2026-10-14

The exclusive L4 source writer stopped all handles. Parent inspected file/profile preflight, exclusive
creation and rollback ownership. Existing identical external profiles are omitted from outputs; bytes
and mtime survive success, partial/final metadata failure and dry run. Conflicting or redirected outputs
fail before writes, including an actual Windows junction. Rollback compares recorded device/inode
identity and preserves replaced/unprovable files and directories; retained paths are reported. Portable
path operations do not promise atomic protection from hostile substitutions between identity check
and removal.

Focused transaction suite:20 tests,5 Windows symlink-privilege skips, success; replacement file,
replacement directory, unprovable identity and Windows junction passed. Shared artifact:
.git/agent-workflow/runs/standards-azure-transaction-20261014/artifacts/1da2ab2770cd7a01-transaction-owned-rollback.log.
Earlier complete lightweight suite:141 tests,15 skips (Terraform disabled), success; final ownership
correction is covered by focused tests and will receive the complete exact-head Linux/Windows CI.
237-file generated freshness, whitespace and pinned Kit1.2.1 conformance pass. Canonical source is
+111/-40, tests+409/-7; generated mirrors are mechanical, goal/record changes retain this delivery map.
This is within the recorded bounded transaction slice; no cloud/workflow-provider cutover is included.

Next phase: L4 independent native correctness and L2 filesystem security review over one committed
frozen candidate; parent alone judges findings. Then exact-head remote scaffold/Terraform, Actions lint
and host gates, authorized merge/cleanup, and the Azure provider composition/B002 slice.

2026-10-14 B003 independent review completed at c1f4aa9 with changes-requested: FT-1 second-preflight OSError translation and FT-2 live ownership references to prevent released-inode reuse before rollback. Two exact-identity v2 results validated; frozen source/coverage checked by parent; canonical review/security watermarks stamped. Bounded read-only identity-lifetime design precedes the sole source repair lease. PR9 remains draft; exact-head remote CI passed initial checks but review gates remain. No cloud or application build.


2026-10-14 parent accepts FT1/2 live-ownership repair after source and complete test-delta inspection. Focused32 tests pass,7 Windows privilege/POSIX-platform skips; generator237 outputs/freshness and diff checks pass. Authored full-base change is source241 plus tests792 lines =1033; generated mirrors excluded. Reassessment retains one atomic B003 transaction slice: exclusive creation, live identity acquisition, stream-close failure handling and owned rollback must land together; splitting the lifetime correction would publish the reviewed unsafe cleanup. No provider/composition/B002 changes accumulated. Fresh incremental review and exact-head CI precede merge. Separately, source-owner GHA B001 record-only PR4 merged d93b6ce from e814659 after ordinary docs review and all5 exact-head checks; primary record retained/verified, docs checkout removed. Its public-new rollback repair remains bounded item0 follow-up, independent of Azure's pure renderer consumption.

### B004 interruption prerequisite - 2026-10-14

B003 PR9 mergedf05693ec from approved9e90c28, all6 exact-head checks passed and its checkout/binding cleaned. GHA public-writer B001/WT-1 PR5 merged5dbe9cc09799911ed896c2e35007167d41d2d853 from approved4497cf7 after fresh correctness/security review, frozenKit/host checks and all5 replacement checks; Linux100 tests/2 Windows-only skips. Primary Azure B004 record-only PR10 mergeddefd414e383b3190a3c8540faf36fe81eec8947f from independently approvedfb7207c with all6 checks green; primary record verified before safe fast-forward and checkout cleanup.

Current bounded branchFix/ScaffoldInterruptionRelease starts from fetcheddefd414. Parent temporary-only Azure probe confirms KeyboardInterrupt bypasses Exception handler/success-release and leaves2 live references in an importing process; probe explicitly released both. Repair acquired-reference release on every exit while preserving existing Azure Exception wrapping/rollback, original BaseException identity, usual release diagnostics and completed outputs. No GHA renderer/provider/options/pins/workflow changes; the accepted GHA repair is a source-design reference, not a runtime dependency. One exclusive L4 writer owns canonical script/tests/three generated mirrors; parent owns record/goal/commit/review/delivery. Expected under200 authored lines, measured before freeze; focused WriteTransactionTests and generation/freshness/AST checks locally, full Linux/Windows/Terraform/lint/host gates remote. No local Terraform/C# build or live Azure/new native session. Next: bounded implementation/verification, audit/checkpoint, fresh immutable independent correctness/security review and exact-head CI, merge/cleanup, then provider composition/B002 and0b/rest.

### B004 source acceptance - 2026-10-14

Parent inspected the complete source/test delta and accepted129 authored changed lines (script17/6, tests106/0); generated copies excluded. Finally attempts all recorded-reference releases, per-invocation error state preserves escaping BaseException identity over release errors; first new non-OSError success-release error propagates after remaining attempts. Azure's existing broad Exception rollback/wrapping remains. Three new unique regression methods cover write/final-close interruption, release errors/continued attempts, actual close/rename, caller actively handling another exception and RuntimeError/ValueError wrapped rollback. After removing identical duplicate test insertions caused by ambiguous edit-tool responses, final35 focused tests ran successfully with7 existing platform/privilege skips.237-file generation/freshness, all5 Python3.9 AST/definition uniqueness, mirror parity and whitespace pass; all nontransaction AST and4 representative pure plans matchdefd414. Local WindowsApps pwsh returned access denied before launch; equivalent owned Python generator/check succeeded. Writer stopped all sessions/handles and relinquished lease. Next: private audit/checkpoint, fresh immutable independent correctness/filesystem-security review and exact-head full two-OS/Terraform/lint/host CI; then merge/cleanup before provider composition/B002. No renderer/workflow/provider/options/pin change.
