---
name: build
description: Tommy's agreed Azure build — Terraform and provider pins, the lock file, the provider block and the fmt, init and validate gate. Read before writing or reviewing Azure that touches it; nothing applies until it is recorded here.
kind: contract
domain: azure
profile: core
applicability: Azure projects
requires: azure
provenance: house
---

# Azure build

Owns the toolchain pin, build configuration, the verification gate and CI.

## Agreed

- Pins:
  - `required_version = ">= 1.15, < 2.0"`;
  - `azurerm ~> 5.0`, plus `random` and `time` where used, each `~> MAJOR.MINOR`;
  - CI pins the exact Terraform CLI.
  - Source: Terraform docs, "Version constraints".
- `.terraform.lock.hcl` is committed. Why: every run resolves the same provider builds. Source: Terraform
  docs, "Dependency lock file".
- The provider block sets `features {}` and `resource_provider_registrations = "none"`. The subscription
  comes from `ARM_SUBSCRIPTION_ID`, never a committed value. Source: azurerm provider docs.
- The gate for every root is `terraform fmt -check -recursive`, then `init -backend=false`, then `validate`,
  run locally and in CI. Source: house.
- A new workload starts from `azure:scaffold`. Source: house.
