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

`azure:scaffold` writes this into a new or existing repository, driven by the app profile at
`infra/app.json`. Each `<area>.tf` file exists only when its component is selected; `workloads.tf`,
`identity.tf`, `function-storage.tf` and the `images` variable are always present, with their maps empty
when the profile has no workloads.

```text
<repo>/
  .scaffold.azure.json
  .github/
    dependabot.yml
    actions/
      terraform-login-init/action.yml   Azure sign-in plus Terraform init, for jobs that touch state
      build-workload/action.yml         builds one workload's image or package from its build command
    workflows/
      pr.yml                 verify, then plan, then an optional preview instance (ephemeral_id=pr<N>)
      ci.yml                 push to main: verify, then deploy the shared dev environment
      prod.yml               tag push v*: deploy through the protected prod environment
      pr-closed.yml          destroys the PR's preview instance
      deploy-environment.yml reusable (workflow_call): applies foundation and one instance
      destroy-preview.yml    reusable (workflow_call): destroys one PR's instance
  infra/
    app.json             the app profile
    README.md
    .gitignore
    env/dev.tfvars  env/prod.tfvars
    foundation/
      versions.tf  providers.tf  backend.tf  variables.tf  locals.tf  outputs.tf
      main.tf               resource group, state storage, Log Analytics, deployment identity
      network.tf            vnet
      container-apps.tf     environment and registry
      app-service.tf        plan
      flex-consumption.tf   Flex Consumption plan
      postgres.tf           server
      key-vault.tf
      service-bus.tf        namespace and topics
    instance/
      versions.tf  providers.tf  backend.tf  variables.tf  locals.tf  outputs.tf
      data.tf                 foundation lookups
      workloads.tf            locals.workloads and locals.settings, rendered from the profile
      identity.tf             one identity per workload
      function-storage.tf     one storage account per `functions` workload
      container-apps.tf       a Container App per workload hosted there
      app-service.tf          a web app and staging slot per `http` workload hosted there
      flex-consumption.tf     a Function App per `functions` workload hosted there
      postgres.tf             database and migrator identity
      migration.tf            the migration Container Apps Job, when the profile has one
      key-vault.tf            role assignment
      service-bus.tf          subscriptions, rules and role assignments
```
