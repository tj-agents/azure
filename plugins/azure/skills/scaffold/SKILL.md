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
    {"id": "worker", "kind": "background", "build": {"image": "..."}, "messaging": {"consumes": ["orders"]}},
    {"id": "functions", "kind": "functions", "runtime": {"name": "dotnet-isolated", "version": "10.0"},
     "build": {"package": "<command that writes a zip to $PACKAGE>"}}
  ],
  "database": {"engine": "postgres", "migration": {"build": {"image": "..."}, "command": ["..."]}},
  "settings": {
    "ConnectionStrings__Postgres": "Host={postgres.host};Database={postgres.database};Username={postgres.user};Ssl Mode=Require",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "{monitor.connection_string}",
    "AZURE_CLIENT_ID": "{identity.client_id}"
  }
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
    No `build.*` command, nor a `database.migration.command` entry, may contain `${{` or `}}`; azure passes
    each command to the workflow through a step `env:` value and runs it with `bash -c`, never splicing it
    into the YAML, but rejects the GitHub Actions expression syntax outright regardless.
  - `settings`: optional, same shape and validation as the top-level `settings` below, merged over it for
    that workload — the workload's value wins on a key clash.
  - `messaging`: optional, `{"publishes": [topic...], "consumes": [topic...]}`. Every topic named must be in
    the `topics` option, and either list requires `messaging=service-bus`; either exits 2 otherwise. Each
    `publishes` topic grants that workload `Azure Service Bus Data Sender` at topic scope. Each `consumes`
    topic creates a subscription `<id>-<topic>` in the instance root — `max_delivery_count = 10`, lock
    `PT1M`, dead-lettering on expiry and filter errors, a `handled-types` SQL rule — and grants that workload
    `Azure Service Bus Data Receiver` at that subscription's scope. A workload gets no role on a topic or
    subscription it doesn't name.
- `database`: optional, `engine` must be `postgres`. An optional `migration` holds `build.image` (a shell
  command) and `command` (a list of strings) for the migration job. The migration job's env uses only the
  top-level `settings`, never a workload's.
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
gets. `workloads.tf`, `migration.tf` and `.github/workflows/infra.yml` are generated directly from the
profile in Python, not from `.in` fragments, because their shape depends on the workload list itself.

## The deploy workflow (`.github/workflows/infra.yml`, when `workflows`)

`validate` and `plan` are unchanged from a plain Terraform gate. `deploy` (push to `main`): applies
foundation, then for each workload runs its `build.image` or `build.package` command (and the database's
migration build, under key `migration`), setting `REGISTRY` (the registry login server), `REPOSITORY`
(`<name>-<id>`, or `<name>-migration`), `TAG` (the commit sha) and `IMAGE=$REGISTRY/$REPOSITORY:$TAG` for
an image build, or `PACKAGE` for a package build. It then applies the migration job and its identity,
admin and database, runs the job and waits for it, applies the full instance plan with the built images,
deploys each package (`az functionapp deploy` for Flex Consumption; a staging-slot deploy, readiness wait
and swap for App Service), and waits for each Container App's revision to become ready.

## Validation and publication

Every generated root must pass `terraform fmt -check -recursive`, `terraform init -backend=false` and
`terraform validate`. `.agents/tests/test_scaffold.py` covers profile validation, token resolution,
interactive mode, `--into` refusal and rollback, file lists, and — only with `AZURE_SCAFFOLD_TERRAFORM=1`
set, using a local Terraform install — the gate above against a foundation-only profile and a sandbox
profile (http, background and functions workloads plus Postgres) across its host options.
