# Phase A spec: `azure:scaffold` as an app-profile-driven component provider

Goal and decisions: [GOAL.md](GOAL.md). Read it first. Rules that still bind the generated Terraform:
`.agents/azure/contract/{structure,style,build,deployment,security}/SKILL.md`. Scaffold contract shape (layered
JSON options, components, LF, stdlib only, exit codes) is unchanged from v1 except where stated here.

## CLI

```text
python -B scripts/scaffold.py new --into <repo> [--profile <file>] [--preset p] [--config f.json] [--set k=v]... [--dry-run] [--no-input]
python -B scripts/scaffold.py options [--profile <file>] [--preset p] [--config f.json] [--set k=v]...
```

- `--name/--destination` and the `name`, `host` and `database` options are removed.
  - `name` comes from the profile.
  - Hosts come from the profile's workload kinds plus the host options.
  - The database comes from the profile.
- `--profile` defaults to `<into>/infra/app.json`. A missing profile exits 2. A profile outside `<into>` is
  copied to `<into>/infra/app.json` and counts as an output for the conflict check. A profile already at that
  path is not a conflict.
- Interactive mode:
  - Runs on `new`, when `sys.stdin.isatty()`, without `--no-input` and without `--dry-run`.
  - Prompts for every option no layer set, showing its allowed values and default; Enter keeps the default.
  - Validates each answer and re-asks on an invalid one.
  - Inject the input function so tests can drive it.
  - The `options` command never prompts.
- Atomic `--into` (refuse on any conflict; roll back on a write error) and exit codes stay as in v1.

## Options (`options.json`, app-agnostic only)

| Option | Values | Default |
|---|---|---|
| `http_host` | `container-apps`, `app-service` | `container-apps` |
| `background_host` | `container-apps` | `container-apps` |
| `functions_host` | `flex-consumption`, `container-apps` | `flex-consumption` |
| `key_vault` | bool | `false` |
| `vnet` | bool | `false` |
| `messaging` | `none`, `service-bus` | `none` |
| `topics`, `subscriptions` | as v1 | `[]` |
| `workflows` | bool | `true` |
| `terraform_version`, `azurerm_version` | as v1 | as v1 |

Presets: `public` (`{}`) and `private` (`vnet=true`, `key_vault=true`). Delete `foundation` and `web-api`.
`.scaffold.azure.json` adds `"profile": "infra/app.json"` and the profile's sha256.

## Profile (`schema_version` 1); every violation exits 2 with a precise message

- `name`: `^[a-z][a-z0-9]{1,11}$`.
- `workloads`: a list, possibly empty (foundation only), with unique `id` values matching `^[a-z][a-z0-9-]{0,15}$`.
  - `kind` is `http`, `background` or `functions`.
  - `http` requires an integer `port` (1–65535) and `probes.liveness` / `probes.readiness`, each a path
    starting with `/`.
  - `functions` requires `runtime.name` and `runtime.version`. An `http` workload on `app-service` requires
    `runtime`, whose `name` is one of `dotnet`, `node`, `python`, `java`.
  - `build`:
    - `build.image` (a shell command) is required for every workload hosted on Container Apps;
    - `build.package` is required for App Service and Flex Consumption.
    - A command may reference `$IMAGE`, `$TAG` and `$PACKAGE`.
- `database`: optional, with `engine` `postgres` and an optional `migration` holding `build.image` and
  `command` (a list of strings). It may appear only when `database` is present.
- `settings`: an object mapping an env name (`^[A-Za-z_][A-Za-z0-9_]*$`) to a string.
  - Tokens `{...}` must be in the fixed set.
  - `postgres.*` requires a database, `servicebus.namespace` requires `messaging=service-bus`, and
    `keyvault.uri` requires `key_vault`.
  - Literal text around tokens is kept.
  - `{{` and `}}` escape literal braces.
- Unknown top-level keys and unknown workload keys exit 2.

## Generated Terraform

The shared files and foundation stay as v1, apart from the changes below. Workloads are data, not per-workload
files.

