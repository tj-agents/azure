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

## Next Steps

1. Review, PR and merge slice 1, then notify `sandbox-dotnet-17`. Then slice 3 (sticky slot settings).
