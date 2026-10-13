---
name: scaffold
description: Provide the Azure infrastructure an app profile asks for, in the File structure `azure:structure` records — two Terraform roots (foundation, instance), one instance resource per workload kind, the optional components from `azure:security`, `azure:deployment` and `azure:build`, and the CI gate. Use when an app stack scaffolds Azure infrastructure from its own `infra/app.json`; it writes nothing until the profile and options validate.
kind: utility
domain: azure
profile: scaffold
applicability: new or existing Azure projects driven by an app profile
requires: azure
provenance: house
---

# Azure infrastructure, driven by an app profile

`azure:scaffold` is a component provider: an app stack (for example `dotnet:scaffold`) decides the
workloads, builds `infra/app.json`, and runs this skill to turn that profile into Terraform. `azure` holds
no language- or framework-specific values; everything app-facing — images, packages, runtimes, build
commands, env values — comes from the profile. Its options, presets, components and templates sit beside
this file; `scripts/scaffold.py` renders them. It needs Python 3.9+ and nothing else; the gate below needs
Terraform.

## Create a project

```sh
python -B <skill-directory>/scripts/scaffold.py new --into <existing-dir> --profile infra/app.json [--preset public|private] [--config f.json] [--set k=v]... [--dry-run] [--no-input]
python -B <skill-directory>/scripts/scaffold.py options [--preset <p>] [--config f.json] [--set k=v]...
python -B <skill-directory>/scripts/scaffold.py options --schema
```

`--profile` defaults to `<into>/infra/app.json`; a missing profile exits 2. A profile given from outside
`<into>` is copied to `<into>/infra/app.json` and counts as an output for the `--into` conflict check — a
profile already identical at that path is not a conflict. On a TTY, without `--no-input` and without
`--dry-run`, `new` prompts for every app-agnostic option no layer set, showing its allowed values and
default; `options` never prompts. `options --schema` prints one JSON object to stdout — and nothing
else — mapping each option to `{type, allowed (if any), default, description}`; this is how a driving
stack discovers azure's options without reading `options.json` directly.

Options layer lowest-first: `options.json` defaults, the preset, `--config`, then `--set`. Every value is
validated — pattern, enum, `requires`, `conflicts` — and the profile is validated against the resolved
options, before anything is written; any failure exits 2. `--into` refuses and lists every output that
already exists; `--dry-run` prints the plan and writes nothing.

Options: `http_host` (`container-apps` default, `app-service`), `background_host` (`container-apps`),
`functions_host` (`flex-consumption` default, `container-apps`), `messaging` (`none`, `service-bus`) with a
`topics` list — which workloads publish and consume is a profile field, not an option — `key_vault`, `vnet`,
`workflows`, `terraform_version`, `azurerm_version`. Presets: `public` (`{}`) and `private` (`vnet=true`,
`key_vault=true`).

## The app profile (`infra/app.json`)

```json
{
  "schema_version": 1,
  "name": "demo",
  "workloads": [
    {"id": "api", "kind": "http", "port": 8080,
     "probes": {"liveness": "/health/live", "readiness": "/health/ready"},
     "build": {"image": "<command that builds and pushes $IMAGE>"},
     "messaging": {"publishes": ["orders"]}},
    {"id": "worker", "kind": "background", "build": {"image": "..."},
     "messaging": {"consumes": [{"topic": "orders", "types": ["order-placed"]}]}},
    {"id": "functions", "kind": "functions", "runtime": {"name": "dotnet-isolated", "version": "10.0"},
     "build": {"package": "<command that writes a zip to $PACKAGE>"}}
  ],
  "database": {"engine": "postgres", "schemas": ["public"], "migration": {"build": {"image": "..."}, "command": ["..."]}},
  "settings": {
    "ConnectionStrings__Postgres": "Host={postgres.host};Database={postgres.database};Username={postgres.user};Ssl Mode=Require",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "{monitor.connection_string}",
    "AZURE_CLIENT_ID": "{identity.client_id}"
  },
  "verify": ["dotnet restore", "dotnet build", "dotnet test"]
}
```

