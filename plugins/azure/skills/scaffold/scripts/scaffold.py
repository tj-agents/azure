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

TOP_LEVEL_KEYS = {"schema_version", "name", "workloads", "database", "settings", "verify"}
WORKLOAD_KEYS = {"id", "kind", "port", "probes", "runtime", "build", "settings", "messaging"}
SCHEMA_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

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


CONSUME_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def validate_workload_messaging(prefix: str, messaging: object, values: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(messaging, dict):
        return [f"{prefix}: must be an object"]
    for key in sorted(set(messaging) - {"publishes", "consumes"}):
        errors.append(f"{prefix}: unknown key {key!r}")
    topics_option = values.get("topics") or []

    publishes = messaging.get("publishes", [])
    if not isinstance(publishes, list) or not all(isinstance(item, str) for item in publishes):
        errors.append(f"{prefix}.publishes: must be a list of strings")
    else:
        if publishes and values.get("messaging") != "service-bus":
            errors.append(f"{prefix}.publishes: requires messaging=service-bus")
        for topic in publishes:
            if topic not in topics_option:
                errors.append(f"{prefix}.publishes: {topic!r} is not declared in topics")

    consumes = messaging.get("consumes", [])
    if not isinstance(consumes, list):
        errors.append(f"{prefix}.consumes: must be a list of objects")
    else:
        if consumes and values.get("messaging") != "service-bus":
            errors.append(f"{prefix}.consumes: requires messaging=service-bus")
        for index, entry in enumerate(consumes):
            entry_prefix = f"{prefix}.consumes[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{entry_prefix}: must be an object")
                continue
            for key in sorted(set(entry) - {"topic", "types"}):
                errors.append(f"{entry_prefix}: unknown key {key!r}")
            topic = entry.get("topic")
            if not isinstance(topic, str):
                errors.append(f"{entry_prefix}.topic: must be a string")
            elif topic not in topics_option:
                errors.append(f"{entry_prefix}.topic: {topic!r} is not declared in topics")
            types = entry.get("types")
            if not isinstance(types, list) or not types or not all(isinstance(item, str) for item in types):
                errors.append(f"{entry_prefix}.types: must be a non-empty list of strings")
            else:
                seen_types: set[str] = set()
                for type_value in types:
                    if CONSUME_TYPE_PATTERN.fullmatch(type_value) is None:
                        errors.append(
                            f"{entry_prefix}.types: {type_value!r} does not match {CONSUME_TYPE_PATTERN.pattern!r}"
                        )
                    elif type_value in seen_types:
                        errors.append(f"{entry_prefix}.types: duplicate type {type_value!r}")
                    else:
                        seen_types.add(type_value)
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


def _has_actions_expression(text: str) -> bool:
    return "${{" in text or "}}" in text


def validate_verify(profile: dict) -> list[str]:
    errors: list[str] = []
    verify = profile.get("verify")
    if verify is None:
        return errors
    if not isinstance(verify, list) or not all(isinstance(item, str) for item in verify):
        errors.append("profile.verify: must be a list of strings")
        return errors
    for item in verify:
        if item == "":
            errors.append("profile.verify: must not contain an empty string")
        elif _has_actions_expression(item):
            errors.append(f"profile.verify: {item!r} must not contain '${{{{' or '}}}}'")
    return errors


