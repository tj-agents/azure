---
name: deployment
description: Tommy's agreed Azure deployment — environments and regions as configuration, remote state with Entra auth, the foundation bootstrap, the plan-then-apply CI flow and observability wiring. Read before writing or reviewing Azure state, environments, deploy workflows or telemetry; nothing applies until it is recorded here.
kind: contract
domain: azure
profile: core
applicability: Azure projects
requires: azure
provenance: house
---

# Azure deployment

Owns environments, Terraform state, the deploy flow and observability wiring.

## Agreed

- Environment and region are input variables. Per-environment values that are not secret live in
  `infra/env/<env>.tfvars`, and there are no workspaces. Environment-dependent defaults, such as SKU and
  replicas, live in `locals`. Source: Terraform docs, "Workspaces" (not suited to separate environments).
- Remote state:
  - The backend is a partial `backend "azurerm" { use_azuread_auth = true }`, completed at `init` with
    `-backend-config`.
  - State lives in one `tfstate` container, keyed `<name>/<root>[.<ephemeral_id>].tfstate`.
  - The state account disables shared keys.
  - Source: azurerm backend docs.
- Foundation bootstraps once with local state, creating its state storage account, then migrates into it
  with `init -migrate-state`. Source: house.
- CI flow:
  - A pull request runs the gate and a `terraform plan` against remote state.
  - `main` plans to a saved file and applies that file through a protected GitHub environment.
  - The `concurrency` group never cancels in progress. Why: killing an apply leaks the state lease.
  - Source: Terraform docs, "Running Terraform in automation".
- Observability:
  - One Log Analytics workspace per environment in foundation, with 30-day retention and a daily cap.
  - One workspace-based Application Insights per workload. Apps receive
    `APPLICATIONINSIGHTS_CONNECTION_STRING` and a stable `OTEL_SERVICE_NAME`.
  - Diagnostic settings send `allLogs` plus `AllMetrics` to the workspace from Postgres, Key Vault and
    Service Bus.
  - Source: Azure Monitor docs.
