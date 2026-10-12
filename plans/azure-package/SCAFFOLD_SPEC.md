# `azure:scaffold` v1 implementation spec

Approved design: [PROPOSAL.md](PROPOSAL.md) § Scaffold v1. Rules it must follow: `azure:structure` (including
its File structure), `azure:style`, `azure:build`, `azure:deployment` and `azure:security` under
`.agents/azure/contract/`. Model the Python shape on `rust:scaffold`.

## Layout

```text
.agents/azure/utility/scaffold/
  SKILL.md                 what it makes and the commands to run, nothing else
  options.json
  presets/foundation.json  presets/web-api.json
  components/<id>/component.json + files/<repo-relative paths>.in
  templates/               files every output gets
  scripts/scaffold.py
.agents/tests/test_scaffold.py
.github/workflows/terraform.yml   toolchain gate (repository CI, not a template)
```

## CLI (Python 3.9+, stdlib only, pathlib, LF endings)

```text
python -B scripts/scaffold.py new --into <existing-dir> [--preset p] [--config f.json] [--set k=v]... [--dry-run]
python -B scripts/scaffold.py new --name <n> --destination <existing-dir> [same options]
python -B scripts/scaffold.py options [--preset p] [--config f.json] [--set k=v]...
```

- Precedence, lowest first: `options.json` defaults, then the preset, then `--config`, then `--set`.
  `--name` sets option `name` with `--set` precedence. `--set` values are parsed by the option's type (bool
  `true`/`false`, lists as comma-separated).
- Exit 2 before any write: an unknown key or value, a type error, a failed `validation` pattern, a missing
  required option, a broken `requires`, or a `conflicts` violation. Other failures exit 1.
- `--into`: the directory must exist. Compute every output path first. If **any** exists, print each
  conflict and exit 1 without writing anything. Record `<into>/.scaffold.azure.json`.
- `--name/--destination`: `<destination>/<name>` must not exist; the scaffold creates it and writes the same
  tree plus `.scaffold.azure.json`.
- `--dry-run` prints the resolved options and the file list, and writes nothing.
- `options` prints each resolved option with the layer that set it.
- `.scaffold.azure.json` holds `package: "azure"`, `skill: "scaffold"`, `version` (from options.json),
  `preset` and the resolved `options`.
- No shell-outs, no network, no `git init`.

## Options

| Option | Type | Default | Validation / notes |
|---|---|---|---|
| `name` | string | required | `^[a-z][a-z0-9]{1,11}$` |
| `host` | enum `none`, `container-apps`, `app-service` | `none` | |
| `database` | enum `none`, `postgres-flexible` | `none` | |
| `messaging` | enum `none`, `service-bus` | `none` | |
| `topics` | list of string | `[]` | each `^[a-z][a-z0-9-]{0,49}$`; requires `messaging=service-bus` when non-empty |
| `subscriptions` | list of `topic` strings this workload consumes | `[]` | each must be in `topics`; requires `messaging=service-bus` |
| `key_vault` | bool | `false` | |
| `vnet` | bool | `false` | |
| `workflows` | bool | `true` | |
| `terraform_version` | string | `1.16.5` | `^\d+\.\d+\.\d+$` |
| `azurerm_version` | string | `~> 5.0` | |

`options.json` declares type, allowed values, default, description, `pattern`, `requires` and `conflicts`.
Components are selected by `when` expressions over options in `component.json`: foundation (always), vnet
(`vnet`), container-apps (`host=container-apps`), app-service (`host=app-service`), postgres-flexible,
key-vault and service-bus.

Presets:

- `foundation`: `{}`, the foundation only.
- `web-api`: `host=container-apps`, `database=postgres-flexible`, `key_vault=true`, `vnet=true`.

## Generated Terraform (both roots must pass `fmt -check`, `init -backend=false` and `validate`)

Render with simple `__PLACEHOLDER__` substitution, like `rust:scaffold`. Terraform-level choices such as
`private_networking` are **Terraform variables**, not scaffold options. Where a resource depends on the
network, emit both branches with `count = var.private_networking ? 1 : 0`, and add a `precondition` or
validation so `private_networking = true` without the vnet component fails at plan time. Simplest form: emit
the `private_networking` variable only when vnet is selected, and otherwise hard-code public.

### Common files (each root)

- `versions.tf`: `required_version = ">= 1.15, < 2.0"`; azurerm `__AZURERM_VERSION__`; `time ~> 0.13` when
  needed.
- `providers.tf`: `features {}`, `resource_provider_registrations = "none"`, no subscription id (it comes
  from `ARM_SUBSCRIPTION_ID`).