- `name`: `^[a-z][a-z0-9]{1,11}$`.
- `workloads`: a list, possibly empty, with unique `id` values matching `^[a-z][a-z0-9-]{0,15}$`.
  - `kind` is `http`, `background` or `functions`; it selects a host through the matching `*_host` option.
  - `http` requires an integer `port` (1-65535) and `probes.liveness` / `probes.readiness` paths.
  - `functions` requires `runtime.name` and `runtime.version`. An `http` workload on `app-service`
    requires `runtime`, whose `name` is one of `dotnet`, `node`, `python`, `java`.
  - `build.image` is required when the workload's host is Container Apps; `build.package` is required on
    App Service or Flex Consumption. A workload may carry both; azure uses the one its host needs and
    ignores the other. A command may reference `$IMAGE`, `$TAG`, `$PACKAGE`, `$REGISTRY` and `$REPOSITORY`.
    A `build.image` command must `docker push $IMAGE`: azure resolves the pushed digest from the local
    image's `RepoDigests` afterward and deploys that, never the mutable `$TAG` reference.
    No `build.*` command may contain `${{` or `}}`; azure passes it to the workflow through a step `env:`
    value and runs it with `bash -c`, never splicing it into the YAML, but rejects the GitHub Actions
    expression syntax outright regardless. A `database.migration.command` entry is held to the same
    rejection, but for a different reason: it renders into the migration Terraform job's own `command` list
    (`hcl_list`, via `escape_hcl_string`), not into a workflow step, so the ban is defense in depth rather
    than a splicing risk.
  - `settings`: optional, same shape and validation as the top-level `settings` below, merged over it for
    that workload — the workload's value wins on a key clash.
  - `messaging`: optional, `{"publishes": [topic...], "consumes": [{"topic": ..., "types": [...]}...]}`.
    Every `publishes` topic, and every `consumes` entry's `topic`, must be in the `topics` option; either
    requires `messaging=service-bus`; either exits 2 otherwise. A `consumes` entry's `types` is a required,
    non-empty, duplicate-free list matching `^[A-Za-z0-9._-]{1,128}$`, naming the `type` application
    property values that subscription accepts; it exits 2 otherwise. Each `publishes` topic grants that
    workload `Azure Service Bus Data Sender` at topic scope. Each `consumes` entry creates a subscription
    `<id>-<topic>` in the instance root — `max_delivery_count = 10`, lock `PT1M`, dead-lettering on expiry
    and filter errors, a `handled-types` SQL rule filtering on that entry's own `types` — and grants that
    workload `Azure Service Bus Data Receiver` at that subscription's scope. The deploy workflow removes
    each subscription's `$Default` rule after applying, so only `handled-types` is left filtering. A
    workload gets no role on a topic or subscription it doesn't name.
- `database`: optional, `engine` must be `postgres`. An optional `schemas` list (default `["public"]`,
  identifiers matching `^[a-z_][a-z0-9_]{0,62}$`, unique) names every schema the migration job prepares:
  it creates each non-`public` schema (`public` always exists) owned by the migrator, then grants every
  workload identity `USAGE` on the schema plus `SELECT, INSERT, UPDATE, DELETE` on its tables and
  `USAGE, SELECT` on its sequences — both for objects that already exist and, through
  `ALTER DEFAULT PRIVILEGES`, for ones a later migration creates. An optional `migration` holds
  `build.image` (a shell command) and `command` (a list of strings) for the migration job. The migration
  job's env uses only the top-level `settings`, never a workload's.
