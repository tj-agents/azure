# azure

Azure infrastructure learning companion, standards and a Terraform scaffold.

Published as plugin `azure` from this repository's `azure-agents` marketplace.

## Skills

- Knowledge: `azure:direction`, `azure:knowledge`, `azure:learning`.
- Contracts, empty until a decision is recorded: `azure:build`, `azure:domain-design`, `azure:errors`, `azure:libraries`, `azure:structure`, `azure:style`, `azure:testing`.
- Utilities: `azure:scaffold`.

## Authoring and verification

```powershell
pwsh .agents/sync-generated.ps1
pwsh .agents/sync-generated.ps1 -Check
```

The layout, the vendored generator and CI come from [Template](https://github.com/tj-agents/template).
