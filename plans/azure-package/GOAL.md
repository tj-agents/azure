# `tj-agents/azure` package with `azure:scaffold` (basic first version)

Canonical goal for this repository. The owner's private host notes keep source locations that must not
appear here.

## Outcome

A basic first version of Tommy's Azure standards plus a scripted scaffold. The scaffold generates Terraform
for Azure infrastructure from JSON configuration and runs by hand on Windows, Linux and macOS with no AI.
Keep this version small and correct; breadth grows later.

Done when:

1. The repository is a kit `stack` (kit 1.2.1), with detection globs `*.tf` and `*.bicep`.
2. `azure:direction` holds the skill tree; `azure:knowledge` records only what Tommy has proven.
3. The contracts hold a basic set of conventions Tommy approved through `azure:learning`'s convention
   procedure. Likely areas:
   - naming;
   - foundation vs instance roots and remote state with Entra auth;
   - regions and environments as configuration;
   - identity: a user-assigned identity per workload, data-plane roles, no stored secrets, GitHub OIDC
     deploys;
   - private networking for data services;
   - observability;
   - provider pinning.
4. `azure:scaffold` implements the shared scripted scaffold contract (v0), including `new --into
   <existing-repo>`. That mode writes `infra/` and `.github/workflows/`, refuses before any write when any
   target exists, and records `.scaffold.azure.json`.
   - Components: foundation, app-service (staging slot), container-apps, postgres-flexible, key-vault, vnet,
     service-bus.
   - Presets: `foundation`, `web-api`.
   - Every preset's output passes `terraform fmt -check`, `init -backend=false` and `validate`.
5. Sync check and `kit:check` pass, CI is green, and the follow-up PR is reviewed and merged.

## Constraints

- The scaffold's defaults and examples target Tommy's **personal** Azure account. Observations of other
  estates inform generic patterns only.
- No organisation-specific identifiers anywhere in this repository. Run the owner's deny-list audit before
  every commit and push.
- Azure access while building this is read-only. Never plan or apply against any subscription.
- Workflow YAML follows `tj-agents/github-actions` conventions; this package owns the Azure resources,
  identity, federated-credential subjects and deploy commands.

## Decisions

- 2026-10-12: Azure is surveyed through a read-only Azure MCP server for reconnaissance only. Practice and
  scaffold targets are the personal account.
- 2026-10-12: `new --into` is required (shared contract § Composing scaffolds in one repository).
- 2026-10-12, Tommy: approved all 27 rules in [PROPOSAL.md](PROPOSAL.md), including the new contracts
  `azure:deployment` and `azure:security`, Entra-only Postgres and plan-then-apply CI.
- 2026-10-12, Tommy: approved scaffold v1 as proposed, with presets `foundation` and `web-api`.
- 2026-10-12, Tommy: microservice-communication standards belong in a future `dotnet:microservice-messaging`
  contract. This package owns only the Service Bus resources, and nothing is created for it here.
- 2026-10-12, Tommy: the repository is public so CI runs free, like its siblings.

## Progress

- 2026-10-12: repository generated from kit 1.2.1; sync check and `kit:check` pass.

## Next Steps

1. Survey the reference estates and put the proposed contract rules, the scaffold option schema and presets,
   and a microservice-communication packaging recommendation to Tommy in one batched question.
2. On a feature branch, write the contracts, direction and scaffold, with tests and a toolchain gate. Then
   review, open the PR and merge.
3. Open the marketplace catalog PR.