- `infra/instance/workloads.tf` holds `locals.workloads`, a map rendered from the profile by id with kind, port
  and probes. `locals.settings` is a map from id to an env map whose values are Terraform expressions
  produced from the tokens:
  - `postgres.host` → the server FQDN;
  - `postgres.database` → the database name;
  - `postgres.user` → that workload's identity name;
  - `servicebus.namespace` → `"<namespace>.servicebus.windows.net"`;
  - `keyvault.uri`;
  - `monitor.connection_string` → the App Insights connection string, read from foundation through a `data`
    lookup;
  - `identity.client_id` → that workload's identity client id.
  - Use `format()` or string templates. Every env value is a plain value; there are no secrets.
- Variable `images`, `map(string)`: each workload id maps to its image, defaulting per id to
  `mcr.microsoft.com/k8se/quickstart:latest` so a first plan works before any build.
- Identities: one user-assigned identity per workload, `${instance_prefix}-${id}-id`, with AcrPull on the
  registry when a Container Apps host is used, and a `time_sleep` after creation.
- `container-apps` hosts:
  - http workloads get external ingress on `port` with liveness and readiness probes;
  - background workloads get no ingress and `min_replicas = 1`;
  - functions on `container-apps` follow the azurerm 5 documentation for Functions on Container Apps. If
    azurerm has no first-class support, use a Container App with the functions image, ingress on its `port`
    (default 80) and `AzureWebJobsStorage__*` identity settings, and say so in the report.
  - The Container Apps environment and registry live in foundation whenever any Container Apps host is used.
- `app-service` http:
  - the plan lives in foundation;
  - a Linux web app has `application_stack` from `runtime`: `dotnet_version`, `node_version`,
    `python_version`, or `java_version` with `java_server = "JAVA"` and `java_server_version` (azurerm 5);
  - it uses a user-assigned identity, the health check path from `probes.readiness`, and a `staging` slot.
- `flex-consumption` functions:
  - an `azurerm_function_app_flex_consumption`;
  - its own storage account with shared keys disabled and a deployment blob container;
  - storage authentication by user-assigned identity, with `runtime_name` and `runtime_version` from the
    profile;
  - roles on that account: Storage Blob Data Owner, Storage Queue Data Contributor and Storage Table Data
    Contributor (Durable needs queues and tables);
  - app settings: the workload's `settings`, plus `AzureWebJobsStorage__accountName`,
    `AzureWebJobsStorage__credential = managedidentity` and `AzureWebJobsStorage__clientId`.
