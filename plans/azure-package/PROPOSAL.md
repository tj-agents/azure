# Proposal: basic Azure conventions and `azure:scaffold` v1

Status: awaiting Tommy's decision (2026-10-12). Each rule is a proposed `azure:learning` convention; the owning
contract is in brackets. Sources: Terraform azurerm provider docs, Azure Well-Architected, and the observed
reference estates (generic patterns only).

## Contract rules

### Structure [`azure:structure`]

1. Each workload has two Terraform roots.
   - `infra/foundation` holds slow, costly or shared resources: resource group, Log Analytics, VNet,
     Container Apps environment, Postgres server, Service Bus namespace and topics, Key Vault.
   - `infra/instance` holds per-deployment resources: identities, the app, its database, subscriptions and
     role assignments.
   - Why: tearing down an instance drops a database, never a server.
2. `instance` reads `foundation` with `data` sources by conventional name, never `terraform_remote_state`.
   Why: each root depends only on the names it needs, not on another root's state format.
3. Each root uses flat files: `versions.tf`, `providers.tf`, `backend.tf`, `variables.tf`, `locals.tf`,
   `outputs.tf` and one `<area>.tf` per resource area. There are no registry modules until a second consumer
   needs one.
4. An optional `ephemeral_id` variable suffixes instance names and state keys, so a PR can deploy its own
   throwaway instance.

### Style [`azure:style`]

5. Names are `{region}-{env}-{name}[-{ephemeral_id}]-{type}`, built in `locals`.
   - `type` is a short suffix: `rg`, `law`, `appi`, `vnet`, `cae`, `ca`, `caj`, `psql`, `kv`, `sbns`, `id`,
     `asp`, `app`.
   - Storage accounts and registries use the same parts with no hyphens.
   - A `precondition` enforces Azure's length limits so `plan` fails early.
6. `region` is a short code mapped to an Azure location in `locals` (`uks` → `uksouth`, `ukw` → `ukwest`,
   `weu` → `westeurope`, `neu` → `northeurope`).
7. Every resource gets `local.tags`: `{ workload, environment, region, managed_by = "terraform" }`, plus
   `ephemeral_id` when set.
8. `terraform fmt` is the formatter. Variables have a `type`, a `description` and a `validation` where the
   value is constrained.

### Build [`azure:build`]

9. Pin `required_version = ">= 1.15, < 2.0"` and `azurerm ~> 5.0` (latest 5.9.0), plus `azuread`, `random` and
   `time` where used, each with `~> MAJOR.MINOR`. CI pins the exact CLI (1.16.5).
10. Commit `.terraform.lock.hcl`.
11. The provider block sets `features {}` and `resource_provider_registrations = "none"`. The subscription
    comes from `ARM_SUBSCRIPTION_ID`, never a committed value.
12. The gate for every root is `terraform fmt -check -recursive`, then `init -backend=false`, then `validate`.

### Environments and state [`azure:deployment`, a new contract]

13. Environment and region are input variables. Per-environment values that are not secret live in
    `infra/env/<env>.tfvars`. There are no workspaces. Environment-dependent defaults, such as SKU and
    replicas, live in `locals`.
14. The backend is a partial `backend "azurerm" { use_azuread_auth = true }`, completed at `init` with
    `-backend-config`. State lives in one `tfstate` container with key
    `<name>/<root>[.<ephemeral_id>].tfstate`. Storage shared keys are disabled.
15. Foundation bootstraps once with local state, creating the state storage account, then migrates into it.
16. CI flow:
    - A PR runs the gate and `terraform plan` against remote state.
    - `main` applies a saved plan through a protected GitHub environment.
    - The `concurrency` group never cancels in progress, because killing an apply leaks the state lease.

### Security and identity [`azure:security`, a new contract]

17. Each workload gets one user-assigned identity, plus a separate migrator identity where schema rights
    matter. The app identity holds data rights only.
18. Role assignments use data-plane roles at the narrowest scope: a topic, a container or a vault. Never use
    `Contributor` for a workload.
19. No stored secrets:
    - storage `shared_access_key_enabled = false`;
    - registry `admin_enabled = false`;
    - Service Bus `local_auth_enabled = false`;
    - Key Vault in RBAC mode, never access policies;
    - Postgres with Entra auth only (`password_auth_enabled = false`), with the migrator identity as its Entra
      admin.
20. Add a `time_sleep` after creating an identity or grant that a later resource uses immediately. Why: Entra
    propagation.
