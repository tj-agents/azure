---
name: scaffold
description: Provide the Azure infrastructure an app profile asks for, in the File structure `azure:structure` records — two Terraform roots (foundation, instance), one instance resource per workload kind, the optional components from `azure:security`, `azure:deployment` and `azure:build`, and the CI gate. Use when an app stack scaffolds Azure infrastructure from its own `infra/app.json`; it writes nothing until the profile and options validate.
kind: utility
domain: azure
profile: scaffold
applicability: new or existing Azure projects driven by an app profile
requires: azure
provenance: house
---

# Azure infrastructure, driven by an app profile

Read and follow the [canonical definition](../../.agents/azure/utility/scaffold/SKILL.md) in full.
This discovery entry is generated; edit the referenced `.agents/` definition.