def validate_schemas(database: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(database, dict):
        return errors
    schemas = database.get("schemas")
    if schemas is None:
        return errors
    if not isinstance(schemas, list) or not all(isinstance(item, str) for item in schemas):
        errors.append("profile.database.schemas: must be a list of strings")
        return errors
    seen: set[str] = set()
    for schema in schemas:
        if SCHEMA_NAME_PATTERN.fullmatch(schema) is None:
            errors.append(f"profile.database.schemas: {schema!r} does not match {SCHEMA_NAME_PATTERN.pattern!r}")
        elif schema in seen:
            errors.append(f"profile.database.schemas: duplicate schema {schema!r}")
        else:
            seen.add(schema)
    return errors


def resolved_schemas(database: dict) -> list[str]:
    return database.get("schemas") or ["public"]


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
            for key in ("image", "package"):
                value = build.get(key)
                if isinstance(value, str) and _has_actions_expression(value):
                    errors.append(f"{prefix}.build.{key}: must not contain '${{{{' or '}}}}'")

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
            for key in sorted(set(database) - {"engine", "migration", "schemas"}):
                errors.append(f"profile.database: unknown key {key!r}")
            if database.get("engine") != "postgres":
                errors.append("profile.database.engine: must be postgres")
            errors += validate_schemas(database)
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
                    elif _has_actions_expression(build["image"]):
                        errors.append("profile.database.migration.build.image: must not contain '${{' or '}}'")
                    command = migration.get("command")
                    if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
                        errors.append("profile.database.migration.command: must be a list of strings")
                    else:
                        for item in command:
                            if _has_actions_expression(item):
                                errors.append(
                                    f"profile.database.migration.command: {item!r} must not contain '${{{{' or '}}}}'"
                                )

    settings = profile.get("settings", {})
    if settings:
        errors += validate_settings("profile.settings", settings, values, has_database)
    elif not isinstance(settings, dict):
        errors.append("profile.settings: must be an object")

    errors += validate_verify(profile)

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


def workload_uses_token(profile: dict, workload: dict, token: str) -> bool:
    merged = dict(profile.get("settings") or {})
    merged.update(workload.get("settings") or {})
    for template in merged.values():
        for kind, value in parse_template(template):
            if kind == "token" and value == token:
                return True
    return False


def render_migration_settings(profile: dict) -> dict[str, str]:
    settings = profile.get("settings") or {}
    return {key: render_hcl_value(template, "migrator") for key, template in settings.items()}


# ---------------------------------------------------------------------------
# HCL rendering helpers
# ---------------------------------------------------------------------------


def hcl_list(items: list[str]) -> str:
    return "[" + ", ".join(hcl_string(item) for item in items) + "]"


def hcl_consume_list(entries: list[dict]) -> str:
    rendered = [
        f'{{ topic = {hcl_string(entry["topic"])}, types = {hcl_list(entry["types"])} }}' for entry in entries
    ]
    return "[" + ", ".join(rendered) + "]"


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
        uses_key_vault = workload_uses_token(profile, workload, "keyvault.uri")

        pairs = [
            ("kind", hcl_string(kind)),
            ("host", hcl_string(host)),
            ("port", str(port)),
            ("probes", f"{{ liveness = {hcl_string(liveness)}, readiness = {hcl_string(readiness)} }}"),
            ("runtime_name", hcl_string(runtime_name)),
            ("runtime_version", hcl_string(runtime_version)),
            ("publishes", hcl_list(publishes)),
            ("consumes", hcl_consume_list(consumes)),
            ("uses_key_vault", "true" if uses_key_vault else "false"),
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
    lines.append(
        '  key_vault_workloads        = { for id, workload in local.workloads : id => workload '
        "if workload.uses_key_vault }"
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

MIGRATION_CREATE_SCHEMA_TEMPLATE = (
    'CREATE SCHEMA IF NOT EXISTS "@@SCHEMA@@" AUTHORIZATION "@@MIGRATOR@@";\n'
)

MIGRATION_PRINCIPAL_TEMPLATE = """DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@@IDENTITY@@') THEN
    PERFORM pgaadauth_create_principal('@@IDENTITY@@', false, false);
  END IF;
END
$$;

GRANT CONNECT ON DATABASE "@@DATABASE@@" TO "@@IDENTITY@@";
"""

MIGRATION_SCHEMA_GRANT_TEMPLATE = """GRANT USAGE ON SCHEMA "@@SCHEMA@@" TO "@@IDENTITY@@";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "@@SCHEMA@@" TO "@@IDENTITY@@";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "@@SCHEMA@@" TO "@@IDENTITY@@";
ALTER DEFAULT PRIVILEGES FOR ROLE "@@MIGRATOR@@" IN SCHEMA "@@SCHEMA@@" GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "@@IDENTITY@@";
ALTER DEFAULT PRIVILEGES FOR ROLE "@@MIGRATOR@@" IN SCHEMA "@@SCHEMA@@" GRANT USAGE, SELECT ON SEQUENCES TO "@@IDENTITY@@";
"""


def render_migration_script(profile: dict) -> str:
    workloads = [w for w in profile.get("workloads") or [] if isinstance(w, dict) and isinstance(w.get("id"), str)]
    migrator_name = "${" + identity_reference("migrator", "name") + "}"
    database_name = "${local.database_name}"
    schemas = resolved_schemas(profile["database"])

    statements: list[str] = []
    for schema in schemas:
        if schema == "public":
            continue
        statements.append(
            MIGRATION_CREATE_SCHEMA_TEMPLATE.replace("@@SCHEMA@@", schema).replace("@@MIGRATOR@@", migrator_name)
        )
    for workload in workloads:
        identity = "${" + identity_reference(workload["id"], "name") + "}"
        statements.append(
            MIGRATION_PRINCIPAL_TEMPLATE.replace("@@IDENTITY@@", identity).replace("@@DATABASE@@", database_name)
        )
        for schema in schemas:
            statements.append(
                MIGRATION_SCHEMA_GRANT_TEMPLATE.replace("@@SCHEMA@@", schema)
                .replace("@@IDENTITY@@", identity)
                .replace("@@MIGRATOR@@", migrator_name)
            )
    grants = "\n".join(statements)
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
            "  lifecycle {",
            "    precondition {",
            '      condition     = length("${local.instance_prefix}-migrate-caj") <= 32',
            '      error_message = "Container App Job name must be 32 characters or fewer."',
            "    }",
            "  }",
            "",
            "  depends_on = [time_sleep.migrator_propagation, azurerm_postgresql_flexible_server_database.main]",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


CHECKOUT_ACTION = "actions/checkout"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
CHECKOUT_TAG = "v7.0.1"
SETUP_TERRAFORM_ACTION = "hashicorp/setup-terraform"
SETUP_TERRAFORM_SHA = "dfe3c3f87815947d99a8997f908cb6525fc44e9e"
SETUP_TERRAFORM_TAG = "v4.0.1"
AZURE_LOGIN_ACTION = "azure/login"
AZURE_LOGIN_SHA = "a641126d1b8aa4d1fa005f4f92df94a3a4c4c906"
AZURE_LOGIN_TAG = "v3.1.0"
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact"
UPLOAD_ARTIFACT_SHA = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"
UPLOAD_ARTIFACT_TAG = "v7.0.2"
GITHUB_SCRIPT_ACTION = "actions/github-script"
GITHUB_SCRIPT_SHA = "3a2844b7e9c422d3c10d287c895573f7108da1b3"
GITHUB_SCRIPT_TAG = "v9.0.0"

CHECKOUT_REF = f"{CHECKOUT_ACTION}@{CHECKOUT_SHA}  # {CHECKOUT_TAG}"
SETUP_TERRAFORM_REF = f"{SETUP_TERRAFORM_ACTION}@{SETUP_TERRAFORM_SHA}  # {SETUP_TERRAFORM_TAG}"
AZURE_LOGIN_REF = f"{AZURE_LOGIN_ACTION}@{AZURE_LOGIN_SHA}  # {AZURE_LOGIN_TAG}"
UPLOAD_ARTIFACT_REF = f"{UPLOAD_ARTIFACT_ACTION}@{UPLOAD_ARTIFACT_SHA}  # {UPLOAD_ARTIFACT_TAG}"
GITHUB_SCRIPT_REF = f"{GITHUB_SCRIPT_ACTION}@{GITHUB_SCRIPT_SHA}  # {GITHUB_SCRIPT_TAG}"


def yaml_dq(text: str) -> str:
    return json.dumps(text)


def yaml_flow_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def render_checkout_step(indent: str = "      ") -> str:
    return (
        f"{indent}- name: Check out the repository\n"
        f"{indent}  uses: {CHECKOUT_REF}\n"
        f"{indent}  with:\n"
        f"{indent}    persist-credentials: false\n"
    )


TERRAFORM_LOGIN_INIT_ACTION = f"""name: Terraform login and init
description: Sign in to Azure over OIDC and initialize Terraform for one root.

inputs:
  client-id:
    required: true
  tenant-id:
    required: true
  subscription-id:
    required: true
  terraform-version:
    required: true
  working-directory:
    required: true
  tfstate-resource-group:
    required: true
  tfstate-storage-account:
    required: true
  backend-key:
    required: true

runs:
  using: composite
  steps:
    - name: Set up Terraform
      uses: {SETUP_TERRAFORM_REF}
      with:
        terraform_version: ${{{{ inputs.terraform-version }}}}
        terraform_wrapper: false

    - name: Sign in to Azure
      uses: {AZURE_LOGIN_REF}
      with:
        client-id: ${{{{ inputs.client-id }}}}
        tenant-id: ${{{{ inputs.tenant-id }}}}
        subscription-id: ${{{{ inputs.subscription-id }}}}

    - name: Initialize Terraform
      shell: bash
      working-directory: ${{{{ inputs.working-directory }}}}
      env:
        TFSTATE_RESOURCE_GROUP: ${{{{ inputs.tfstate-resource-group }}}}
        TFSTATE_STORAGE_ACCOUNT: ${{{{ inputs.tfstate-storage-account }}}}
        BACKEND_KEY: ${{{{ inputs.backend-key }}}}
      run: |
        set -euo pipefail
        terraform init \\
          -backend-config="resource_group_name=$TFSTATE_RESOURCE_GROUP" \\
          -backend-config="storage_account_name=$TFSTATE_STORAGE_ACCOUNT" \\
          -backend-config="container_name=tfstate" \\
          -backend-config="key=$BACKEND_KEY"
"""

BUILD_WORKLOAD_ACTION = """name: Build workload
description: Build one workload's container image or deployment package from its profile build command.

inputs:
  workload-id:
    required: true
  host:
    required: true
  build-command:
    required: true
  registry:
    required: false
    default: ""
  repository:
    required: false
    default: ""
  tag:
    required: false
    default: ""

runs:
  using: composite
  steps:
    - name: Build a container image
      if: inputs.host == 'container-apps'
      shell: bash
      env:
        REGISTRY: ${{ inputs.registry }}
        REPOSITORY: ${{ inputs.repository }}
        TAG: ${{ inputs.tag }}
        BUILD_COMMAND: ${{ inputs.build-command }}
        WORKLOAD_ID: ${{ inputs.workload-id }}
      run: |
        set -euo pipefail
        mkdir -p "$RUNNER_TEMP/images"
        export IMAGE="$REGISTRY/$REPOSITORY:$TAG"
        bash -c "$BUILD_COMMAND"
        digest_ref=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE")
        printf '{"%s": "%s"}' "$WORKLOAD_ID" "$digest_ref" > "$RUNNER_TEMP/images/$WORKLOAD_ID.json"

    - name: Build a deployment package
      if: inputs.host != 'container-apps'
      shell: bash
      env:
        BUILD_COMMAND: ${{ inputs.build-command }}
        WORKLOAD_ID: ${{ inputs.workload-id }}
      run: |
        set -euo pipefail
        export PACKAGE="$RUNNER_TEMP/$WORKLOAD_ID.zip"
        bash -c "$BUILD_COMMAND"
"""


def render_dependabot() -> str:
    return """version: 2
updates:
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
    cooldown:
      default-days: 7
    groups:
      github-actions:
        patterns:
          - "*"
"""


def render_verify_job(profile: dict) -> str | None:
    commands = profile.get("verify")
    if not commands:
        return None
    lines = [
        "  verify:",
        "    name: Verify the app stack",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 15",
        "    permissions:",
        "      contents: read",
        "    steps:",
        render_checkout_step().rstrip("\n"),
    ]
    for command in commands:
        lines.append("")
        lines.append(f"      - name: {yaml_dq(command)}")
        lines.append("        env:")
        lines.append(f"          VERIFY_COMMAND: {yaml_dq(command)}")
        lines.append("        run: |")
        lines.append("          set -euo pipefail")
        lines.append('          bash -c "$VERIFY_COMMAND"')
    return "\n".join(lines) + "\n"


def render_validate_job(terraform_version: str, needs: list[str]) -> str:
    needs_line = f"    needs: {yaml_flow_list(needs)}\n" if needs else ""
    return f"""  validate:
    name: Validate Terraform
{needs_line}    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    strategy:
      matrix:
        root: [foundation, instance]
    steps:
{render_checkout_step()}
      - name: Set up Terraform
        uses: {SETUP_TERRAFORM_REF}
        with:
          terraform_version: "{terraform_version}"
          terraform_wrapper: false

      - name: Check formatting
        working-directory: infra/${{{{ matrix.root }}}}
        run: terraform fmt -check -recursive

      - name: Initialize without a backend
        working-directory: infra/${{{{ matrix.root }}}}
        run: terraform init -backend=false

      - name: Validate
        working-directory: infra/${{{{ matrix.root }}}}
        run: terraform validate
"""


def render_plan_job(name: str, terraform_version: str, needs: list[str]) -> str:
    return f"""  plan:
    name: Plan Terraform
    if: ${{{{ vars.AZURE_FOUNDATION_READY == 'true' && github.event.pull_request.head.repo.full_name == github.repository }}}}
    needs: {yaml_flow_list(needs)}
    runs-on: ubuntu-latest
    timeout-minutes: 15
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
{render_checkout_step()}
      - name: Terraform login and init
        uses: ./.github/actions/terraform-login-init
        with:
          client-id: ${{{{ vars.AZURE_CLIENT_ID }}}}
          tenant-id: ${{{{ vars.AZURE_TENANT_ID }}}}
          subscription-id: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
          terraform-version: "{terraform_version}"
          working-directory: infra/${{{{ matrix.root }}}}
          tfstate-resource-group: ${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}
          tfstate-storage-account: ${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}
          backend-key: {name}/${{{{ matrix.root }}}}.tfstate

      - name: Plan
        working-directory: infra/${{{{ matrix.root }}}}
        run: |
          set -euo pipefail
          terraform plan -no-color -var-file=../env/ci.tfvars | tee "$RUNNER_TEMP/${{{{ matrix.root }}}}-plan.txt"

      - name: Upload the plan
        uses: {UPLOAD_ARTIFACT_REF}
        with:
          name: terraform-plan-${{{{ matrix.root }}}}
          path: ${{{{ runner.temp }}}}/${{{{ matrix.root }}}}-plan.txt
          if-no-files-found: error
          retention-days: 7
"""


def render_compute_ephemeral_id_job(job_id: str, workflow_file: str) -> str:
    return f"""  {job_id}:
    name: Compute the preview instance id
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
    outputs:
      ephemeral-id: ${{{{ steps.ephemeral-id.outputs.ephemeral-id }}}}
    steps:
      - name: Compute the ephemeral id
        id: ephemeral-id
        env:
          PR_NUMBER: ${{{{ github.event.pull_request.number }}}}
        run: |
          set -euo pipefail
          digits=$(printf '%s' "$PR_NUMBER" | tr -cd '0-9')
          id="pr$digits"
          # ephemeral_id allows at most 6 lowercase alphanumeric characters; a truncated id can collide
          # between PRs, so a PR number that doesn't fit fails instead of deploying under the wrong state key.
          if [ "${{#id}}" -gt 6 ]; then
            echo "::error file={workflow_file}::ephemeral id \\"$id\\" exceeds the 6-character limit for a preview instance id" >&2
            exit 1
          fi
          echo "ephemeral-id=$id" >> "$GITHUB_OUTPUT"
"""


def render_ci_complete_job(needs: list[str], workflow_file: str) -> str:
    return f"""  ci-complete:
    name: CI complete
    if: always()
    needs: {yaml_flow_list(needs)}
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions: {{}}
    steps:
      - name: Check that every gate succeeded
        env:
          GATE_RESULTS: ${{{{ toJSON(needs) }}}}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import json
          import os
          import sys

          results = json.loads(os.environ["GATE_RESULTS"])
          failed = [name for name, info in results.items() if info["result"] not in ("success", "skipped")]
          if failed:
              print("::error file={workflow_file}::failed gates: " + ", ".join(sorted(failed)), file=sys.stderr)
              sys.exit(1)
          PY
"""


def render_build_steps(profile: dict, values: dict, derived: dict, name: str) -> list[str]:
    workloads = [w for w in profile.get("workloads") or [] if isinstance(w, dict) and isinstance(w.get("id"), str)]
    steps: list[str] = []
    for workload in workloads:
        workload_id = workload["id"]
        host = workload_host(workload.get("kind"), values)
        build = workload.get("build") or {}
        command = build.get("image", "") if host == "container-apps" else build.get("package", "")
        steps.append(
            f"""
      - name: Build {workload_id}
        uses: ./.github/actions/build-workload
        with:
          workload-id: {workload_id}
          host: {host}
          build-command: {json.dumps(command)}
          registry: ${{{{ steps.foundation-outputs.outputs.registry_login_server }}}}
          repository: {name}-{workload_id}
          tag: ${{{{ github.sha }}}}
"""
        )
    if derived["has_migration"]:
        migration = profile["database"]["migration"]
        command = migration.get("build", {}).get("image", "")
        steps.append(
            f"""
      - name: Build migration
        uses: ./.github/actions/build-workload
        with:
          workload-id: migration
          host: container-apps
          build-command: {json.dumps(command)}
          registry: ${{{{ steps.foundation-outputs.outputs.registry_login_server }}}}
          repository: {name}-migration
          tag: ${{{{ github.sha }}}}
"""
        )
    return steps


def render_deploy_steps(profile: dict, values: dict, name: str) -> list[str]:
    workloads = [w for w in profile.get("workloads") or [] if isinstance(w, dict) and isinstance(w.get("id"), str)]
    steps: list[str] = []
    for workload in workloads:
        workload_id = workload["id"]
        host = workload_host(workload.get("kind"), values)
        if host == "flex-consumption":
            steps.append(
                f"""
      - name: Deploy {workload_id}
        env:
          RESOURCE_GROUP: ${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}
        run: |
          set -euo pipefail
          az functionapp deploy \\
            --name "{name}-{workload_id}-func" \\
            --resource-group "$RESOURCE_GROUP" \\
            --src-path "$RUNNER_TEMP/{workload_id}.zip" \\
            --type zip
          for _ in $(seq 1 30); do
            state=$(az functionapp show \\
              --name "{name}-{workload_id}-func" \\
              --resource-group "$RESOURCE_GROUP" \\
              --query state -o tsv 2>/dev/null || echo "")
            if [ "$state" = "Running" ]; then
              break
            fi
            sleep 10
          done
"""
            )
        elif host == "app-service":
            readiness = ((workload.get("probes") or {}).get("readiness")) or "/"
            steps.append(
                f"""
      - name: Deploy {workload_id}
        env:
          RESOURCE_GROUP: ${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}
        run: |
          set -euo pipefail
          az webapp deploy \\
            --name "{name}-{workload_id}-app" \\
            --resource-group "$RESOURCE_GROUP" \\
            --slot staging \\
            --src-path "$RUNNER_TEMP/{workload_id}.zip" \\
            --type zip
          for _ in $(seq 1 30); do
            code=$(az webapp deployment slot show --slot staging \\
              --name "{name}-{workload_id}-app" \\
              --resource-group "$RESOURCE_GROUP" \\
              --query defaultHostName -o tsv 2>/dev/null | xargs -I{{}} curl -s -o /dev/null -w '%{{http_code}}' "https://{{}}{readiness}" || echo "")
            if [ "$code" = "200" ]; then
              break
            fi
            sleep 10
          done
          az webapp deployment slot swap \\
            --name "{name}-{workload_id}-app" \\
            --resource-group "$RESOURCE_GROUP" \\
            --slot staging --target-slot production
          for _ in $(seq 1 30); do
            code=$(az webapp show \\
              --name "{name}-{workload_id}-app" \\
              --resource-group "$RESOURCE_GROUP" \\
              --query defaultHostName -o tsv 2>/dev/null | xargs -I{{}} curl -s -o /dev/null -w '%{{http_code}}' "https://{{}}{readiness}" || echo "")
            if [ "$code" = "200" ]; then
              break
            fi
            sleep 10
          done
"""
            )
        elif host == "container-apps":
            steps.append(
                f"""
      - name: Wait for {workload_id}
        env:
          RESOURCE_GROUP: ${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}
        run: |
          set -euo pipefail
          for _ in $(seq 1 30); do
            ready=$(az containerapp revision list \\
              --name "{name}-{workload_id}-ca" \\
              --resource-group "$RESOURCE_GROUP" \\
              --query "[?properties.active].properties.healthState" -o tsv 2>/dev/null || echo "")
            if echo "$ready" | grep -q Healthy; then
              break
            fi
            sleep 10
          done
"""
            )
    return steps


def has_consume_entries(profile: dict) -> bool:
    for workload in profile.get("workloads") or []:
        if isinstance(workload, dict) and (workload.get("messaging") or {}).get("consumes"):
            return True
    return False


REMOVE_DEFAULT_RULE_STEP = """
      - name: Remove the default Service Bus subscription rule
        working-directory: infra/instance
        env:
          RESOURCE_GROUP: ${{ steps.foundation-outputs.outputs.resource_group_name }}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import json
          import os
          import subprocess
          import sys

          resource_group = os.environ["RESOURCE_GROUP"]
          raw = subprocess.run(
              ["terraform", "output", "-json", "service_bus_default_rule_subscriptions"],
              check=True,
              capture_output=True,
              text=True,
          ).stdout
          for entry in json.loads(raw):
              result = subprocess.run(
                  [
                      "az", "servicebus", "topic", "subscription", "rule", "delete",
                      "--resource-group", resource_group,
                      "--namespace-name", entry["namespace"],
                      "--topic-name", entry["topic"],
                      "--subscription-name", entry["subscription"],
                      "--name", "$Default",
                  ],
                  capture_output=True,
                  text=True,
              )
              if result.returncode != 0 and "not found" not in result.stderr.lower():
                  print("::error file=.github/workflows/deploy-environment.yml::" + result.stderr, file=sys.stderr)
                  sys.exit(1)
          PY
"""


def render_deploy_environment_workflow(profile: dict, values: dict, derived: dict) -> str:
    name = profile["name"]
    terraform_version = values["terraform_version"]

    header = f"""name: Deploy Environment

on:
  workflow_call:
    inputs:
      environment:
        required: true
        type: string
      tfvars-file:
        required: true
        type: string
      ephemeral-id:
        required: false
        type: string
        default: ""

permissions:
  contents: read

jobs:
  deploy:
    name: Deploy the environment
    runs-on: ubuntu-latest
    timeout-minutes: 45
    environment: ${{{{ inputs.environment }}}}
    concurrency:
      group: ${{{{ inputs.ephemeral-id != '' && format('preview-{{0}}', inputs.ephemeral-id) || inputs.environment }}}}
      cancel-in-progress: false
    permissions:
      id-token: write
      contents: read
    env:
      ARM_USE_OIDC: "true"
      ARM_CLIENT_ID: ${{{{ vars.AZURE_CLIENT_ID }}}}
      ARM_TENANT_ID: ${{{{ vars.AZURE_TENANT_ID }}}}
      ARM_SUBSCRIPTION_ID: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
    steps:
{render_checkout_step()}
      - name: Terraform login and init
        uses: ./.github/actions/terraform-login-init
        with:
          client-id: ${{{{ vars.AZURE_CLIENT_ID }}}}
          tenant-id: ${{{{ vars.AZURE_TENANT_ID }}}}
          subscription-id: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
          terraform-version: "{terraform_version}"
          working-directory: infra/foundation
          tfstate-resource-group: ${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}
          tfstate-storage-account: ${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}
          backend-key: {name}/foundation.tfstate

      - name: Plan and apply the foundation
        if: inputs.ephemeral-id == ''
        working-directory: infra/foundation
        env:
          TFVARS_FILE: ${{{{ inputs.tfvars-file }}}}
        run: |
          set -euo pipefail
          terraform plan -var-file="../env/$TFVARS_FILE" -out=tfplan
          terraform apply tfplan
"""

    steps: list[str] = [header]

    outputs_step = [
        "",
        "      - name: Read the foundation outputs",
        "        id: foundation-outputs",
        "        working-directory: infra/foundation",
        "        run: |",
        "          set -euo pipefail",
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
      - name: Sign in to the container registry
        env:
          REGISTRY: ${{ steps.foundation-outputs.outputs.registry_login_server }}
        run: |
          set -euo pipefail
          az acr login --name "$REGISTRY"
"""
        )

    steps.append(
        """
      - name: Prepare the image manifest directory
        run: mkdir -p "$RUNNER_TEMP/images"
"""
    )

    steps.extend(render_build_steps(profile, values, derived, name))

    steps.append(
        """
      - name: Collect the built images
        run: |
          set -euo pipefail
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

    steps.append(
        f"""
      - name: Terraform login and init (instance)
        uses: ./.github/actions/terraform-login-init
        with:
          client-id: ${{{{ vars.AZURE_CLIENT_ID }}}}
          tenant-id: ${{{{ vars.AZURE_TENANT_ID }}}}
          subscription-id: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
          terraform-version: "{terraform_version}"
          working-directory: infra/instance
          tfstate-resource-group: ${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}
          tfstate-storage-account: ${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}
          backend-key: ${{{{ inputs.ephemeral-id != '' && format('{name}/instance.{{0}}.tfstate', inputs.ephemeral-id) || '{name}/instance.tfstate' }}}}
"""
    )

    if derived["has_migration"]:
        steps.append(
            f"""
      - name: Run the migration
        working-directory: infra/instance
        env:
          TFVARS_FILE: ${{{{ inputs.tfvars-file }}}}
          EPHEMERAL_ID: ${{{{ inputs.ephemeral-id }}}}
          RESOURCE_GROUP: ${{{{ steps.foundation-outputs.outputs.resource_group_name }}}}
        run: |
          set -euo pipefail
          terraform apply -auto-approve \\
            -var-file="../env/$TFVARS_FILE" \\
            -var-file="$RUNNER_TEMP/images.auto.tfvars.json" \\
            -var "ephemeral_id=$EPHEMERAL_ID" \\
            -target=azurerm_user_assigned_identity.migrator \\
            -target=azurerm_postgresql_flexible_server_active_directory_administrator.migrator \\
            -target=azurerm_postgresql_flexible_server_database.main \\
            -target=azurerm_container_app_job.migrate
          az containerapp job start \\
            --name "{name}-migrate-caj" \\
            --resource-group "$RESOURCE_GROUP"
          for _ in $(seq 1 30); do
            status=$(az containerapp job execution list \\
              --name "{name}-migrate-caj" \\
              --resource-group "$RESOURCE_GROUP" \\
              --query "[0].properties.status" -o tsv 2>/dev/null || echo "")
            if [ "$status" = "Succeeded" ]; then
              exit 0
            fi
            if [ "$status" = "Failed" ]; then
              echo "::error file=.github/workflows/deploy-environment.yml::migration job failed" >&2
              exit 1
            fi
            sleep 10
          done
          echo "::error file=.github/workflows/deploy-environment.yml::migration job timed out" >&2
          exit 1
"""
        )

    steps.append(
        """
      - name: Apply the instance
        working-directory: infra/instance
        env:
          TFVARS_FILE: ${{ inputs.tfvars-file }}
          EPHEMERAL_ID: ${{ inputs.ephemeral-id }}
        run: |
          set -euo pipefail
          terraform plan \\
            -var-file="../env/$TFVARS_FILE" \\
            -var-file="$RUNNER_TEMP/images.auto.tfvars.json" \\
            -var "ephemeral_id=$EPHEMERAL_ID" \\
            -out=tfplan
          terraform apply tfplan
"""
    )

    if has_consume_entries(profile):
        steps.append(REMOVE_DEFAULT_RULE_STEP)

    steps.extend(render_deploy_steps(profile, values, name))

    return "".join(steps).rstrip("\n") + "\n"


def render_destroy_preview_workflow(values: dict, name: str) -> str:
    terraform_version = values["terraform_version"]
    return f"""name: Destroy Preview

on:
  workflow_call:
    inputs:
      ephemeral-id:
        required: true
        type: string
      tfvars-file:
        required: true
        type: string

permissions:
  contents: read

jobs:
  destroy:
    name: Destroy the preview instance
    runs-on: ubuntu-latest
    timeout-minutes: 20
    environment: preview
    concurrency:
      group: preview-${{{{ inputs.ephemeral-id }}}}
      cancel-in-progress: false
    permissions:
      id-token: write
      contents: read
    env:
      ARM_USE_OIDC: "true"
      ARM_CLIENT_ID: ${{{{ vars.AZURE_CLIENT_ID }}}}
      ARM_TENANT_ID: ${{{{ vars.AZURE_TENANT_ID }}}}
      ARM_SUBSCRIPTION_ID: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
    steps:
{render_checkout_step()}
      - name: Terraform login and init
        uses: ./.github/actions/terraform-login-init
        with:
          client-id: ${{{{ vars.AZURE_CLIENT_ID }}}}
          tenant-id: ${{{{ vars.AZURE_TENANT_ID }}}}
          subscription-id: ${{{{ vars.AZURE_SUBSCRIPTION_ID }}}}
          terraform-version: "{terraform_version}"
          working-directory: infra/instance
          tfstate-resource-group: ${{{{ vars.TFSTATE_RESOURCE_GROUP }}}}
          tfstate-storage-account: ${{{{ vars.TFSTATE_STORAGE_ACCOUNT }}}}
          backend-key: {name}/instance.${{{{ inputs.ephemeral-id }}}}.tfstate

      - name: Destroy the preview instance
        working-directory: infra/instance
        env:
          TFVARS_FILE: ${{{{ inputs.tfvars-file }}}}
          EPHEMERAL_ID: ${{{{ inputs.ephemeral-id }}}}
        run: |
          set -euo pipefail
          terraform destroy -auto-approve \\
            -var-file="../env/$TFVARS_FILE" \\
            -var "ephemeral_id=$EPHEMERAL_ID"
"""


def render_pr_workflow(profile: dict, values: dict, derived: dict) -> str:
    name = profile["name"]
    terraform_version = values["terraform_version"]
    has_verify = bool(profile.get("verify"))

    header = """name: PR

on:
  pull_request:
    types: [opened, synchronize, reopened, labeled]

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
"""

    parts = [header]
    gates: list[str] = []

    if has_verify:
        parts.append(render_verify_job(profile))
        gates.append("verify")

    parts.append(render_validate_job(terraform_version, ["verify"] if has_verify else []))
    gates.append("validate")

    parts.append(render_plan_job(name, terraform_version, ["validate"]))
    gates.append("plan")

    parts.append(render_compute_ephemeral_id_job("compute-preview-id", ".github/workflows/pr.yml"))
    parts.append(
        """  deploy-preview:
    name: Deploy the preview instance
    if: ${{ !cancelled() && needs.compute-preview-id.result == 'success' && needs.plan.result == 'success' && vars.AZURE_FOUNDATION_READY == 'true' && contains(github.event.pull_request.labels.*.name, 'deploy-preview') && github.event.pull_request.head.repo.full_name == github.repository }}
    needs: [compute-preview-id, plan]
    permissions:
      id-token: write
      contents: read
    uses: ./.github/workflows/deploy-environment.yml
    with:
      environment: preview
      tfvars-file: ci.tfvars
      ephemeral-id: ${{ needs.compute-preview-id.outputs.ephemeral-id }}
"""
    )
    gates.append("compute-preview-id")
    gates.append("deploy-preview")

    parts.append(
        """  break-preview-lock:
    name: Break a leaked preview state lease
    if: ${{ cancelled() && github.event.pull_request.head.repo.full_name == github.repository }}
    needs: [compute-preview-id, deploy-preview]
    runs-on: ubuntu-latest
    timeout-minutes: 5
    environment: preview
    concurrency:
      group: preview-${{ needs.compute-preview-id.outputs.ephemeral-id }}
      cancel-in-progress: false
    permissions:
      id-token: write
      contents: read
    steps:
      - name: Sign in to Azure
        uses: """
        + AZURE_LOGIN_REF
        + """
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}

      - name: Break the leaked state lease
        env:
          STORAGE_ACCOUNT: ${{ vars.TFSTATE_STORAGE_ACCOUNT }}
          BLOB_NAME: """
        + f"{name}/instance.${{{{ needs.compute-preview-id.outputs.ephemeral-id }}}}.tfstate"
        + """
        run: |
          set -euo pipefail
          az storage blob lease break \\
            --account-name "$STORAGE_ACCOUNT" \\
            --container-name tfstate \\
            --name "$BLOB_NAME" \\
            --auth-mode login
"""
    )
    gates.append("break-preview-lock")

    parts.append(render_ci_complete_job(gates, ".github/workflows/pr.yml"))

    return "\n".join(part.rstrip("\n") for part in parts) + "\n"


def render_ci_workflow(profile: dict, values: dict, derived: dict) -> str:
    name = profile["name"]
    terraform_version = values["terraform_version"]
    has_verify = bool(profile.get("verify"))

    header = """name: CI

on:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ci
  cancel-in-progress: false

jobs:
"""

    parts = [header]
    gates: list[str] = []

    if has_verify:
        parts.append(render_verify_job(profile))
        gates.append("verify")

    parts.append(render_validate_job(terraform_version, ["verify"] if has_verify else []))
    gates.append("validate")

    parts.append(
        """  deploy-ci:
    name: Deploy the ci environment
    if: ${{ !cancelled() && needs.validate.result == 'success' && vars.AZURE_FOUNDATION_READY == 'true' }}
    needs: [validate]
    permissions:
      id-token: write
      contents: read
    uses: ./.github/workflows/deploy-environment.yml
    with:
      environment: ci
      tfvars-file: ci.tfvars
      ephemeral-id: ""
"""
    )
    gates.append("deploy-ci")

    parts.append(render_ci_complete_job(gates, ".github/workflows/ci.yml"))

    return "\n".join(part.rstrip("\n") for part in parts) + "\n"


def render_prod_workflow(values: dict) -> str:
    terraform_version = values["terraform_version"]
    return f"""name: Release (Production)

on:
  push:
    tags:
      - "v[0-9]+.[0-9]+.[0-9]+"

permissions:
  contents: read

concurrency:
  group: prod
  cancel-in-progress: false

jobs:
  assert-release-commit:
    name: Assert the release commit is the latest green CI run
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      actions: read
      contents: read
    steps:
      - name: Compare the tag's commit against ci.yml's latest success on main
        uses: {GITHUB_SCRIPT_REF}
        env:
          TAG_SHA: ${{{{ github.sha }}}}
        with:
          script: |
            const {{ data }} = await github.rest.actions.listWorkflowRuns({{
              owner: context.repo.owner,
              repo: context.repo.repo,
              workflow_id: "ci.yml",
              branch: "main",
              status: "success",
              per_page: 1,
            }});
            const latestSha = data.workflow_runs[0]?.head_sha;
            const tagSha = process.env.TAG_SHA;
            if (!latestSha) {{
              core.setFailed("no successful ci.yml run found on main");
              return;
            }}
            if (latestSha !== tagSha) {{
              core.setFailed(
                `release commit ${{tagSha}} is not the latest green ci.yml run on main (${{latestSha}})`
              );
            }}

{render_validate_job(terraform_version, [])}
  deploy-prod:
    name: Deploy the production environment
    if: ${{{{ !cancelled() && needs.assert-release-commit.result == 'success' && needs.validate.result == 'success' }}}}
    needs: [assert-release-commit, validate]
    permissions:
      id-token: write
      contents: read
    uses: ./.github/workflows/deploy-environment.yml
    with:
      environment: prod
      tfvars-file: prod.tfvars
      ephemeral-id: ""
"""


def render_pr_closed_workflow() -> str:
    return f"""name: PR Closed

on:
  pull_request:
    types: [closed]

permissions:
  contents: read

jobs:
{render_compute_ephemeral_id_job("compute-preview-id", ".github/workflows/pr-closed.yml")}
  destroy-preview:
    name: Destroy the preview instance
    if: ${{{{ !cancelled() && needs.compute-preview-id.result == 'success' && contains(github.event.pull_request.labels.*.name, 'deploy-preview') && github.event.pull_request.head.repo.full_name == github.repository }}}}
    needs: [compute-preview-id]
    permissions:
      id-token: write
      contents: read
    uses: ./.github/workflows/destroy-preview.yml
    with:
      ephemeral-id: ${{{{ needs.compute-preview-id.outputs.ephemeral-id }}}}
      tfvars-file: ci.tfvars
"""


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
        files[".github/workflows/pr.yml"] = render_pr_workflow(profile, values, derived)
        files[".github/workflows/ci.yml"] = render_ci_workflow(profile, values, derived)
        files[".github/workflows/prod.yml"] = render_prod_workflow(values)
        files[".github/workflows/pr-closed.yml"] = render_pr_closed_workflow()
        files[".github/workflows/deploy-environment.yml"] = render_deploy_environment_workflow(profile, values, derived)
        files[".github/workflows/destroy-preview.yml"] = render_destroy_preview_workflow(values, profile["name"])
        files[".github/actions/terraform-login-init/action.yml"] = TERRAFORM_LOGIN_INIT_ACTION
        files[".github/actions/build-workload/action.yml"] = BUILD_WORKLOAD_ACTION
        files[".github/dependabot.yml"] = render_dependabot()

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
