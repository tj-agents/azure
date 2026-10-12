# The app stack drives Azure infrastructure

Status: planning (2026-10-12). Authorized by Tommy: "finish this off now", after agreeing that dotnet drives
and azure stays app-agnostic. Decisions marked **pending** below need Tommy before Phase A starts.

## Outcome

`dotnet:scaffold new ... --set cloud=azure` produces a .NET solution plus its Azure infrastructure and a deploy
workflow that builds, migrates and deploys every selected host, with no hand wiring. `azure` contains nothing
.NET-specific; any other stack can drive it the same way.

## Why the current scaffold is wrong

`azure:scaffold` v1 is a standalone "new project". It hand-picks resources, and every app-facing value is a
placeholder:

- the image is the hello-world quickstart;
- the database env vars are named `DATABASE_*`;
- the App Service has no runtime;
- the workflow never builds or deploys the app.

The shared scaffold contract's `--into` section is on hold for the same reason (coordinator, 2026-10-12).

## Interface: the app profile (`infra/app.json`, owned by azure, written by the app stack)

```json
{
  "schema_version": 1,
  "name": "demo",
  "workloads": [
    {"id": "api", "kind": "http", "port": 8080,
     "probes": {"liveness": "/health/live", "readiness": "/health/ready"},
     "build": {"image": "<command that builds and pushes $IMAGE>"}},
    {"id": "worker", "kind": "background", "build": {"image": "..."}},
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

- `kind` selects a host through azure's options (see Decisions):
  - `http` → Container App with ingress on `port` using the probes, or App Service with the profile's
    `runtime`;
  - `background` → Container App with no ingress;
  - `functions` → Flex Consumption Function App with identity-based storage, or a Functions container in
    the Container Apps environment.
- `database.engine`: `postgres` → Postgres Flexible Server (Entra-only) plus a Container Apps Job that runs
  `migration` under the migrator identity.
- `settings` values are strings that may embed azure's fixed tokens: `postgres.host`, `postgres.database`,
  `postgres.user`, `servicebus.namespace`, `keyvault.uri`, `monitor.connection_string`,
  `identity.client_id`.
  - `identity.client_id` and `postgres.user` resolve to the consuming workload's own identity.
  - Azure resolves the tokens to Terraform expressions. An unknown token exits 2.
  - The connection-string format belongs to the app stack, for example
    `"Host={postgres.host};Database={postgres.database};Username={postgres.user};Ssl Mode=Require"`, so
    azure holds no client-library syntax.
- Build commands are the app stack's. Azure's workflow sets `$IMAGE`/`$PACKAGE`/`$TAG`, logs in to the
  registry, runs them, runs the migration job, applies Terraform and waits for the new revisions.

## Phases

### Phase A: azure (PR on `Feature/AppProfile`)

1. Document the profile schema, and make `new --into <repo> --profile infra/app.json` required for workloads.
   App-agnostic options stay as `--set`: `key_vault`, `vnet`, `messaging`, `topics`, `subscriptions`.
2. Generate one instance-root resource per workload from its `kind`. Remove the placeholders: `DATABASE_*`,
   the quickstart default (kept only as the bootstrap image before the first build), and the runtime-less
   App Service. App Service leaves v1 unless a profile asks for it.
3. Add the Postgres migration Container Apps Job and the passwordless connection-string token.
4. Rewrite `infra.yml` to build from the profile, migrate, apply and verify the deploy.
5. Tests: profile validation (unknown kind, unknown token, missing build), file lists, and the Terraform
   gate on a sandbox-shaped profile (api + worker + functions + postgres).

### Phase B: dotnet (PR after A merges; the dotnet scaffold v1 and hosts work is merged)

1. Add option `cloud: none|azure`. `azure` requires `database` in `none|postgres` (sqlserver is rejected
   until azure has an Azure SQL component).
2. .NET wiring when `cloud=azure`:
   - health checks at `/health/live` and `/health/ready`, with readiness checking the DbContext;
   - an Npgsql Entra token provider through `Azure.Identity`;
   - `UseAzureMonitor` when telemetry is on;
   - SDK container publish properties;
   - an EF migration bundle as the migration command.
3. Write `infra/app.json` from the dotnet choices, then hand off to azure's scaffold (**pending Q1**).
4. Verify: `dotnet build` and `test` on the sandbox preset with `cloud=azure`, then azure's Terraform gate on
   the result.

### Phase C: contracts

- Send the coordinator the profile fields, the app-agnostic component list, the workflow flow and azure's
  kit type (**pending Q3**) for the `SCAFFOLD_CONTRACT.md` revision.
- The kit `Template` owner change reaches the Codex thread only when Tommy pastes it.

## Decisions

- 2026-10-12, Tommy: the app stack drives; azure holds no .NET-specific values.
- 2026-10-12, Tommy (Q1): dotnet runs azure. dotnet writes `infra/app.json` and runs azure's `scaffold.py`
  from `--azure-scaffold <path>` or `AZURE_SCAFFOLD`. Without either, it prints the exact azure command.
- 2026-10-12, Tommy (Q2): every divergent choice is a configuration option with a default, like
  `vite create`, never a design question.
  - Hosting is an azure option per workload kind: `http_host` (`container-apps` default, `app-service`),
    `background_host` (`container-apps`), `functions_host` (`flex-consumption` default, `container-apps`).
  - Both scaffolds get an interactive `new`: on a TTY, with no `--no-input`, it prompts for each unset option
    with its allowed values and default.
  - When dotnet runs azure, `--set azure.<option>=<value>` passes azure's options through.
- 2026-10-12, Tommy (Q3): kit is Template, published as Template 1.4.0 today; the repository is still
  `tj-agents/kit` and old addresses keep working. azure stays a structural `stack` and its scaffold becomes a
  component provider. The Template owner migrates consumers to 1.4.0, so this goal does not.

## Lanes

- Design: L3 (this session).
- Phase A and B implementation: L4 lane agents, one writer per repository.
- Review: an independent L4 review lens per PR.

## Progress

- 2026-10-12: read dotnet `origin/main` `693618d`:
  - hosts are api/functions/durable/worker;
  - the connection string key is `ConnectionStrings:Postgres`;
  - OTLP tracing only;
  - no health checks, no Azure.Identity and no container publish.
- 2026-10-12: plan written; Tommy answered Q1–Q3 (Decisions).

## Next Steps

1. Send the coordinator the contract revision: profile fields, app-agnostic components, workflow flow,
   interactive mode and host options.
2. Write the Phase A spec and dispatch it at L4. Review, PR and merge.
3. Phase B on dotnet, then the end-to-end verification on the sandbox preset.
