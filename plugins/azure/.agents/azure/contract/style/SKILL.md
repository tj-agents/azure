---
name: style
description: Tommy's agreed Azure style — resource naming, region codes, tags, formatting and variable declarations. Read before writing or reviewing Azure that touches it; nothing applies until it is recorded here.
kind: contract
domain: azure
profile: core
applicability: Azure projects
requires: azure
provenance: house
---

# Azure style

Owns naming, formatting, comments and lint policy.

## Agreed

- Names are `{region}-{env}-{name}[-{ephemeral_id}]-{type}`, built in `locals`.
  - `type` is a short suffix: `rg`, `law`, `appi`, `vnet`, `cae`, `ca`, `caj`, `psql`, `kv`, `sbns`, `id`,
    `asp`, `app`.
  - Storage accounts and registries use the same parts with no hyphens.
  - A `precondition` enforces Azure's length limits, so `plan` fails before any API call.
  - Source: Microsoft Cloud Adoption Framework, "Define your naming convention".
- `region` is a short code mapped to an Azure location in `locals`: `uks` → `uksouth`, `ukw` → `ukwest`,
  `weu` → `westeurope`, `neu` → `northeurope`. Why: names stay short and the location stays a configuration
  value. Source: house.
- Every resource gets `local.tags`: `{ workload, environment, region, managed_by = "terraform" }`, plus
  `ephemeral_id` when set. Why: cost and ownership queries. Source: Cloud Adoption Framework, "Define your
  tagging strategy".
- `terraform fmt` is the formatter. Every variable has a `type`, a `description` and, where the value is
  constrained, a `validation`. Source: Terraform docs, "Input variables".
