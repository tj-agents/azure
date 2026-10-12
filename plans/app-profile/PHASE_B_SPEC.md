# Phase B spec: `dotnet:scaffold --set cloud=azure`

Goal and decisions: [GOAL.md](GOAL.md). Azure's input contract: [PHASE_A_SPEC.md](PHASE_A_SPEC.md) and the
merged `azure:scaffold` SKILL.md on `tj-agents/azure` main. All .NET-specific Azure wiring lives here; azure
holds none.

Repository: `tj-agents/dotnet`, branch `Feature/AzureHosting` from `origin/main`, in the worktree
`dotnet/.worktrees/Feature-AzureHosting`. Engine: `.agents/dotnet/utility/scaffold/scripts/scaffold.py`
(components append into `$VARIABLE$` slots).

## Options

- `cloud`: `none` (default) or `azure`. Constraint: `cloud=azure` requires `database` to be `none` or `postgres`.
- Pass-through:
  - `--set azure.<option>=<value>` is not validated against dotnet's schema. It is collected and forwarded to
    azure as `--set <option>=<value>`, and only when `cloud=azure`; otherwise it exits 2.
  - `--config` may hold an `"azure": {...}` object that is forwarded the same way.
- `new --azure-scaffold <path>`, or the `AZURE_SCAFFOLD` env var, gives the path to azure's `scaffold.py`.
- Interactive mode, matching azure:
  - on a TTY, with no `--no-input` and no `--dry-run`, `new` prompts for every option no layer set, with its
    allowed values and default;
  - when `cloud=azure` and the azure path is known, it then asks azure's options by running azure's `options`
    command and prompting for those not set;
  - inject the input function for tests.

## .NET wiring (component `azure`, `when: {"cloud": "azure"}`, plus per-host and per-data parts)

- **API health:** `AddHealthChecks()`, plus `AddDbContextCheck<ApplicationDbContext>("database")` when using
  EF. Map `/health/live` (no checks) and `/health/ready` (all checks). The package is
  `Microsoft.Extensions.Diagnostics.HealthChecks.EntityFrameworkCore` when using EF.
- **Postgres Entra auth** (database `postgres`): in `AddInfrastructure`, build an `NpgsqlDataSource`.
  - When the connection string has no password, `UsePeriodicPasswordProvider` returns a token from
    `DefaultAzureCredential(new DefaultAzureCredentialOptions { ManagedIdentityClientId = configuration["AZURE_CLIENT_ID"] })`
    for `https://ossrdbms-aad.database.windows.net/.default`, refreshed every 55 minutes.
  - EF uses `UseNpgsql(dataSource)` and Dapper uses the same data source.
  - A connection string with a password (local dev, Testcontainers) bypasses the provider, so the existing
    integration fixtures keep working.
  - Package: `Azure.Identity`.
- **Telemetry** (`opentelemetry`): when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set, add `UseAzureMonitor()`.
  - The API uses `Azure.Monitor.OpenTelemetry.AspNetCore`.
  - The worker and Functions use `Azure.Monitor.OpenTelemetry.Exporter` with `AddAzureMonitorTraceExporter`.
  - OTLP export stays.
- **Containers:** the worker csproj gets `<EnableSdkContainerSupport>true</EnableSdkContainerSupport>`; the API
  already supports container publish. The Functions csproj gets the same property plus `<ContainerBaseImage>`
  set to the verified Azure Functions .NET isolated base image for net10.0.
- Azure's interactive options come from `options --schema`, never from azure's `options.json`.
- **Migrations** (EF): `.config/dotnet-tools.json` pins `dotnet-ef`, and the API gets
  `Microsoft.EntityFrameworkCore.Design` with `PrivateAssets=all`. Add `infra/migration.Dockerfile`:
  `FROM mcr.microsoft.com/dotnet/runtime-deps:10.0`, copy `efbundle`, `ENTRYPOINT ["/efbundle"]`.
- Pin every new package version in `Directory.Packages.props` through `PACKAGE_VERSIONS`, at the current
  stable version from nuget.org.

## Profile written to `infra/app.json`

- `name` is the solution name lowercased with non-alphanumerics dropped, truncated to 12 characters. The
  string option `cloud-name` overrides it. A derived name that doesn't start with a letter exits 2 and asks
  for `--set cloud-name=<name>`.
- Workloads:

| dotnet host | id | kind | Details |
|---|---|---|---|
| `api` | `api` | `http` | port 8080; probes `/health/live` and `/health/ready`; `build.image`; `runtime` `{name: dotnet, version: "10.0"}`; `build.package` for an App Service host. |
| `worker` | `worker` | `background` | `build.image` |
| `functions` | `functions` | `functions` | `runtime` `{name: dotnet-isolated, version: "10.0"}`; both `build.package` and `build.image` |

- Build commands:
  - api image: `dotnet publish src/<N>.Api/<N>.Api.csproj -c Release /t:PublishContainer -p:ContainerRegistry=$REGISTRY -p:ContainerRepository=$REPOSITORY -p:ContainerImageTag=$TAG`;
  - worker: the same, with the worker project;
  - functions package: `dotnet publish src/<N>.Functions/<N>.Functions.csproj -c Release -o "$RUNNER_TEMP/<id>" && (cd "$RUNNER_TEMP/<id>" && zip -qr "$PACKAGE" .)`;
  - functions image: container publish of the Functions project;
  - api package: the same publish-and-zip as functions.
- Database (`postgres`): `{"engine": "postgres"}`. With EF it adds a `migration`:
  - `build.image`: `dotnet tool restore && dotnet ef migrations bundle --project src/<N>.Infrastructure --startup-project src/<N>.Api -r linux-x64 --self-contained -o "$RUNNER_TEMP/efbundle" --force && docker build -t "$IMAGE" -f infra/migration.Dockerfile "$RUNNER_TEMP" && docker push "$IMAGE"`;
  - `command`: `["/efbundle"]`.
- Top-level `settings`:
  - `ConnectionStrings__Postgres: "Host={postgres.host};Database={postgres.database};Username={postgres.user};Ssl Mode=Require"`
    with postgres;
  - `APPLICATIONINSIGHTS_CONNECTION_STRING: "{monitor.connection_string}"` with telemetry;
  - `AZURE_CLIENT_ID: "{identity.client_id}"`.
- Per-workload `settings`: `OTEL_SERVICE_NAME: "<name>-<id>"`.

## Invocation order (atomic across both scaffolds)

1. Resolve dotnet options, then build the dotnet file plan and the profile in memory.
2. With an azure path: write the profile to a temporary file and run
   `python -B <azure> new --into <tmp-empty-dir> --profile <tmp-profile> --dry-run --no-input [--set ...]`.
   A nonzero exit stops here with azure's message and dotnet's own exit code 2. Nothing has been written.
3. Write the dotnet project, including `infra/app.json` and `infra/migration.Dockerfile`.
4. With an azure path: run azure for real with `--into <project>`. On failure, report azure's output and exit
   1, leaving the dotnet project, because azure's own rollback leaves no partial infra. Without a path, print
   the exact azure command and exit 0.
5. Record the forwarded azure options in `.scaffold.json` under `azure`.

## SKILL.md

Document `cloud=azure`, the pass-through, `--azure-scaffold`/`AZURE_SCAFFOLD`, interactive mode, and the
one-command example for the sandbox preset.

## Tests

- **unittest** (`.agents/tests/test_scaffold.py`):
  - the `cloud` constraint (`sqlserver` + `azure` exits 2);
  - pass-through validation (`azure.*` without `cloud=azure` exits 2);
  - profile content for the `api` and `sandbox` presets;
  - name derivation;
  - interactive prompts;
  - invocation order, with a fake azure script that records its argv and can fail the dry run (nothing
    written) or the real run;
  - a printed command when there's no azure path;
  - the non-azure output byte-identical to before.
- **`verify.py`**: add a `sandbox` + `cloud=azure` configuration that builds and tests the solution.
  - When `AZURE_SCAFFOLD` is set, also run azure into it and run `terraform fmt -check`,
    `init -backend=false` and `validate` on both roots.
  - `scaffold.yml` checks out `tj-agents/azure` (public) into `.azure`, sets
    `AZURE_SCAFFOLD=.azure/.agents/azure/utility/scaffold/scripts/scaffold.py`, and installs Terraform with
    `hashicorp/setup-terraform@v3` (1.16.5, `terraform_wrapper: false`).

## Done when

- The dotnet repository's sync check, Template/kit check and unittest pass locally.
- One local end-to-end run (sandbox + `cloud=azure`, with the real azure scaffold from `tj-agents/azure` main,
  then `dotnet build` and a single `terraform validate` per root) passes.
- CI is green on Windows and Ubuntu.
- There are no comments beyond a rare why.
