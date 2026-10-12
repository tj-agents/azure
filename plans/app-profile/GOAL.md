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
- 2026-10-12: the coordinator has the contract revision. Phase A was dispatched at L4 against
  [PHASE_A_SPEC.md](PHASE_A_SPEC.md), with three additions sent mid-run. [PHASE_B_SPEC.md](PHASE_B_SPEC.md)
  is written.
- 2026-10-12: Phase B was dispatched at L4 in parallel, in `dotnet/.worktrees/Feature-AzureHosting` from
  `693618d`, against the spec with a fake azure script. The real end-to-end check waits for Phase A's merge.
  Each repository has one writer, and builds and Terraform runs one at a time because of host memory.
- 2026-10-12: side items, owned elsewhere:
  - core PR #227 records Tommy's 2:1 Codex/Claude default-host debt;
  - the core Stop-hook false `binding-head-changed` (it compares `head` while the binding stores
    `remote_head_sha`) went to Codex at L4 in `core/.worktrees/Fix-ContinuationStopBindingHead`. The launch
    pointer's paths were garbled by `printf` escapes, but the tab's working directory and the goal's
    relative path are correct, so it was not relaunched.
- 2026-10-12 about 21:27: Tommy's PC crashed and interrupted every agent. Reconciled after the restart:
  - Phase A had 22 uncommitted changed paths in this worktree; resumed the same agent from its transcript.
  - The Phase B worktree was clean at `693618d`; resumed it from the start, with code and unittests only and
    no `dotnet build` until Phase A finishes, because the host is under load.
  - core PR #227: the review found three record-shape and quote issues, fixed in `0b81254`. A fresh lens
    approved, CI was green, and it merged as `48ab568`. The binding was released and the worktree and branch
    removed.
  - core PR #230 (the stop-hook fix) is open, owned by the Codex successor.
- 2026-10-12 after the restart:
  - Phase B code is done (34 unittests, sync and kit check pass), including Functions container support with
    the MCR tag `4-dotnet-isolated10.0` verified and azure options read through `options --schema`. Its one
    dotnet build hasn't run yet.
  - **Host gate:** 0.4 GB free and commit at 54.7 of 55.3 GB, with the nonpaged kernel pool at 16 GB, a driver
    leak and the likely cause of the 21:27 crash. All Terraform and dotnet builds are paused.
  - Resolver: Tommy, by rebooting to clear the pool. Resume condition: commit well below the limit
    (`\Memory\Committed Bytes` under about 40 GB) and the nonpaged pool under 2 GB.
  - Meanwhile only non-build work continues. CI can run the full gates on GitHub runners once the PRs open.
- 2026-10-12:
  - Phase B is committed locally as `22bc9e6` on `Feature/AzureHosting`; its PR follows Phase A's merge.
  - sandbox-dotnet (`tomjseery/sandbox-dotnet`, owner session `repos-4c`) exists, seeded from azure v1. Its
    `infra/SCAFFOLD_GAPS.md` items 5, 7 and 8 were added to Phase A as addition 5.
  - Notify the sandbox-dotnet session (`sandbox-dotnet-17` after the reopen; formerly `repos-4c`) when each
    PR merges.
- 2026-10-13: **pending side workstream (standards defect).** core `continuation_stop.py` evaluates the
  delivery binding in the session's launch directory and never checks which session owns it.
  - Crash recovery started this session in Codex's `core/.worktrees/Fix-ContinuationStopBindingHead`
    (PR #230), so every Stop demands "Initialize persistent-workflow for the active delivery binding". Taking
    over another writer's PR would make this session a second owner.
  - Handoff is blocked by host capacity: C: has 0.23 GB free, commit is 55.1 of 55.3 GB, and the nonpaged pool
    is 19.1 GB.
  - Resolver: Tommy, who frees disk and reboots. Resume condition: C: has at least 20 GB free and commit is
    under 40 GB. Then launch one Codex L4 bounded side workstream in a fresh core worktree with this defect
    as its goal.
- 2026-10-13: the PC crashed again at about 23:09 (a Bitdefender kernel memory leak) and was rebooted. The
  host is healthy: 11.7 GB free and commit at 20.6 of 43.5 GB.
  - New limit from Tommy: at most 7 agent sessions on this machine, so launch no extra CLI sessions. The
    stop-hook ownership side workstream waits for session capacity.
  - Phase A's per-workload messaging (`publishes`/`consumes` with least-privilege roles) landed before the
    crash; the flat `subscriptions` option is gone.
  - Fixed inline: the stale scaffold mirror, the App Service gate case now uses vnet + key_vault, and the
    messaging gate case uses the per-workload fields.
  - Verified: sync `-Check`, `kit:check` 1.2.1, and 55 tests (50 plus 5 Terraform gate cases, all green
    locally against Terraform 1.15.8 and azurerm 5.x).
  - Phase A was committed and pushed as `8c16c93`, as https://github.com/tj-agents/azure/pull/3. CI `ci` and
    `terraform` are green (runs 38321914419 and 38321914119). Independent review is in progress.
  - Phase B was pushed at `22bc9e6`.
  - Local end-to-end run of sandbox + cloud=azure through the real Phase A scaffold: the profile and infra
    generate, but `dotnet build` fails with CS1061 (missing Azure Monitor `using`s in Api, Worker and
    Functions). Sent back to the Phase B agent to fix, then run verify.py's azure configuration with the
    Terraform validate.
  - Phase B fix `3d550b7` was pushed. The sandbox + cloud=azure end-to-end run through the real Phase A
    scaffold builds Release with 0 warnings and 0 errors, and Terraform fmt/init/validate pass on both
    roots. `dotnet test` found no test methods, because the scaffold ships empty test projects, the same as
    before.
  - PR #3 review found real defects:
    - critical: migration role names don't match the Terraform identity names;
    - critical: `GRANT CONNECT` targets a literal string;
    - high: possible `${{` workflow injection;
    - high: the migration job has no length precondition;
    - medium: Key Vault reader rights go to every workload.
    All were sent to the Phase A agent to fix with tests; `java_server = "JAVA"` is kept and the spec
    corrected.

## Next Steps

1. Send the coordinator the contract revision: profile fields, app-agnostic components, workflow flow,
   interactive mode and host options.
2. Write the Phase A spec and dispatch it at L4. Review, PR and merge.
3. Phase B on dotnet, then the end-to-end verification on the sandbox preset.
