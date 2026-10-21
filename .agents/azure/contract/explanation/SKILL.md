---
name: explanation
description: How Terraform is quoted when explaining Azure infrastructure code. Use when an explanation shows Terraform/HCL.
kind: contract
domain: azure
profile: core
applicability: Azure projects
requires: azure
provenance: house
---

# Explaining Azure infrastructure code

Owns how Terraform is quoted in an explanation.

Quote each file in its own top-level `hcl` block, never indented inside a list item, under a `path:line`
line. Copy the code verbatim and replace provider, variable and output blocks, tags and other arguments
the point does not need with `...`.

`infrastructure/instance/main.tf:12`

````hcl
resource "azurerm_container_app" "this" {
  name = local.app_name
  ...
  template {
    ...
  }
}
````