21. Deploys authenticate with GitHub OIDC through a federated credential and never with a client secret. Use
    the repository's subject format, which is the immutable `repo:<owner>@<id>/<repo>@<id>:...` form for
    repositories created after 2026-07-15. Workflows set `permissions: {}` at the top and grant
    `id-token: write` per job.

### Networking [`azure:security`]

22. A `private_networking` variable, default `true`, controls data-service exposure.
    - When on: data services have public access off and use a private endpoint plus a `privatelink.*` DNS
      zone linked to the VNet, and the Container Apps environment sits in a delegated /23 subnet.
    - When off: the cheap, public variant for throwaway sandboxes.
    - Layout: VNet `10.0.0.0/16`, `cae` `10.0.0.0/23`, `private-endpoints` `10.0.2.0/24`.

### Observability [`azure:deployment`]

23. One Log Analytics workspace per environment in foundation, with 30-day retention and a daily cap.
24. One workspace-based Application Insights per workload. Apps receive
    `APPLICATIONINSIGHTS_CONNECTION_STRING` and a stable `OTEL_SERVICE_NAME`.
25. Diagnostic settings send `allLogs` plus `AllMetrics` to the workspace from Postgres, Key Vault and Service
    Bus.

### Messaging resources [`azure:structure`]

26. Topics are created in foundation, with duplicate detection on and a TTL set.
27. Each consumer owns its subscription in its own instance root, named `<consumer>-<topic>`:
    - `max_delivery_count = 10` and lock `PT1M`;
    - dead-lettering on expiry and on filter errors;
    - a SQL rule filtering on a `type` application property.

## Scaffold v1

```text
python -B scripts/scaffold.py new --into <repo> [--preset foundation|web-api] [--config f.json] [--set k=v]... [--dry-run]
python -B scripts/scaffold.py options [--preset <p>] [--config f.json]
```

`new --name/--destination` creates a fresh repository containing the same tree.

| Option | Type | Default | Notes |
|---|---|---|---|
| `name` | string | required | Workload name: lowercase, 2–12 characters. |
| `host` | `none` \| `container-apps` \| `app-service` | `none` | `app-service` adds a `staging` slot. A slot needs P0v3 or higher (~£55/month); the default SKU is P0v3. |
| `database` | `none` \| `postgres-flexible` | `none` | B1ms outside prod, Entra-only. |
| `messaging` | `none` \| `service-bus` | `none` | Standard SKU (~£8/month); `topics` and `subscriptions` are lists. |
| `key_vault` | bool | `false` | |
| `vnet` | bool | `false` | `private_networking` requires it. |
| `workflows` | bool | `true` | Writes `.github/workflows/infra.yml`. |
| `terraform_version` | string | `1.16.5` | |
| `azurerm_version` | string | `~> 5.0` | |

- Components: `foundation` (always), `vnet`, `container-apps`, `app-service`, `postgres-flexible`, `key-vault`,
  `service-bus`. `container-apps` conflicts with `app-service`, and `private_networking` requires `vnet`.
- Presets:
  - `foundation`: foundation and workflows only.
  - `web-api`: `host=container-apps`, `database=postgres-flexible`, `key_vault=true`, `vnet=true`. Container
    Apps on Consumption can scale to zero, so this is the cheap default host.
- Output:
  - `infra/{foundation,instance}/`, `infra/env/dev.tfvars` and `infra/README.md`, which holds the bootstrap and
    hand-run commands;
  - `.github/workflows/infra.yml`;
  - `.scaffold.azure.json`.
- Tests:
  - unittest: precedence, validation, `--dry-run`, `--into` refusal, and one generation per preset;
  - a CI matrix on Windows and Ubuntu running `fmt -check`, `init -backend=false` and `validate` on each
    preset.

## Microservice communication: where it belongs

Recommendation:

- `azure` owns the Service Bus **resources** (rules 26–27).
- The envelope and reliability patterns go in a new **`dotnet:microservice-messaging`** contract beside
  `dotnet:microservice-boundaries`, which already says "coordinate through an outbox". The patterns:
  - CloudEvents 1.0 structured mode with `type` mirrored to an application property, and versioned type names;
  - a transactional outbox whose `MessageId` is the outbox row id, so broker duplicate detection works;
  - an inbox table for idempotent consumers;
  - PeekLock with manual completion;
  - dead-lettering of non-retryable failures with a reason, and capped exponential backoff for transient ones.
- A language-agnostic `messaging` package only pays off when a second language consumes it. Every service in
  view is .NET.
