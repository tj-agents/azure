---
name: scaffold
description: Create a new Azure project in the File structure `azure:structure` records — two Terraform roots (foundation, instance), the optional components from `azure:security`, `azure:deployment` and `azure:build`, and the CI gate. Use when starting an Azure project; it writes nothing until the options validate.
kind: utility
domain: azure
profile: scaffold
applicability: new Azure projects
requires: azure
provenance: house
---

# Azure project scaffold

Creates a project in the File structure `azure:structure` records: `infra/foundation`, `infra/instance`,
`infra/env/dev.tfvars`, `infra/README.md` and `.github/workflows/infra.yml`. Its options, presets,
components and templates sit beside this file; `scripts/scaffold.py` renders them. It needs Python 3.9+
and nothing else; the gate below needs Terraform.

## Create a project

```sh
python -B <skill-directory>/scripts/scaffold.py new --into <existing-dir> [--preset foundation|web-api] [--config f.json] [--set k=v]... [--dry-run]
python -B <skill-directory>/scripts/scaffold.py new --name <n> --destination <existing-dir> [same options]
python -B <skill-directory>/scripts/scaffold.py options [--preset <p>] [--config f.json] [--set k=v]...
```

Options layer lowest-first: `options.json` defaults, the preset, `--config`, then `--set`; `--name` sets
`name` but loses to `--set name=...`. `options` prints each resolved option with the layer that set it.
Every value is validated — pattern, enum, `requires`, `conflicts`, and the one required option, `name` —
before anything is written; any failure there exits 2. `--into` refuses and lists every output that
already exists; `--name`/`--destination` refuses when `<destination>/<name>` exists. `--dry-run` prints
the plan and writes nothing.

Options: `name` (required), `host` (`none`, `container-apps`, `app-service`), `database` (`none`,
`postgres-flexible`), `messaging` (`none`, `service-bus`) with `topics` and `subscriptions` lists,
`key_vault`, `vnet`, `workflows`, `terraform_version`, `azurerm_version`. Presets: `foundation` (the
foundation only) and `web-api` (`host=container-apps`, `database=postgres-flexible`, `key_vault=true`,
`vnet=true`).

## Build and adapt

Components live under `components/<id>/`: `component.json` declares a `when` expression over the
resolved options, and `files/<repo-relative path>.in` holds its fragment of each output file, rendered
with `__PLACEHOLDER__` substitution and `@if`/`@only-if` flag lines. `templates/` holds the files every
project gets. Add a component by giving it an id, a `when`, and the fragments it contributes; shared
files (`variables.tf`, `locals.tf`, `outputs.tf`, `data.tf`) accumulate one fragment per selected
component, in `component.json`'s `order`.

## Validation and publication

Every generated root must pass `terraform fmt -check -recursive`, `terraform init -backend=false` and
`terraform validate`. `.agents/tests/test_scaffold.py` covers option precedence and validation, file
lists per preset, and — only with `AZURE_SCAFFOLD_TERRAFORM=1` set, using a local Terraform install —
the gate above against one generation per preset plus a full-option and an app-service case.
