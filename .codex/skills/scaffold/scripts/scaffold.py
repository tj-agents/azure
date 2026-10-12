#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SKILL_DIR = Path(__file__).resolve().parent.parent
OPTIONS_PATH = SKILL_DIR / "options.json"
PRESETS_DIR = SKILL_DIR / "presets"
COMPONENTS_DIR = SKILL_DIR / "components"
TEMPLATES_DIR = SKILL_DIR / "templates"

PLACEHOLDER = re.compile(r"__[A-Z_]+__")
IF_LINE = re.compile(r"^@if\s+(!?)([a-zA-Z_][a-zA-Z0-9_]*)\s*$")
ONLY_IF_LINE = re.compile(r"^@only-if\s+(!?)([a-zA-Z_][a-zA-Z0-9_]*)\s*$")

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,11}$")
WORKLOAD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,15}$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROBE_PATH_PATTERN = re.compile(r"^/")
RUNTIME_NAMES = {"dotnet", "node", "python", "java"}
WORKLOAD_KINDS = {"http", "background", "functions"}

PROFILE_TOKENS = {
    "postgres.host",
    "postgres.database",
    "postgres.user",
    "servicebus.namespace",
    "keyvault.uri",
    "monitor.connection_string",
    "identity.client_id",
}

TOP_LEVEL_KEYS = {"schema_version", "name", "workloads", "database", "settings"}
WORKLOAD_KEYS = {"id", "kind", "port", "probes", "runtime", "build", "settings", "messaging"}

TEMPLATE_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z][A-Za-z0-9_.]*)\}|[{}]")


class ScaffoldError(Exception):
    pass


class OptionError(Exception):
    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


class TemplateError(Exception):
    pass


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ScaffoldError(f"missing {path}") from None
    except json.JSONDecodeError as error:
        raise ScaffoldError(f"{path}: invalid JSON ({error})") from None


def load_spec() -> dict:
    return load_json(OPTIONS_PATH)


def discover_components() -> dict[str, dict]:
    components: dict[str, dict] = {}
    for path in sorted(COMPONENTS_DIR.iterdir()):
        if not path.is_dir():
            continue
        spec = load_json(path / "component.json")
        components[path.name] = {
            "id": path.name,
            "order": spec["order"],
            "when": spec["when"],
            "files_dir": path / "files",
        }
    return components


def components_all_ids() -> list[str]:
    return [path.name for path in sorted(COMPONENTS_DIR.iterdir()) if path.is_dir()]


# ---------------------------------------------------------------------------
# Options (app-agnostic)
# ---------------------------------------------------------------------------


def parse_set_value(raw: str, option_type: str) -> object:
    if option_type == "bool":
        if raw not in ("true", "false"):
            raise ValueError(f"must be true or false, not {raw!r}")
        return raw == "true"
    if option_type == "list":
        return [item.strip() for item in raw.split(",") if item.strip() != ""] if raw else []
    return raw


def resolve(spec: dict, preset: str | None, config_path: Path | None, sets: list[str]):
    options = spec["options"]
    values: dict[str, object] = {}
    layers: dict[str, str] = {}
    errors: list[str] = []

    for key, option in options.items():
        if "default" in option:
            values[key] = option["default"]
            layers[key] = "default"

    def apply_layer(layer_name: str, data: dict) -> None:
        for key, raw in data.items():
            if key not in options:
                errors.append(f"{key}: unknown option")
                continue
            values[key] = raw
            layers[key] = layer_name

    if preset is not None:
        preset_path = PRESETS_DIR / f"{preset}.json"
        if not preset_path.is_file():
            errors.append(f"{preset}: unknown preset")
        else:
            apply_layer("preset", load_json(preset_path))

    if config_path is not None:
        apply_layer("config", load_json(config_path))

    for item in sets:
        if "=" not in item:
            errors.append(f"{item}: --set values must be key=value")
            continue
        key, _, raw = item.partition("=")
        if key not in options:
            errors.append(f"{key}: unknown option")
            continue
        try:
            values[key] = parse_set_value(raw, options[key]["type"])
        except ValueError as error:
            errors.append(f"{key}: {error}")
            continue
        layers[key] = "set"

    if errors:
        raise OptionError(errors)

    return values, layers


def validate(spec: dict, values: dict[str, object]) -> list[str]:
    options = spec["options"]
    errors: list[str] = []

    for key, option in options.items():
        value = values.get(key)
        if value is None:
            if option.get("required"):
                errors.append(f"{key}: required")
            continue

        option_type = option["type"]
        if option_type == "bool":
            if not isinstance(value, bool):
                errors.append(f"{key}: must be a boolean")
        elif option_type == "enum":
            if value not in option["values"]:
                errors.append(f"{key}: must be one of {', '.join(option['values'])}")
        elif option_type == "list":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                errors.append(f"{key}: must be a list of strings")
            else:
                pattern = option.get("pattern")
                if pattern is not None:
                    for item in value:
                        if re.fullmatch(pattern, item) is None:
                            errors.append(f"{key}: {item!r} does not match {pattern}")
        else:
            if not isinstance(value, str):
                errors.append(f"{key}: must be a string")
            else:
                pattern = option.get("pattern")
                if pattern is not None and re.fullmatch(pattern, value) is None:
                    errors.append(f"{key}: {value!r} does not match {pattern}")

        requires = option.get("requires")
        if requires:
            triggered = bool(value) if option_type == "list" else value != option.get("default")
            if triggered:
                for req_key, req_value in requires.items():
                    if values.get(req_key) != req_value:
                        errors.append(f"{key}: requires {req_key}={req_value}")

        conflicts = option.get("conflicts")
        if conflicts:
            triggered = bool(value) if option_type == "list" else value != option.get("default")
            if triggered:
                for conf_key, conf_value in conflicts.items():
                    if values.get(conf_key) == conf_value:
                        errors.append(f"{key}: conflicts with {conf_key}={conf_value}")

    return errors


