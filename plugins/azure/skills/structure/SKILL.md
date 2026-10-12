---
name: structure
description: Tommy's agreed Azure project structure — the foundation and instance Terraform roots, how they find each other, file layout, ephemeral instances and where Service Bus topics and subscriptions live. Read before writing or reviewing Azure that touches it; nothing applies until it is recorded here.
kind: contract
domain: azure
profile: core
applicability: Azure projects
requires: azure
provenance: house
---

# Azure structure

Owns the repository and module layout, visibility, and where dependencies are wired.

## Agreed

- Each workload has two Terraform roots.
  - `infra/foundation` holds slow, costly or shared resources: resource group, state storage, Log Analytics,
    VNet, Container Apps environment, registry, App Service plan, Postgres server, Key Vault, Service Bus
    namespace and topics, and the deployment identity.
  - `infra/instance` holds per-deployment resources: workload identities, the app, its database,
    subscriptions and role assignments.
  - Why: tearing down an instance drops a database, never a server. Source: Azure Well-Architected,
    operational excellence.
- `instance` reads `foundation` through `data` sources by conventional name, never `terraform_remote_state`.
  Why: each root depends on the names it needs, not on another root's state. Source: Terraform docs, "Data
  sources".
- Each root uses flat files: `versions.tf`, `providers.tf`, `backend.tf`, `variables.tf`, `locals.tf`,
  `outputs.tf` and one `<area>.tf` per resource area. There are no registry modules until a second consumer
  needs one. Why: one workload reads top to bottom. Source: house.
- An optional `ephemeral_id` variable suffixes instance names and state keys, so a PR can deploy a throwaway
  instance. Source: house.
- Service Bus:
  - Topics live in foundation, with duplicate detection on and a TTL set.
  - Each consumer owns its subscription in its own instance root, named `<consumer>-<topic>`:
    `max_delivery_count = 10`, lock `PT1M`, dead-lettering on expiry and filter errors, and a SQL rule on a
    `type` application property.
  - Why: the producer owns the contract and the consumer owns its delivery settings.
  - Source: Azure Service Bus docs, "Topics and subscriptions".

## File structure

`azure:scaffold` writes this into a new or existing repository. Each `<area>.tf` file exists only when its
component is selected.

```text
<repo>/
  .scaffold.azure.json
  .github/workflows/infra.yml
  infra/
    README.md
    env/dev.tfvars
    foundation/
      versions.tf  providers.tf  backend.tf  variables.tf  locals.tf  outputs.tf
      main.tf             resource group, state storage, Log Analytics, deployment identity
      network.tf          vnet
      container-apps.tf   environment and registry
      app-service.tf      plan
      postgres.tf         server
      key-vault.tf
      service-bus.tf      namespace and topics
    instance/
      versions.tf  providers.tf  backend.tf  variables.tf  locals.tf  outputs.tf
      data.tf             foundation lookups
      identity.tf         workload identity
      container-apps.tf   app
      app-service.tf      web app and staging slot
      postgres.tf         database and migrator identity
      key-vault.tf        role assignment
      service-bus.tf      subscriptions, rules and role assignments
```