- `backend.tf`: `backend "azurerm" { use_azuread_auth = true }`.
- `variables.tf`:
  - `env` (validation `^[a-z]{2,5}$`) and `region` (validation in `uks`, `ukw`, `weu`, `neu`);
  - in instance only: `ephemeral_id` (default `""`, validation `^$|^[a-z0-9]{1,8}$`);
  - per component: `private_networking` (bool, default true, only when vnet); in foundation, the GitHub OIDC
    inputs `github_owner`, `github_owner_id`, `github_repository` and `github_repository_id`.
- `locals.tf`:
  - region map `uks`→`uksouth`, `ukw`→`ukwest`, `weu`→`westeurope`, `neu`→`northeurope`;
  - `prefix = "${var.region}-${var.env}-<name>"`; instance also has `instance_prefix` with the
    ephemeral suffix;
  - a `compact` prefix (no hyphens) for storage and registry names;
  - `tags = { workload, environment, region, managed_by = "terraform" }` plus `ephemeral_id` when set.
- Length preconditions: storage and registry names ≤ 24 lowercase alphanumerics, Container App name ≤ 32.

### Foundation `main.tf` (always)

- Resource group `${prefix}-rg`.
- State storage account (`compact` + `st`): Standard LRS, `shared_access_key_enabled = false`, TLS1_2, no
  public nested items, and container `tfstate`. Grant `Storage Blob Data Contributor` to the current
  principal (`data.azurerm_client_config.current`), and `depends_on` the container.
- Log Analytics `${prefix}-law`: PerGB2018, 30 days, `daily_quota_gb = 0.5`.
- Application Insights `${prefix}-appi`, workspace-based.
- Deployment identity `${prefix}-deploy-id` (user-assigned):
  - `azurerm_federated_identity_credential` entries for `repo:<owner>@<owner_id>/<repo>@<repo_id>:pull_request`
    and `...:environment:${var.env}`, issuer `https://token.actions.githubusercontent.com`, audience
    `api://AzureADTokenExchange`;
  - roles `Contributor` and `Role Based Access Control Administrator` on the RG, and
    `Storage Blob Data Contributor` on the state account.
- Outputs: resource group, state account, deploy identity client id, and the App Insights connection string
  (sensitive).

### Components

- **vnet**:
  - foundation: VNet `${prefix}-vnet` `10.0.0.0/16`, subnet `cae` `10.0.0.0/23` delegated to
    `Microsoft.App/environments`, subnet `private-endpoints` `10.0.2.0/24`.
- **container-apps**:
  - foundation: registry (`compact` + `cr`), Basic, `admin_enabled = false`; Container Apps environment
    `${prefix}-cae` on Consumption, logs to the workspace, `infrastructure_subnet_id` = the `cae` subnet when
    private networking is on.
  - instance:
    - workload identity `${instance_prefix}-id` with AcrPull on the registry, then a `time_sleep` 60s;
    - Container App `${instance_prefix}-ca`: single revision, ingress on 8080, image variable
      `container_image` defaulting to `mcr.microsoft.com/k8se/quickstart:latest`, the registry block using
      the identity, and env vars `AZURE_CLIENT_ID`, `APPLICATIONINSIGHTS_CONNECTION_STRING` and
      `OTEL_SERVICE_NAME`.
- **app-service**:
  - foundation: Linux service plan `${prefix}-asp`, SKU variable default `P0v3`.
  - instance: workload identity, a Linux web app `${instance_prefix}-app` with the user-assigned identity,
    `https_only`, `always_on`, health check path `/health`, and a `staging` slot.
- **postgres-flexible**:
  - foundation: server `${prefix}-psql`, version 17, SKU `B_Standard_B1ms` with a variable override,
    32768 MB, and `authentication { active_directory_auth_enabled = true, password_auth_enabled = false,
    tenant_id }`.
    - With private networking: `public_network_access_enabled = false`, a private endpoint in
      `private-endpoints`, and the `privatelink.postgres.database.azure.com` zone linked to the VNet.
    - Diagnostic settings to the workspace.
  - instance:
    - migrator identity `${instance_prefix}-migrator-id`, with a `time_sleep`, then
      `azurerm_postgresql_flexible_server_active_directory_administrator` for it;
    - the database `<name>` with the `ephemeral_id` suffix, which `depends_on` the administrator;
    - the app gets `DATABASE_HOST`, `DATABASE_NAME` and `DATABASE_USER` (the workload identity name) env
      vars when a host is selected.