- `settings`: an object mapping an env name (`^[A-Za-z_][A-Za-z0-9_]*$`) to a string. Literal text around
  `{token}` placeholders is kept; `{{` and `}}` escape literal braces. Tokens:
  - `postgres.host`, `postgres.database`, `postgres.user` (requires `database`; `postgres.user` resolves
    to the consuming workload's own identity, or the migrator's inside the migration job);
  - `servicebus.namespace` (requires `messaging=service-bus`);
  - `keyvault.uri` (requires `key_vault`; only a workload whose effective settings — top-level merged with
    its own — reference this token gets `Key Vault Secrets User`, at the vault's scope);
  - `monitor.connection_string`;
  - `identity.client_id` (the consuming workload's own identity).
  - An unknown token, or a token used without its required option, exits 2.
- `verify`: optional, a list of non-empty shell commands (the app stack's restore, build and test, for
  example), validated and run the same way as `build.*` commands — no `${{` or `}}`, passed through a step
  `env:` and run with `bash -c`. When present, `pr.yml` and `ci.yml` run them in a sentence-named `verify`
  job before any Azure job.
- Unknown top-level keys and unknown workload keys exit 2.

## Generated Terraform

`infra/instance/workloads.tf` holds `locals.workloads` (by id: kind, host, port, probes, runtime, and its
`messaging.publishes`/`consumes` topics) and
`locals.settings` (by id: an env map of Terraform expressions resolved from the profile's tokens); both are
always present, empty when the profile has no workloads. One user-assigned identity per workload lives in
`identity.tf`. Each host component — `container-apps.tf`, `app-service.tf`, `flex-consumption.tf` — holds
one resource `for_each`-ing the workloads hosted there; `function-storage.tf` holds one storage account per
`functions` workload, shared by both function hosts. A database adds the server, the per-workload Postgres
roles, and — when the profile has a `migration` — `migration.tf`'s Container Apps Job, which runs an
azure-owned init-container script that creates each workload's Postgres role and grants it table and
sequence privileges under the migrator.

## Build and adapt

Components live under `components/<id>/`: `component.json` declares a `when` expression over the resolved
options plus the profile-derived flags (`has_database`, `has_migration`, `has_functions`, `has_background`,
`http_container_apps`, `http_app_service`, `functions_flex`, `functions_container`, `container_apps_used`),
and `files/<repo-relative path>.in` holds its fragment of each output file, rendered with
`__PLACEHOLDER__` substitution and `@if`/`@only-if` flag lines. `templates/` holds the files every project
gets, including `infra/.gitignore` and `infra/env/{ci,prod}.tfvars`. `workloads.tf`, `migration.tf` and
every `.github` file are generated directly from the profile in Python, not from `.in` fragments, because
their shape depends on the workload list itself.

## The GitHub Actions workflows (`.github/`, when `workflows`)

Every generated workflow follows `github-actions`'s structure, style, security, testing, build, errors and
domain-design contracts: a top-level `permissions: contents: read` that jobs re-declare in full,
`id-token: write` only on a job that signs in, `persist-credentials: false` on every checkout, every remote
`uses:` pinned to a commit SHA with a `# vX.Y.Z` comment, a `ci-complete` aggregator in `pr.yml` and
`ci.yml`, `timeout-minutes` on every job that has one (GitHub does not allow it on a job that only `uses:` a
reusable workflow — the timeout lives in that reusable workflow's own job instead), and a GitHub environment
per deploy target: `ci` (the shared non-production environment `ci.yml` deploys), `preview` (a PR's
instance) and `prod`. Give `preview` and `prod` their own protection rules in repository settings; `prod`
should allow deployment from `v*` tags only.

- `pr.yml` triggers on `opened`, `synchronize`, `reopened` and `labeled`, with the verification concurrency
  group `${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}` and
  `cancel-in-progress: true`. It runs an optional sentence-named `verify` job (when the profile has one),
  `validate` (fmt, init `-backend=false`, validate, matrixed over both roots), `plan` (a real
  `terraform plan` against the `ci` environment's state, uploaded as an artifact), then `compute-preview-id`
  and `deploy-preview` — gated on `vars.AZURE_FOUNDATION_READY == 'true'` and the PR carrying the
  `deploy-preview` label — which calls `deploy-environment.yml` for the `preview` environment with an
  ephemeral id derived from the PR number. A `break-preview-lock` job, `if: cancelled()`, breaks the
  preview's state blob lease over OIDC if the verification group's `cancel-in-progress` killed a run mid
  `apply`.
- `ci.yml` has the fixed concurrency group `ci` (`cancel-in-progress: false`): the optional `verify` job,
  `validate`, then `deploy-ci` calling `deploy-environment.yml` for the `ci` environment.
- `prod.yml` has the fixed concurrency group `prod`: on a semver tag push (`v[0-9]+.[0-9]+.[0-9]+`),
  `assert-release-commit` fails the run (`::error::`) unless the tag's commit is the `headSha` of the
  latest successful `ci.yml` run on `main` (via `actions/github-script`, `actions: read` only on that job);
  then `validate`; then `deploy-prod`, needing both, calling `deploy-environment.yml` for the protected
  `prod` environment, using `prod.tfvars`.
- `pr-closed.yml`: on PR close, recomputes the same ephemeral id and, only if the PR carried the
  `deploy-preview` label, calls `destroy-preview.yml`.
- `deploy-environment.yml` (`workflow_call`; inputs `environment`, `tfvars-file`, `ephemeral-id`): its job's
  concurrency group is `preview-<ephemeral-id>` when ephemeral, or `inputs.environment` otherwise, so a
  preview apply and its eventual `destroy-preview.yml` teardown share one queue. It signs in and initializes
  Terraform for `foundation` through the `terraform-login-init` composite action; applies foundation unless
  `ephemeral-id` is set (a preview reuses `ci`'s foundation); reads its outputs; builds every workload (and
  the migration image) through the `build-workload` composite action, one step per workload; initializes the
  `instance` root at key `<name>/instance.tfstate`, or `<name>/instance.<ephemeral-id>.tfstate` when
  ephemeral; runs the migration job if the profile has one; applies the instance with
  `-var ephemeral_id=$EPHEMERAL_ID` (always passed, empty by default); and deploys each package or waits for
  each Container App to become healthy.
- `destroy-preview.yml` (`workflow_call`; inputs `ephemeral-id`, `tfvars-file`): its job shares the same
  `preview-<ephemeral-id>` concurrency group, initializes the `instance` root at that PR's ephemeral key and
  runs `terraform destroy`.
- `ephemeral_id` is the full `pr<N>`, never truncated — two PRs whose numbers differ by exactly a multiple
  of a truncated width must never collide. The Terraform variable allows up to 6 lowercase alphanumeric
  characters; a PR number that doesn't fit makes the id-computing step fail with
  `::error file=<workflow>::…`, naming the limit, instead of deploying under a mangled key.
- A pull request's preview is opt-in: `deploy-preview` and its teardown run only when the PR carries the
  `deploy-preview` label. Applying a label needs the repository's `triage` role, so the label is also the
  trust gate — a fork's PR can't self-label its way into a preview run. `plan`, `deploy-preview`,
  `break-preview-lock` and `destroy-preview` additionally require
  `github.event.pull_request.head.repo.full_name == github.repository` as defense in depth.
- `.github/dependabot.yml` keeps the `github-actions` ecosystem's pins current, weekly, with a 7-day
  cooldown.

## Validation and publication

Every generated root must pass `terraform fmt -check -recursive`, `terraform init -backend=false` and
`terraform validate`. `.agents/tests/test_scaffold.py` covers profile validation, token resolution,
interactive mode, `--into` refusal and rollback, file lists, and — only with `AZURE_SCAFFOLD_TERRAFORM=1`
set, using a local Terraform install — the gate above against a foundation-only profile and a sandbox
profile (http, background and functions workloads plus Postgres) across its host options.
