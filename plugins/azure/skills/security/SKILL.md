---
name: security
description: Tommy's agreed Azure security — managed identities per workload, least-scope data-plane roles, no stored secrets, Entra-only Postgres, GitHub OIDC deploys and private networking for data services. Read before writing or reviewing Azure identity, role assignments, secrets, deploy authentication or network exposure; nothing applies until it is recorded here.
kind: contract
domain: azure
profile: core
applicability: Azure projects
requires: azure
provenance: house
---

# Azure security

Owns identities, role assignments, secrets, deploy authentication and network exposure.

## Agreed

- Each workload gets one user-assigned identity, plus a separate migrator identity where schema rights
  matter. The app identity holds data rights only. Source: Microsoft Entra docs, "Managed identities best
  practices".
- Role assignments use data-plane roles at the narrowest scope: a topic, a container or a vault. A workload
  never gets `Contributor`. Source: Azure RBAC docs, "Best practices".
- No stored secrets:
  - storage `shared_access_key_enabled = false`;
  - registry `admin_enabled = false`;
  - Service Bus `local_auth_enabled = false`;
  - Key Vault in RBAC mode, never access policies;
  - Postgres with Entra auth only (`password_auth_enabled = false`), with the migrator identity as its Entra
    administrator.
  - Source: Azure security baselines.
- A `time_sleep` follows an identity or role assignment that a later resource uses at once. Why: Entra
  propagation makes the first use fail. Source: azurerm provider docs.
- Deploys authenticate with GitHub OIDC through a federated credential on the deployment identity, never a
  client secret.
  - Subjects use the repository's format: the immutable `repo:<owner>@<owner-id>/<repo>@<repo-id>:...` form
    for repositories created after 2026-07-15.
  - Workflows set `permissions: contents: read` at the top (per `github-actions:security`), each job
    re-declaring its full list, and grant `id-token: write` only on the job that signs in.
  - Source: GitHub docs, "Configuring OpenID Connect in Azure"; GitHub changelog 2026-04-23.
- A `private_networking` variable, default `true`, controls data-service exposure.
  - When on: data services have public access off and use a private endpoint plus a `privatelink.*` DNS
    zone linked to the VNet, and the Container Apps environment sits in a delegated /23 subnet.
  - Layout: VNet `10.0.0.0/16`, `cae` `10.0.0.0/23`, `private-endpoints` `10.0.2.0/24`.
  - When off: the public variant for throwaway sandboxes.
  - Source: Azure Private Link docs.