- **key-vault**:
  - foundation: vault (`${prefix}-kv`, truncated to 24) with `rbac_authorization_enabled = true`, soft delete
    7 days, purge protection off, and `public_network_access_enabled` = `!private_networking`. With private
    networking, a private endpoint plus `privatelink.vaultcore.azure.net`. Diagnostic settings.
  - instance: `Key Vault Secrets User` for the workload identity on the vault.
- **service-bus**:
  - foundation: namespace `${prefix}-sbns`, Standard, `local_auth_enabled = false`, `minimum_tls_version =
    "1.2"`. Topics from `topics`: `requires_duplicate_detection = true`, `default_message_ttl = "P14D"`.
    Diagnostic settings.
  - instance: for each of `subscriptions`, a subscription `<name>-<topic>` with `max_delivery_count = 10`,
    `lock_duration = "PT1M"`, `dead_lettering_on_message_expiration = true` and
    `dead_lettering_on_filter_evaluation_error = true`; a SQL rule `handled-types` with a variable list of
    types; and `Azure Service Bus Data Receiver` at subscription scope. Also `Azure Service Bus Data Sender`
    at topic scope for each topic in `topics`. The `$Default` rule is out of scope for v1; say so in the
    README.

When no host is selected, the instance root still validates: identity, database and role pieces stand alone.

### Other output

- `infra/env/dev.tfvars`: `env = "dev"`, `region = "uks"`, and placeholder GitHub ids as obvious zeros.
- `infra/README.md`: the bootstrap (personal account, `AZURE_CONFIG_DIR` isolation), state migration
  commands, the repository variables the workflow needs, how to read the owner and repository ids
  (`gh api repos/<owner>/<repo> --jq '.owner.id, .id'`), and the offline gate commands.
- `.github/workflows/infra.yml` (when `workflows`):
  - triggers: `pull_request` and `push` to `main` on `infra/**`, plus `workflow_dispatch`;
  - `permissions: {}` at the top;
  - `validate` job: matrix over the roots, running fmt, `init -backend=false` and validate, with no Azure
    login;
  - `plan` job on PRs, only when `vars.AZURE_FOUNDATION_READY == 'true'`: `id-token: write`,
    `azure/login@v2` with OIDC from `vars.AZURE_CLIENT_ID`, `vars.AZURE_TENANT_ID` and
    `vars.AZURE_SUBSCRIPTION_ID`; `ARM_USE_OIDC=true`; init with `-backend-config` from
    `vars.TFSTATE_RESOURCE_GROUP` and `vars.TFSTATE_STORAGE_ACCOUNT` and key `<name>/<root>.tfstate`; then
    `plan -var-file=../env/dev.tfvars`;
  - `apply` job on push to `main`: `environment: dev`, plan `-out=tfplan` then `apply tfplan`, foundation
    then instance;
  - concurrency `infra-${{ github.ref }}` with `cancel-in-progress: false`;
  - `hashicorp/setup-terraform@v3` with `terraform_version: __TERRAFORM_VERSION__`.

## Tests (`.agents/tests/test_scaffold.py`, unittest, stdlib)

- Precedence across all four layers; `options` output names each layer.
- Each exit-2 case: unknown key, bad enum, pattern failure, missing `name`, `subscriptions` without the
  topic, `topics` without `messaging`.
- `--dry-run` writes nothing.
- `--into` refuses when one target exists, lists it, and leaves the tree untouched. `--name` refuses when the
  project exists.
- One generation per preset into a temp dir, asserting the expected file list.
- A toolchain test that runs Terraform on each preset's output (both roots: `fmt -check -recursive`,
  `init -backend=false`, `validate`) **only when `AZURE_SCAFFOLD_TERRAFORM=1`**, and fails rather than skips
  if the variable is set and `terraform` is missing.

## Repository CI: `.github/workflows/terraform.yml`

- Matrix `ubuntu-latest` and `windows-latest`, `permissions: contents: read`, `hashicorp/setup-terraform@v3`
  pinned to `1.16.5`, Python 3.12.
- Runs `AZURE_SCAFFOLD_TERRAFORM=1 python -B -m unittest discover -s .agents/tests -p "test_scaffold.py"`.
- Add a third preset run as a full-option config (container-apps + postgres + key-vault + service-bus with
  topics and subscriptions + vnet) as a test case so every component is validated. App Service gets its own
  case because it conflicts with container-apps.

## Done when

- `pwsh .agents/sync-generated.ps1` has regenerated the outputs and `-Check` passes.
- kit `check.py` passes.
- `python -B -m unittest discover -s .agents/tests` passes locally with `AZURE_SCAFFOLD_TERRAFORM=1`, using
  the local Terraform 1.15.8.
- No comments in generated Terraform or Python beyond a rare why. No sample application logic.