def prompt_for_missing(
    spec: dict, values: dict[str, object], layers: dict[str, str], input_func
) -> None:
    options = spec["options"]
    for key, option in options.items():
        if layers.get(key, "default") != "default":
            continue
        option_type = option["type"]
        default = option.get("default")
        while True:
            if option_type == "enum":
                prompt = f"{key} ({'/'.join(option['values'])}) [{default}]: "
            elif option_type == "bool":
                prompt = f"{key} (true/false) [{str(default).lower()}]: "
            elif option_type == "list":
                shown = ",".join(default or [])
                prompt = f"{key} (comma-separated) [{shown}]: "
            else:
                prompt = f"{key} [{default}]: "
            raw = input_func(prompt)
            if raw.strip() == "":
                values[key] = default
                layers[key] = "default"
                break
            try:
                candidate = parse_set_value(raw, option_type)
            except ValueError as error:
                print(f"{key}: {error}", file=sys.stderr)
                continue
            trial = dict(values)
            trial[key] = candidate
            errors = [error for error in validate_single(option, candidate)]
            if errors:
                for error in errors:
                    print(f"{key}: {error}", file=sys.stderr)
                continue
            values[key] = candidate
            layers[key] = "set"
            break


def validate_single(option: dict, value: object) -> list[str]:
    errors: list[str] = []
    option_type = option["type"]
    if option_type == "bool":
        if not isinstance(value, bool):
            errors.append("must be a boolean")
    elif option_type == "enum":
        if value not in option["values"]:
            errors.append(f"must be one of {', '.join(option['values'])}")
    elif option_type == "list":
        pattern = option.get("pattern")
        if pattern is not None:
            for item in value:
                if re.fullmatch(pattern, item) is None:
                    errors.append(f"{item!r} does not match {pattern}")
    else:
        pattern = option.get("pattern")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            errors.append(f"{value!r} does not match {pattern}")
    return errors


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def load_profile(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise OptionError([f"profile: missing {path}"]) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise OptionError([f"profile: invalid JSON ({error})"]) from None


def parse_template(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    literal = ""
    pos = 0
    for match in TEMPLATE_TOKEN.finditer(text):
        literal += text[pos : match.start()]
        pos = match.end()
        token = match.group(0)
        if token == "{{":
            literal += "{"
        elif token == "}}":
            literal += "}"
        elif match.group(1) is not None:
            if literal:
                parts.append(("literal", literal))
                literal = ""
            parts.append(("token", match.group(1)))
        else:
            raise TemplateError(f"malformed template near {token!r}")
    literal += text[pos:]
    if literal:
        parts.append(("literal", literal))
    return parts


def validate_settings(prefix: str, settings: object, values: dict, has_database: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(settings, dict):
        return [f"{prefix}: must be an object"]
    for key, value in settings.items():
        if ENV_NAME_PATTERN.fullmatch(key) is None:
            errors.append(f"{prefix}.{key}: not a valid environment variable name")
        if not isinstance(value, str):
            errors.append(f"{prefix}.{key}: must be a string")
            continue
        try:
            parts = parse_template(value)
        except TemplateError as error:
            errors.append(f"{prefix}.{key}: {error}")
            continue
        for kind, token in parts:
            if kind != "token":
                continue
            if token not in PROFILE_TOKENS:
                errors.append(f"{prefix}.{key}: unknown token {{{token}}}")
                continue
            if token.startswith("postgres.") and not has_database:
                errors.append(f"{prefix}.{key}: {{{token}}} requires a database")
            if token == "servicebus.namespace" and values.get("messaging") != "service-bus":
                errors.append(f"{prefix}.{key}: {{servicebus.namespace}} requires messaging=service-bus")
            if token == "keyvault.uri" and not values.get("key_vault"):
                errors.append(f"{prefix}.{key}: {{keyvault.uri}} requires key_vault")
    return errors


def validate_workload_messaging(prefix: str, messaging: object, values: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(messaging, dict):
        return [f"{prefix}: must be an object"]
    for key in sorted(set(messaging) - {"publishes", "consumes"}):
        errors.append(f"{prefix}: unknown key {key!r}")
    topics_option = values.get("topics") or []
    for field in ("publishes", "consumes"):
        items = messaging.get(field, [])
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            errors.append(f"{prefix}.{field}: must be a list of strings")
            continue
        if items and values.get("messaging") != "service-bus":
            errors.append(f"{prefix}.{field}: requires messaging=service-bus")
        for topic in items:
            if topic not in topics_option:
                errors.append(f"{prefix}.{field}: {topic!r} is not declared in topics")
    return errors


def _valid_runtime(runtime: object, allowed_names: set[str] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    name = runtime.get("name")
    version = runtime.get("version")
    if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
        return False
    if allowed_names is not None and name not in allowed_names:
        return False
    return True


def workload_host(kind: str, values: dict) -> str:
    return {
        "http": values.get("http_host"),
        "background": values.get("background_host"),
        "functions": values.get("functions_host"),
    }[kind]


def validate_profile(profile: object, values: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile: must be a JSON object"]

    for key in sorted(set(profile) - TOP_LEVEL_KEYS):
        errors.append(f"profile: unknown key {key!r}")

    if profile.get("schema_version") != 1:
        errors.append("profile.schema_version: must be 1")

    name = profile.get("name")
    if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
        errors.append("profile.name: must match ^[a-z][a-z0-9]{1,11}$")

    workloads = profile.get("workloads", [])
    if not isinstance(workloads, list):
        errors.append("profile.workloads: must be a list")
        workloads = []

    database = profile.get("database")
    has_database = database is not None

    seen_ids: set[str] = set()
    for index, workload in enumerate(workloads):
        prefix = f"profile.workloads[{index}]"
        if not isinstance(workload, dict):
            errors.append(f"{prefix}: must be an object")
            continue

        for key in sorted(set(workload) - WORKLOAD_KEYS):
            errors.append(f"{prefix}: unknown key {key!r}")

        workload_id = workload.get("id")
        if not isinstance(workload_id, str) or WORKLOAD_ID_PATTERN.fullmatch(workload_id) is None:
            errors.append(f"{prefix}.id: must match ^[a-z][a-z0-9-]{{0,15}}$")
            workload_id = None
        elif workload_id in seen_ids:
            errors.append(f"{prefix}.id: duplicate id {workload_id!r}")
        else:
            seen_ids.add(workload_id)

        kind = workload.get("kind")
        if kind not in WORKLOAD_KINDS:
            errors.append(f"{prefix}.kind: must be one of {', '.join(sorted(WORKLOAD_KINDS))}")
            continue

        host = workload_host(kind, values)

        if kind == "http":
            port = workload.get("port")
            if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
                errors.append(f"{prefix}.port: must be an integer 1-65535")
            probes = workload.get("probes")
            if not isinstance(probes, dict):
                errors.append(f"{prefix}.probes: required")
            else:
                for probe_name in ("liveness", "readiness"):
                    path = probes.get(probe_name)
                    if not isinstance(path, str) or PROBE_PATH_PATTERN.match(path) is None:
                        errors.append(f"{prefix}.probes.{probe_name}: must be a path starting with /")
            if host == "app-service" and not _valid_runtime(workload.get("runtime"), RUNTIME_NAMES):
                errors.append(
                    f"{prefix}.runtime: required on app-service, name must be one of "
                    + ", ".join(sorted(RUNTIME_NAMES))
                )

        if kind == "functions" and not _valid_runtime(workload.get("runtime"), None):
            errors.append(f"{prefix}.runtime: requires name and version")

        build = workload.get("build")
        needs_image = host == "container-apps"
        needs_package = host in ("app-service", "flex-consumption")
        if not isinstance(build, dict):
            errors.append(f"{prefix}.build: required")
        else:
            for key in sorted(set(build) - {"image", "package"}):
                errors.append(f"{prefix}.build: unknown key {key!r}")
            if needs_image and not isinstance(build.get("image"), str):
                errors.append(f"{prefix}.build.image: required for a Container Apps host")
            if needs_package and not isinstance(build.get("package"), str):
                errors.append(f"{prefix}.build.package: required for this host")

        workload_settings = workload.get("settings")
        if workload_settings is not None:
            errors += validate_settings(f"{prefix}.settings", workload_settings, values, has_database)

        workload_messaging = workload.get("messaging")
        if workload_messaging is not None:
            errors += validate_workload_messaging(f"{prefix}.messaging", workload_messaging, values)

    if has_database:
        if not isinstance(database, dict):
            errors.append("profile.database: must be an object")
        else:
            for key in sorted(set(database) - {"engine", "migration"}):
                errors.append(f"profile.database: unknown key {key!r}")
            if database.get("engine") != "postgres":
                errors.append("profile.database.engine: must be postgres")
            migration = database.get("migration")
            if migration is not None:
                if not isinstance(migration, dict):
                    errors.append("profile.database.migration: must be an object")
                else:
                    for key in sorted(set(migration) - {"build", "command"}):
                        errors.append(f"profile.database.migration: unknown key {key!r}")
                    build = migration.get("build")
                    if not isinstance(build, dict) or not isinstance(build.get("image"), str):
                        errors.append("profile.database.migration.build.image: required")
                    command = migration.get("command")
                    if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
                        errors.append("profile.database.migration.command: must be a list of strings")

    settings = profile.get("settings", {})
    if settings:
        errors += validate_settings("profile.settings", settings, values, has_database)
    elif not isinstance(settings, dict):
        errors.append("profile.settings: must be an object")

    return errors


def compute_derived(profile: dict, values: dict) -> dict[str, bool]:
    workloads = [w for w in profile.get("workloads") or [] if isinstance(w, dict)]
    database = profile.get("database")
    has_database = database is not None
    has_migration = has_database and isinstance(database, dict) and database.get("migration") is not None

    def kind_hosted(kind: str, host: str) -> bool:
        return any(w.get("kind") == kind and workload_host(kind, values) == host for w in workloads)

    has_functions = any(w.get("kind") == "functions" for w in workloads)
    has_background = any(w.get("kind") == "background" for w in workloads)
    http_container_apps = kind_hosted("http", "container-apps")
    http_app_service = kind_hosted("http", "app-service")
    functions_flex = has_functions and values.get("functions_host") == "flex-consumption"
    functions_container = has_functions and values.get("functions_host") == "container-apps"

    container_apps_used = http_container_apps or has_background or functions_container or has_migration

    return {
        "has_workloads": bool(workloads),
        "has_database": has_database,
        "has_migration": has_migration,
        "has_functions": has_functions,
        "has_background": has_background,
        "http_container_apps": http_container_apps,
        "http_app_service": http_app_service,
        "functions_flex": functions_flex,
        "functions_container": functions_container,
        "container_apps_used": container_apps_used,
    }


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def identity_reference(workload_id: str, attribute: str) -> str:
    if workload_id == "migrator":
        return f"azurerm_user_assigned_identity.migrator.{attribute}"
    return f'azurerm_user_assigned_identity.workload["{workload_id}"].{attribute}'


def token_expression(token: str, workload_id: str) -> str:
    if token == "postgres.host":
        return "data.azurerm_postgresql_flexible_server.main.fqdn"
    if token == "postgres.database":
        return "local.database_name"
    if token == "postgres.user":
        return identity_reference(workload_id, "name")
    if token == "servicebus.namespace":
        return 'format("%s.servicebus.windows.net", data.azurerm_servicebus_namespace.main.name)'
    if token == "keyvault.uri":
        return "data.azurerm_key_vault.main.vault_uri"
    if token == "monitor.connection_string":
        return "data.azurerm_application_insights.main.connection_string"
    if token == "identity.client_id":
        return identity_reference(workload_id, "client_id")
    raise ScaffoldError(f"unknown token {token}")


def escape_hcl_string(text: str) -> str:
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("$", "$$").replace("%", "%%")
    return text


def render_hcl_value(template: str, workload_id: str) -> str:
    parts = parse_template(template)
    if len(parts) == 1 and parts[0][0] == "token":
        return token_expression(parts[0][1], workload_id)
    out = []
    for kind, value in parts:
        if kind == "literal":
            out.append(escape_hcl_string(value))
        else:
            out.append("${" + token_expression(value, workload_id) + "}")
    return '"' + "".join(out) + '"'


def render_workload_settings(profile: dict, workload: dict) -> dict[str, str]:
    merged = dict(profile.get("settings") or {})
    merged.update(workload.get("settings") or {})
    return {key: render_hcl_value(template, workload["id"]) for key, template in merged.items()}


def render_migration_settings(profile: dict) -> dict[str, str]:
    settings = profile.get("settings") or {}
    return {key: render_hcl_value(template, "migrator") for key, template in settings.items()}


# ---------------------------------------------------------------------------
# HCL rendering helpers
# ---------------------------------------------------------------------------


def hcl_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"


def hcl_string(value: str) -> str:
    return '"' + escape_hcl_string(value) + '"'


def aligned_lines(pairs: list[tuple[str, str]], indent: str) -> list[str]:
    if not pairs:
        return []
    width = max(len(key) for key, _ in pairs)
    return [f"{indent}{key}{' ' * (width - len(key))} = {value}" for key, value in pairs]


WORKLOAD_ATTR_ORDER = ["kind", "host", "port", "probes", "runtime_name", "runtime_version"]


def render_workloads_tf(profile: dict, values: dict) -> str:
    workloads = [w for w in profile.get("workloads") or [] if isinstance(w, dict) and isinstance(w.get("id"), str)]

    lines = ["locals {", "  workloads = {"]
    for workload in workloads:
        workload_id = workload["id"]
        kind = workload.get("kind")
        host = workload_host(kind, values)
        if kind == "http":
            port = workload.get("port", 0)
            probes = workload.get("probes") or {}
            liveness = probes.get("liveness", "")
            readiness = probes.get("readiness", "")
            runtime = workload.get("runtime") or {}
            runtime_name = runtime.get("name", "") if host == "app-service" else ""
            runtime_version = runtime.get("version", "") if host == "app-service" else ""
        elif kind == "functions":
            port = 80
            liveness = ""
            readiness = ""
            runtime = workload.get("runtime") or {}
            runtime_name = runtime.get("name", "")
            runtime_version = runtime.get("version", "")
        else:
            port = 0
            liveness = ""
            readiness = ""
            runtime_name = ""
            runtime_version = ""

        messaging = workload.get("messaging") or {}
        publishes = messaging.get("publishes") or []
        consumes = messaging.get("consumes") or []

        pairs = [
            ("kind", hcl_string(kind)),
            ("host", hcl_string(host)),
            ("port", str(port)),
            ("probes", f"{{ liveness = {hcl_string(liveness)}, readiness = {hcl_string(readiness)} }}"),
            ("runtime_name", hcl_string(runtime_name)),
            ("runtime_version", hcl_string(runtime_version)),
            ("publishes", hcl_list(publishes)),
            ("consumes", hcl_list(consumes)),
        ]
        lines.append(f"    {workload_id} = {{")
        lines.extend(aligned_lines(pairs, "      "))
        lines.append("    }")
    lines.append("  }")
    lines.append("")
    lines.append(
        '  container_apps_workloads   = { for id, workload in local.workloads : id => workload '
        'if workload.host == "container-apps" }'
    )
    lines.append(
        '  app_service_workloads      = { for id, workload in local.workloads : id => workload '
        'if workload.host == "app-service" }'
    )
    lines.append(
        '  flex_consumption_workloads = { for id, workload in local.workloads : id => workload '
        'if workload.host == "flex-consumption" }'
    )
    lines.append(
        '  functions_workloads        = { for id, workload in local.workloads : id => workload '
        'if workload.kind == "functions" }'
    )
    lines.append("")
    lines.append("  settings = {")
    for workload in workloads:
        workload_id = workload["id"]
        settings = render_workload_settings(profile, workload)
        if not settings:
            lines.append(f"    {workload_id} = {{}}")
            continue
        lines.append(f"    {workload_id} = {{")
        pairs = [(hcl_string(key), value) for key, value in settings.items()]
        lines.extend(aligned_lines(pairs, "      "))
        lines.append("    }")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


MIGRATION_SCRIPT_HEADER = """#!/bin/sh
set -eu

TOKEN=$(wget -qO- --header="Metadata: true" --header="X-IDENTITY-HEADER: $IDENTITY_HEADER" \\
  "$IDENTITY_ENDPOINT?resource=https://ossrdbms-aad.database.windows.net&api-version=2019-08-01&client_id=$MIGRATOR_CLIENT_ID" \\
  | sed -n 's/.*"access_token":"\\([^"]*\\)".*/\\1/p')

export PGPASSWORD="$TOKEN"

psql "host=$PGHOST dbname=$PGDATABASE user=$PGUSER sslmode=require" <<'SQL'
@@GRANTS@@
SQL
"""

MIGRATION_GRANT_TEMPLATE = """DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@@IDENTITY@@') THEN
    PERFORM pgaadauth_create_principal('@@IDENTITY@@', false, false);
  END IF;
END
$$;

GRANT CONNECT ON DATABASE "@@DATABASE@@" TO "@@IDENTITY@@";
GRANT USAGE ON SCHEMA public TO "@@IDENTITY@@";
ALTER DEFAULT PRIVILEGES FOR ROLE "@@MIGRATOR@@" IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "@@IDENTITY@@";
ALTER DEFAULT PRIVILEGES FOR ROLE "@@MIGRATOR@@" IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "@@IDENTITY@@";
"""


def render_migration_script(profile: dict) -> str:
    workloads = [w for w in profile.get("workloads") or [] if isinstance(w, dict) and isinstance(w.get("id"), str)]
    grants = "\n".join(
        MIGRATION_GRANT_TEMPLATE.replace("@@IDENTITY@@", f"{profile['name']}-{w['id']}-id")
        .replace("@@DATABASE@@", "' || current_database() || '")
        .replace("@@MIGRATOR@@", f"{profile['name']}-migrator-id")
        for w in workloads
    )
    return MIGRATION_SCRIPT_HEADER.replace("@@GRANTS@@", grants)


def render_migration_tf(profile: dict) -> str:
    database = profile["database"]
    migration = database["migration"]
    command = hcl_list(migration.get("command") or [])
    settings = render_migration_settings(profile)
    script = render_migration_script(profile)

    lines = [
        'resource "azurerm_role_assignment" "migrator_acr_pull" {',
        "  scope                = data.azurerm_container_registry.main.id",
        '  role_definition_name = "AcrPull"',
        "  principal_id         = azurerm_user_assigned_identity.migrator.principal_id",
        "",
        "  depends_on = [time_sleep.migrator_propagation]",
        "}",
        "",
        'resource "azurerm_container_app_job" "migrate" {',
        '  name                         = "${local.instance_prefix}-migrate-caj"',
        "  resource_group_name          = data.azurerm_resource_group.main.name",
        "  location                     = data.azurerm_resource_group.main.location",
        "  container_app_environment_id = data.azurerm_container_app_environment.main.id",
        "",
        "  replica_timeout_in_seconds = 1800",
        "  replica_retry_limit        = 1",
        "",
        "  manual_trigger_config {",
        "    parallelism              = 1",
        "    replica_completion_count = 1",
        "  }",
        "",
        "  identity {",
        '    type         = "UserAssigned"',
        "    identity_ids = [azurerm_user_assigned_identity.migrator.id]",
        "  }",
        "",
        "  registry {",
        "    server   = data.azurerm_container_registry.main.login_server",
        "    identity = azurerm_user_assigned_identity.migrator.id",
        "  }",
        "",
        "  template {",
        "    container {",
        '      name    = "migrate"',
        '      image   = lookup(var.images, "migration", "mcr.microsoft.com/k8se/quickstart:latest")',
        f"      command = {command}",
        "      cpu     = 0.5",
        '      memory  = "1Gi"',
    ]
    for key, value in settings.items():
        lines.append("")
        lines.append("      env {")
        lines.append(f"        name  = {hcl_string(key)}")
        lines.append(f"        value = {value}")
        lines.append("      }")
    lines.extend(
        [
            "    }",
            "",
            "    init_container {",
            '      name   = "migrate-init"',
            '      image  = "postgres:17-alpine"',
            '      cpu    = 0.25',
            '      memory = "0.5Gi"',
            "",
            '      command = ["/bin/sh", "-c", <<-EOT',
        ]
    )
    lines.append(script.rstrip("\n"))
    lines.extend(
        [
            "      EOT",
            "      ]",
            "",
            "      env {",
            '        name  = "PGHOST"',
            "        value = data.azurerm_postgresql_flexible_server.main.fqdn",
            "      }",
            "      env {",
            '        name  = "PGDATABASE"',
            "        value = local.database_name",
            "      }",
            "      env {",
            '        name  = "PGUSER"',
            "        value = azurerm_user_assigned_identity.migrator.name",
            "      }",
            "      env {",
            '        name  = "MIGRATOR_CLIENT_ID"',
            "        value = azurerm_user_assigned_identity.migrator.client_id",
            "      }",
            "    }",
            "  }",
            "",
            "  tags = local.tags",
            "",
            "  depends_on = [time_sleep.migrator_propagation, azurerm_postgresql_flexible_server_database.main]",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


def render_workflow(profile: dict, values: dict, derived: dict) -> str:
    name = profile["name"]
    workloads = [w for w in profile.get("workloads") or [] if isinstance(w, dict) and isinstance(w.get("id"), str)]
    terraform_version = values["terraform_version"]

    header = f"""name: infra

on:
  pull_request:
    paths:
      - "infra/**"
  push:
    branches: [main]
    paths:
      - "infra/**"
  workflow_dispatch:

permissions: {{}}

concurrency:
  group: infra-${{{{ github.ref }}}}
  cancel-in-progress: false

jobs:
  validate:
    runs-on: ubuntu-latest
    permissions: {{}}
    strategy:
      matrix:
        root: [foundation, instance]
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "{terraform_version}"
          terraform_wrapper: false
      - run: terraform fmt -check -recursive
        working-directory: infra/${{{{ matrix.root }}}}
      - run: terraform init -backend=false
        working-directory: infra/${{{{ matrix.root }}}}
      - run: terraform validate
        working-directory: infra/${{{{ matrix.root }}}}

  plan:
    if: github.event_name == 'pull_request' && vars.AZURE_FOUNDATION_READY == 'true'
    needs: validate
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    strategy:
      matrix:
        root: [foundation, instance]
    env:
      ARM_USE_OIDC: "true"
      ARM_CLIENT_ID: ${{{{ vars.AZURE_CLIENT_ID }}}}
      ARM_TENANT_ID: ${{{{ vars.AZURE_TENANT_ID }}}}
      ARM_SUBSCRIPTION_ID: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "{terraform_version}"
          terraform_wrapper: false
      - uses: azure/login@v2
        with:
          client-id: ${{{{ vars.AZURE_CLIENT_ID }}}}
          tenant-id: ${{{{ vars.AZURE_TENANT_ID }}}}
          subscription-id: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
      - run: |
          terraform init \\
            -backend-config="resource_group_name=${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}" \\
            -backend-config="storage_account_name=${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}" \\
            -backend-config="container_name=tfstate" \\
            -backend-config="key={name}/${{{{ matrix.root }}}}.tfstate"
        working-directory: infra/${{{{ matrix.root }}}}
      - run: terraform plan -var-file=../env/dev.tfvars
        working-directory: infra/${{{{ matrix.root }}}}

  deploy:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main' && vars.AZURE_FOUNDATION_READY == 'true'
    needs: validate
    runs-on: ubuntu-latest
    environment: dev
    permissions:
      id-token: write
      contents: read
    env:
      ARM_USE_OIDC: "true"
      ARM_CLIENT_ID: ${{{{ vars.AZURE_CLIENT_ID }}}}
      ARM_TENANT_ID: ${{{{ vars.AZURE_TENANT_ID }}}}
      ARM_SUBSCRIPTION_ID: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "{terraform_version}"
          terraform_wrapper: false
      - uses: azure/login@v2
        with:
          client-id: ${{{{ vars.AZURE_CLIENT_ID }}}}
          tenant-id: ${{{{ vars.AZURE_TENANT_ID }}}}
          subscription-id: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}

      - name: Foundation apply
        working-directory: infra/foundation
        run: |
          terraform init \\
            -backend-config="resource_group_name=${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}" \\
            -backend-config="storage_account_name=${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}" \\
            -backend-config="container_name=tfstate" \\
            -backend-config="key={name}/foundation.tfstate"
          terraform plan -var-file=../env/dev.tfvars -out=tfplan
          terraform apply tfplan
"""

    steps: list[str] = [header]

    outputs_step = [
        "",
        "      - id: foundation-outputs",
        "        working-directory: infra/foundation",
        "        run: |",
        '          echo "resource_group_name=$(terraform output -raw resource_group_name)" >> "$GITHUB_OUTPUT"',
    ]
    if derived["container_apps_used"]:
        outputs_step.append(
            '          echo "registry_login_server=$(terraform output -raw registry_login_server)" >> "$GITHUB_OUTPUT"'
        )
    steps.append("\n".join(outputs_step) + "\n")

    if derived["container_apps_used"]:
        steps.append(
            """
      - name: ACR login
        run: az acr login --name "${{ steps.foundation-outputs.outputs.registry_login_server }}"
"""
        )

    steps.append(
        """
      - name: Prepare image manifest
        run: mkdir -p "$RUNNER_TEMP/images"
"""
    )

    for workload in workloads:
        workload_id = workload["id"]
        host = workload_host(workload.get("kind"), values)
        build = workload.get("build") or {}
        if host == "container-apps":
            command = build.get("image", "")
            steps.append(
                f"""
      - name: Build {workload_id}
        env:
          REGISTRY: ${{{{ steps.foundation-outputs.outputs.registry_login_server }}}}
          REPOSITORY: {name}-{workload_id}
          TAG: ${{{{ github.sha }}}}
        run: |
          export IMAGE="$REGISTRY/$REPOSITORY:$TAG"
          {command}
          printf '{{"{workload_id}": "%s"}}' "$IMAGE" > "$RUNNER_TEMP/images/{workload_id}.json"
"""
            )
        else:
            command = build.get("package", "")
            steps.append(
                f"""
      - name: Build {workload_id}
        run: |
          export PACKAGE="$RUNNER_TEMP/{workload_id}.zip"
          {command}
"""
            )

    if derived["has_migration"]:
        migration = profile["database"]["migration"]
        command = migration.get("build", {}).get("image", "")
        steps.append(
            f"""
      - name: Build migration
        env:
          REGISTRY: ${{{{ steps.foundation-outputs.outputs.registry_login_server }}}}
          REPOSITORY: {name}-migration
          TAG: ${{{{ github.sha }}}}
        run: |
          export IMAGE="$REGISTRY/$REPOSITORY:$TAG"
          {command}
          printf '{{"migration": "%s"}}' "$IMAGE" > "$RUNNER_TEMP/images/migration.json"
"""
        )

    steps.append(
        """
      - name: Collect images
        run: |
          python3 - <<'PY'
          import json, os, pathlib
          root = pathlib.Path(os.environ["RUNNER_TEMP"]) / "images"
          images = {}
          for item in sorted(root.glob("*.json")):
              images.update(json.loads(item.read_text()))
          out = pathlib.Path(os.environ["RUNNER_TEMP"]) / "images.auto.tfvars.json"
          out.write_text(json.dumps({"images": images}))
          PY
"""
    )

    if derived["has_migration"]:
        steps.append(
            f"""
      - name: Instance init
        working-directory: infra/instance
        run: |
          terraform init \\
            -backend-config="resource_group_name=${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}" \\
            -backend-config="storage_account_name=${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}" \\
            -backend-config="container_name=tfstate" \\
            -backend-config="key={name}/instance.tfstate"

      - name: Run migration
        working-directory: infra/instance
        run: |
          terraform apply -auto-approve \\
            -var-file=../env/dev.tfvars \\
            -var-file="$RUNNER_TEMP/images.auto.tfvars.json" \\
            -target=azurerm_user_assigned_identity.migrator \\
            -target=azurerm_postgresql_flexible_server_active_directory_administrator.migrator \\
            -target=azurerm_postgresql_flexible_server_database.main \\
            -target=azurerm_container_app_job.migrate
          az containerapp job start \\
            --name "{name}-migrate-caj" \\
            --resource-group "${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}"
          for attempt in $(seq 1 30); do
            status=$(az containerapp job execution list \\
              --name "{name}-migrate-caj" \\
              --resource-group "${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}" \\
              --query "[0].properties.status" -o tsv 2>/dev/null || echo "")
            if [ "$status" = "Succeeded" ]; then
              exit 0
            fi
            if [ "$status" = "Failed" ]; then
              echo "migration job failed" >&2
              exit 1
            fi
            sleep 10
          done
          echo "migration job timed out" >&2
          exit 1
"""
        )
    else:
        steps.append(
            f"""
      - name: Instance init
        working-directory: infra/instance
        run: |
          terraform init \\
            -backend-config="resource_group_name=${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}" \\
            -backend-config="storage_account_name=${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}" \\
            -backend-config="container_name=tfstate" \\
            -backend-config="key={name}/instance.tfstate"
"""
        )

    steps.append(
        """
      - name: Instance apply
        working-directory: infra/instance
        run: |
          terraform plan -var-file=../env/dev.tfvars -var-file="$RUNNER_TEMP/images.auto.tfvars.json" -out=tfplan
          terraform apply tfplan
"""
    )

    for workload in workloads:
        workload_id = workload["id"]
        host = workload_host(workload.get("kind"), values)
        if host == "flex-consumption":
            steps.append(
                f"""
      - name: Deploy {workload_id}
        run: |
          az functionapp deploy \\
            --name "{name}-{workload_id}-func" \\
            --resource-group "${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}" \\
            --src-path "$RUNNER_TEMP/{workload_id}.zip" \\
            --type zip
"""
            )
        elif host == "app-service":
            readiness = ((workload.get("probes") or {}).get("readiness")) or "/"
            steps.append(
                f"""
      - name: Deploy {workload_id}
        run: |
          az webapp deploy \\
            --name "{name}-{workload_id}-app" \\
            --resource-group "${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}" \\
            --slot staging \\
            --src-path "$RUNNER_TEMP/{workload_id}.zip" \\
            --type zip
          for attempt in $(seq 1 30); do
            code=$(az webapp deployment slot show --slot staging \\
              --name "{name}-{workload_id}-app" \\
              --resource-group "${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}" \\
              --query defaultHostName -o tsv 2>/dev/null | xargs -I{{}} curl -s -o /dev/null -w '%{{http_code}}' "https://{{}}{readiness}" || echo "")
            if [ "$code" = "200" ]; then
              break
            fi
            sleep 10
          done
          az webapp deployment slot swap \\
            --name "{name}-{workload_id}-app" \\
            --resource-group "${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}" \\
            --slot staging --target-slot production
"""
            )
        elif host == "container-apps":
            steps.append(
                f"""
      - name: Wait for {workload_id}
        run: |
          for attempt in $(seq 1 30); do
            ready=$(az containerapp revision list \\
              --name "{name}-{workload_id}-ca" \\
              --resource-group "${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}" \\
              --query "[?properties.active].properties.healthState" -o tsv 2>/dev/null || echo "")
            if echo "$ready" | grep -q Healthy; then
              break
            fi
            sleep 10
          done
"""
            )

    return "".join(steps).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Component selection and static rendering
# ---------------------------------------------------------------------------


def evaluate_when(expr: str, values: dict[str, object]) -> bool:
    if expr == "always":
        return True
    if "=" in expr:
        key, _, expected = expr.partition("=")
        return str(values.get(key)) == expected
    negate = expr.startswith("!")
    name = expr[1:] if negate else expr
    value = values.get(name)
    truthy = bool(value) and value != "none"
    return (not truthy) if negate else truthy


def select_components(components: dict[str, dict], values: dict[str, object]) -> list[dict]:
    selected = [c for c in components.values() if evaluate_when(c["when"], values)]
    return sorted(selected, key=lambda c: c["order"])


def build_flags(values: dict[str, object], selected: list[dict]) -> dict[str, bool]:
    selected_ids = {c["id"] for c in selected}
    flags = {component_id.replace("-", "_"): component_id in selected_ids for component_id in components_all_ids()}
    flags["workflows"] = bool(values.get("workflows"))
    flags["time_in_instance"] = bool(values.get("has_workloads")) or bool(values.get("has_database"))
    return flags


def build_placeholders(profile: dict, values: dict[str, object]) -> dict[str, str]:
    return {
        "__NAME__": str(profile["name"]),
        "__TERRAFORM_VERSION__": str(values["terraform_version"]),
        "__AZURERM_VERSION__": str(values["azurerm_version"]),
        "__TOPICS_HCL__": hcl_list(values.get("topics") or []),
    }


def render_fragment(text: str, flags: dict[str, bool], placeholders: dict[str, str], origin: Path) -> str | None:
    lines = text.splitlines()
    if lines:
        match = ONLY_IF_LINE.match(lines[0])
        if match:
            negate, name = match.groups()
            if name not in flags:
                raise ScaffoldError(f"{origin}: unknown flag {name}")
            condition = flags[name]
            if negate:
                condition = not condition
            if not condition:
                return None
            lines = lines[1:]

    stack: list[bool] = []
    out: list[str] = []
    for line in lines:
        match = IF_LINE.match(line)
        if match:
            negate, name = match.groups()
            if name not in flags:
                raise ScaffoldError(f"{origin}: unknown flag {name}")
            condition = flags[name]
            if negate:
                condition = not condition
            stack.append(condition)
            continue
        if line.strip() == "@endif":
            if not stack:
                raise ScaffoldError(f"{origin}: @endif without matching @if")
            stack.pop()
            continue
        if all(stack):
            for token in PLACEHOLDER.findall(line):
                if token not in placeholders:
                    raise ScaffoldError(f"{origin}: unknown placeholder {token}")
                line = line.replace(token, placeholders[token])
            out.append(line)

    if stack:
        raise ScaffoldError(f"{origin}: unterminated @if block")

    rendered = "\n".join(out).strip("\n")
    return rendered if rendered else None


def collect_sources(selected: list[dict]) -> dict[str, list[tuple[int, Path]]]:
    contributions: dict[str, list[tuple[int, Path]]] = {}

    def add(root: Path, order: int) -> None:
        if not root.is_dir():
            return
        for file in sorted(root.rglob("*.in")):
            relative = file.relative_to(root).as_posix()[: -len(".in")]
            contributions.setdefault(relative, []).append((order, file))

    add(TEMPLATES_DIR, -1)
    for component in selected:
        add(component["files_dir"], component["order"])

    return contributions


def render_all(selected: list[dict], flags: dict[str, bool], placeholders: dict[str, str]) -> dict[str, str]:
    contributions = collect_sources(selected)
    files: dict[str, str] = {}
    for relative, sources in contributions.items():
        parts = []
        for _, source in sorted(sources, key=lambda item: item[0]):
            text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
            rendered = render_fragment(text, flags, placeholders, source)
            if rendered is not None:
                parts.append(rendered)
        if not parts:
            continue
        content = "\n\n".join(parts) + "\n"
        while "\n\n\n" in content:
            content = content.replace("\n\n\n", "\n\n")
        files[relative] = content
    return files


# ---------------------------------------------------------------------------
# Plan / write
# ---------------------------------------------------------------------------


def plan(
    spec: dict,
    profile: dict,
    preset: str | None,
    config_path: Path | None,
    sets: list[str],
):
    values, layers = resolve(spec, preset, config_path, sets)
    errors = validate(spec, values)
    if errors:
        raise OptionError(errors)

    profile_errors = validate_profile(profile, values)
    if profile_errors:
        raise OptionError(profile_errors)

    derived = compute_derived(profile, values)
    when_values = {**values, **derived}

    components = discover_components()
    selected = select_components(components, when_values)
    flags = build_flags(when_values, selected)
    placeholders = build_placeholders(profile, values)
    files = render_all(selected, flags, placeholders)

    files["infra/instance/workloads.tf"] = render_workloads_tf(profile, values)
    if derived["has_migration"]:
        files["infra/instance/migration.tf"] = render_migration_tf(profile)
    if values.get("workflows"):
        files[".github/workflows/infra.yml"] = render_workflow(profile, values, derived)

    metadata = {
        "package": "azure",
        "skill": "scaffold",
        "version": spec["version"],
        "preset": preset,
        "name": profile["name"],
        "options": values,
    }
    return values, layers, files, metadata


def write_project(project: Path, files: dict[str, str], metadata: dict) -> None:
    outputs = dict(files)
    outputs[".scaffold.azure.json"] = json.dumps(metadata, indent=2) + "\n"
    created_dirs: list[Path] = []
    written: list[Path] = []
    path = project
    try:
        for relative, content in outputs.items():
            path = project / relative
            for directory in reversed(path.parents):
                if not directory.exists():
                    directory.mkdir()
                    created_dirs.append(directory)
            path.write_bytes(content.encode("utf-8"))
            written.append(path)
    except OSError as error:
        for done in reversed(written):
            done.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise ScaffoldError(f"could not write {path} ({error}); removed everything this run wrote") from error


def resolve_profile_path(into: Path, profile_arg: Path | None) -> Path:
    if profile_arg is not None:
        return profile_arg
    return into / "infra" / "app.json"


def cmd_new(args: argparse.Namespace, input_func=input) -> int:
    spec = load_spec()
    project = args.into
    if not project.is_dir():
        print(f"error: --into directory does not exist: {project}", file=sys.stderr)
        return 1

    try:
        values, layers = resolve(spec, args.preset, args.config, args.set or [])
    except OptionError as error:
        for message in error.messages:
            print(message, file=sys.stderr)
        return 2

    interactive = sys.stdin.isatty() and not args.no_input and not args.dry_run
    if interactive:
        prompt_for_missing(spec, values, layers, input_func)

    errors = validate(spec, values)
    if errors:
        for message in errors:
            print(message, file=sys.stderr)
        return 2

    profile_path = resolve_profile_path(project, args.profile)
    if not profile_path.is_file():
        print(f"error: missing profile {profile_path}", file=sys.stderr)
        return 2

    try:
        profile = load_profile(profile_path)
    except OptionError as error:
        for message in error.messages:
            print(message, file=sys.stderr)
        return 2

    try:
        _, _, files, metadata = plan(spec, profile, args.preset, args.config, args.set or [])
    except OptionError as error:
        for message in error.messages:
            print(message, file=sys.stderr)
        return 2
    except ScaffoldError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    default_profile_path = project / "infra" / "app.json"
    copied_profile_text = None
    if profile_path.resolve() != default_profile_path.resolve():
        copied_profile_text = profile_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        files = dict(files)
        files["infra/app.json"] = copied_profile_text

    profile_bytes = (copied_profile_text or profile_path.read_text(encoding="utf-8").replace("\r\n", "\n")).encode(
        "utf-8"
    )
    metadata = dict(metadata)
    metadata["profile"] = "infra/app.json"
    metadata["profile_sha256"] = hashlib.sha256(profile_bytes).hexdigest()

    conflicts: list[str] = []
    for relative in sorted(files):
        target = project / relative
        if not target.exists():
            continue
        if relative == "infra/app.json" and target.read_text(encoding="utf-8").replace("\r\n", "\n") == files[relative]:
            continue
        conflicts.append(relative)
    if (project / ".scaffold.azure.json").exists():
        conflicts.append(".scaffold.azure.json")
    if conflicts:
        for relative in conflicts:
            print(f"already exists: {project / relative}", file=sys.stderr)
        return 1

    if args.dry_run:
        for key in sorted(values):
            print(f"{key}={values[key]!r} [{layers.get(key, 'default')}]")
        for relative in sorted(files):
            print(project / relative)
        print(project / ".scaffold.azure.json")
        return 0

    try:
        write_project(project, files, metadata)
    except ScaffoldError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"created {project}")
    return 0


def schema_for_options(spec: dict) -> dict:
    schema = {}
    for key, option in spec["options"].items():
        entry = {
            "type": option["type"],
            "default": option.get("default"),
            "description": option.get("description"),
        }
        if "values" in option:
            entry["allowed"] = option["values"]
        schema[key] = entry
    return schema


def cmd_options(args: argparse.Namespace) -> int:
    spec = load_spec()
    if args.schema:
        print(json.dumps(schema_for_options(spec)))
        return 0
    try:
        values, layers = resolve(spec, args.preset, args.config, args.set or [])
    except OptionError as error:
        for message in error.messages:
            print(message, file=sys.stderr)
        return 2
    errors = validate(spec, values)
    if errors:
        for message in errors:
            print(message, file=sys.stderr)
        return 2
    for key in sorted(values):
        print(f"{key}={values[key]!r} [{layers.get(key, 'default')}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provide Azure infrastructure components for an app profile.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new = subparsers.add_parser("new")
    new.add_argument("--into", type=Path, required=True)
    new.add_argument("--profile", type=Path)
    new.add_argument("--preset")
    new.add_argument("--config", type=Path)
    new.add_argument("--set", action="append")
    new.add_argument("--dry-run", action="store_true")
    new.add_argument("--no-input", action="store_true")
    new.set_defaults(func=cmd_new)

    options = subparsers.add_parser("options")
    options.add_argument("--profile", type=Path)
    options.add_argument("--preset")
    options.add_argument("--config", type=Path)
    options.add_argument("--set", action="append")
    options.add_argument("--schema", action="store_true")
    options.set_defaults(func=cmd_options)

    return parser


def main(argv: list[str] | None = None, input_func=input) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "new":
        return cmd_new(args, input_func=input_func)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
