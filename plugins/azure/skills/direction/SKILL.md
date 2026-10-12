---
name: direction
description: Where Tommy is headed with Azure — the goal, the skill tree being worked through, and the references every explanation and convention cites. Use when choosing or sequencing what to learn next, or when sourcing a Azure explanation or a proposed convention.
kind: knowledge
domain: azure
profile: knowledge
applicability: Azure learning
requires: azure
provenance: house
---

# Where Tommy is headed with Azure

`azure:knowledge` tracks where Tommy is now; this tracks where they are going.

## The goal

Run a .NET web API (`sandbox-dotnet`) in a personal Azure subscription, provisioned with Terraform and
deployed from GitHub over OIDC. It uses the identity, networking, data and messaging shapes of a modern
production estate. Practice happens only in the personal account.

## The skill tree

| # | Area | Status |
|---|------|--------|
| 1 | Subscriptions, resource groups, regions and cost budgets | Not started |
| 2 | Terraform: providers, state, plan and apply, the lock file | Not started |
| 3 | Remote state on Blob Storage with Entra auth | Not started |
| 4 | Managed identities and data-plane RBAC | Not started |
| 5 | GitHub OIDC federated credentials and deploy workflows | Not started |
| 6 | Log Analytics, Application Insights and OpenTelemetry | Not started |
| 7 | Container Apps: environment, registry pull, ingress, revisions | Not started |
| 8 | Postgres Flexible Server with Entra-only auth | Not started |
| 9 | VNets, delegated subnets, private endpoints and private DNS | Not started |
| 10 | Key Vault in RBAC mode | Not started |
| 11 | Service Bus topics, subscriptions, filters and dead-lettering | Not started |
| 12 | App Service plans, web apps and slot swaps | Not started |

Update a row's status when Tommy levels up an area.

## The rule

These projects are for learning, not output. A slower path that Tommy understands beats a fast one they do
not. Check that it landed: ask them to explain it back, or to write the next piece before you show yours.

## References

- Microsoft Learn, Azure documentation: the service docs and the Cloud Adoption Framework naming and tagging
  guidance.
- Azure Well-Architected Framework.
- HashiCorp Terraform documentation, and the `azurerm` provider and backend documentation.
- GitHub Docs, "Configuring OpenID Connect in Azure", and the GitHub changelog.