- Database (when present):
  - The foundation server is as v1, including the public firewall rule.
  - The instance creates the database and the migrator identity, and registers that identity as the Entra
    admin (as v1).
  - **Migration job:** `azurerm_container_app_job` `${instance_prefix}-migrate-caj`, manual trigger, migrator
    identity, image `var.images["migration"]` (default quickstart), and `command` from the profile.
    - Env: the profile `settings` resolved with the migrator as `postgres.user`.
    - An init container (`postgres:17-alpine`) runs an azure-owned script. It gets an Entra token for
      `https://ossrdbms-aad.database.windows.net` from the Container Apps identity endpoint (`IDENTITY_ENDPOINT`
      and `IDENTITY_HEADER` with the migrator's client id, using `wget`), then connects as the migrator.
    - For each workload identity, the script creates the principal idempotently
      (`pgaadauth_create_principal` inside a `DO` block that checks `pg_roles`) and grants `CONNECT` on the
      database and `USAGE` on schema `public`. It also grants
      `ALTER DEFAULT PRIVILEGES FOR ROLE <migrator> IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO <identity>`,
      plus the same for sequences (`USAGE, SELECT`).
    - The script is a template file in the component, rendered with the identity names. It is
      language-agnostic SQL and shell.
- Remove `DATABASE_*`, the `database_env` local and the v1 `container_image` variable.

## Workflow (`.github/workflows/infra.yml`, when `workflows`)

Keep `permissions: {}`, the concurrency rule, the `AZURE_FOUNDATION_READY` gate and the OIDC variables. Jobs:

1. **`validate`**: unchanged.
2. **`plan`** (PR): as v1, with `-var-file=../env/dev.tfvars`.
3. **`deploy`** (push to `main`, `environment: dev`), steps in order:
   - checkout, `setup-terraform`, `azure/login`;
   - foundation: init, plan `-out`, apply;
   - read `registry_login_server` and the other needed outputs with `terraform output -raw`, then
     `az acr login`;
   - for each workload, rendered from the profile at scaffold time:
     - with `build.image`: set `IMAGE=<login_server>/<name>-<id>:${{ github.sha }}` and `TAG=${{ github.sha }}`,
       run the command, and add `"<id>": "$IMAGE"` to an images JSON file;
     - with `build.package`: set `PACKAGE=$RUNNER_TEMP/<id>.zip` and run the command.
     - The migration image is built the same way, under key `migration`.
   - instance: init, then `apply -target` of the migration job and its identity, admin and database, then
     `az containerapp job start` and a wait for its execution to succeed (poll `az containerapp job execution show`);
   - instance: full plan `-out` with `-var-file=../env/dev.tfvars -var-file=$RUNNER_TEMP/images.auto.tfvars.json`,
     then apply;
   - package deploys: `az functionapp deploy --src-path $PACKAGE --type zip` for Flex Consumption.
     App Service deploys to the `staging` slot, waits for the readiness path to return 200, then
     `az webapp deployment slot swap`.
   - Container Apps: wait until each app's latest revision is ready
     (`az containerapp revision list --query`).
- `hashicorp/setup-terraform@v3` with `terraform_wrapper: false`.

## README (`infra/README.md`)

Update it with the profile, the bootstrap, the repository variables, and the order the deploy job follows.
Keep the OIDC immutable-subject note and the `$Default` note.

## Contracts and skill text

- Update `azure:structure`'s File structure: the instance gains `workloads.tf`, per-host `<host>.tf`
  files, `migration.tf` and `infra/app.json`, and loses `container-apps.tf` and `app-service.tf`. Leave every
  other contract rule untouched.
- Rewrite `SKILL.md`:
  - azure:scaffold is a component provider an app stack drives with `infra/app.json`;
  - document the profile schema and the token list;
  - give the hand-run command `new --into <repo> --profile infra/app.json`;
  - list the options and the interactive mode.

## Tests (`.agents/tests/test_scaffold.py`)

- Profile validation: each rule above has an exit-2 case.
- Token resolution: one setting with literal text plus two tokens renders the expected Terraform expression.
- Interactive mode: drive the injected input with valid and invalid answers; check that preset and `--set`
  values are not prompted.
- `--into` refusal and rollback stay. A copied profile is a conflict when `infra/app.json` already differs.
- File lists for:
  - (a) an empty-workloads profile;
  - (b) a sandbox profile: http api, background worker, flex-consumption functions and a Postgres database
    with migration.
- Terraform gate (`AZURE_SCAFFOLD_TERRAFORM=1`, fail rather than skip when terraform is missing), each case
  validating both roots:
  - (a);
  - (b) with `public`;
  - (b) with `private`;
  - (b) with `http_host=app-service` and `functions_host=container-apps`;
  - a messaging case using a `servicebus.namespace` token.
- Use a temporary `TF_PLUGIN_CACHE_DIR`, and run the cases sequentially.

## Additions (2026-10-12, sent to the writer mid-run)

1. The deploy job also exports `REGISTRY` (the login server) and `REPOSITORY` (`<name>-<id>`), with
   `IMAGE=$REGISTRY/$REPOSITORY:$TAG`.
2. `build` may contain both `image` and `package`; azure uses the one the selected host needs.
3. A workload may carry its own `settings`, merged over the top-level ones (the workload wins). The migration
   job uses only the top-level settings.
4. `options --schema` prints JSON `{option: {type, allowed?, default, description}}`. It is the published
   way for a driving stack to discover azure's options; callers never read `options.json`.
5. Evidence from `sandbox-dotnet/infra/SCAFFOLD_GAPS.md`:
   - With `vnet`: App Service gets an `app` subnet (10.0.4.0/24) delegated to `Microsoft.Web/serverFarms`
     plus VNet integration on the app and its slot. Flex Consumption gets a `functions` subnet
     (10.0.3.0/24) delegated to `Microsoft.App/environments`.
   - The Terraform principal gets `Key Vault Secrets Officer`.
   - A resource-group budget with 80% and 100% notifications.

## Done when

- The sync check passes, and `kit:check` passes at the repository's pinned release 1.2.1 (use kit's `check.py` from
  `git -C C:/Users/TommySeery/source/repos/tj-agents/kit show v1.2.1:...` or an archive of that tag).
- All non-Terraform tests pass locally. Run only **one** Terraform gate case locally, because host memory is
  limited; CI runs the rest.
- There are no comments beyond a rare why, and no company names.
