#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


class ScaffoldError(Exception):
    pass


class OptionError(Exception):
    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


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


def parse_set_value(raw: str, option_type: str) -> object:
    if option_type == "bool":
        if raw not in ("true", "false"):
            raise ValueError(f"must be true or false, not {raw!r}")
        return raw == "true"
    if option_type == "list":
        return [item.strip() for item in raw.split(",") if item.strip() != ""] if raw else []
    return raw


def resolve(spec: dict, preset: str | None, config_path: Path | None, sets: list[str], name: str | None):
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

    if name is not None:
        if "name" not in options:
            errors.append("name: unknown option")
        else:
            values["name"] = name
            layers["name"] = "name"

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

    topics = values.get("topics") or []
    subscriptions = values.get("subscriptions") or []
    for item in subscriptions:
        if item not in topics:
            errors.append(f"subscriptions: {item!r} is not declared in topics")

    return errors


def select_components(components: dict[str, dict], values: dict[str, object]) -> list[dict]:
    selected = [c for c in components.values() if evaluate_when(c["when"], values)]
    return sorted(selected, key=lambda c: c["order"])


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


def build_flags(values: dict[str, object], selected: list[dict]) -> dict[str, bool]:
    selected_ids = {c["id"] for c in selected}
    flags = {component_id.replace("-", "_"): component_id in selected_ids for component_id in components_all_ids()}
    flags["workflows"] = bool(values.get("workflows"))
    flags["time_in_instance"] = ("container-apps" in selected_ids) or ("postgres-flexible" in selected_ids)
    return flags


def components_all_ids() -> list[str]:
    return [path.name for path in sorted(COMPONENTS_DIR.iterdir()) if path.is_dir()]


def hcl_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"


def build_values(values: dict[str, object]) -> dict[str, str]:
    return {
        "__NAME__": str(values["name"]),
        "__TERRAFORM_VERSION__": str(values["terraform_version"]),
        "__AZURERM_VERSION__": str(values["azurerm_version"]),
        "__TOPICS_HCL__": hcl_list(values.get("topics") or []),
        "__SUBSCRIPTIONS_HCL__": hcl_list(values.get("subscriptions") or []),
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


def plan(spec: dict, preset: str | None, config_path: Path | None, sets: list[str], name: str | None):
    values, layers = resolve(spec, preset, config_path, sets, name)
    errors = validate(spec, values)
    if errors:
        raise OptionError(errors)
    components = discover_components()
    selected = select_components(components, values)
    flags = build_flags(values, selected)
    placeholders = build_values(values)
    files = render_all(selected, flags, placeholders)
    metadata = {
        "package": "azure",
        "skill": "scaffold",
        "version": spec["version"],
        "preset": preset,
        "options": values,
    }
    return values, layers, files, metadata


def write_project(project: Path, files: dict[str, str], metadata: dict) -> None:
    for relative, content in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    (project / ".scaffold.azure.json").write_bytes((json.dumps(metadata, indent=2) + "\n").encode("utf-8"))


def cmd_new(args: argparse.Namespace) -> int:
    spec = load_spec()
    try:
        values, layers, files, metadata = plan(spec, args.preset, args.config, args.set or [], args.name)
    except OptionError as error:
        for message in error.messages:
            print(message, file=sys.stderr)
        return 2
    except ScaffoldError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.into is not None:
        project = args.into
        if not project.is_dir():
            print(f"error: --into directory does not exist: {project}", file=sys.stderr)
            return 1
        conflicts = [relative for relative in sorted(files) if (project / relative).exists()]
        conflicts += [".scaffold.azure.json"] if (project / ".scaffold.azure.json").exists() else []
        if conflicts:
            for relative in conflicts:
                print(f"already exists: {project / relative}", file=sys.stderr)
            return 1
    else:
        if args.destination is None:
            print("error: --name requires --destination", file=sys.stderr)
            return 1
        if not args.destination.is_dir():
            print(f"error: destination does not exist: {args.destination}", file=sys.stderr)
            return 1
        project = args.destination / str(values["name"])
        if project.exists():
            print(f"error: project already exists: {project}", file=sys.stderr)
            return 1

    if args.dry_run:
        for key in sorted(values):
            print(f"{key}={values[key]!r} [{layers.get(key, 'default')}]")
        for relative in sorted(files):
            print(project / relative)
        print((project / ".scaffold.azure.json"))
        return 0

    write_project(project, files, metadata)
    print(f"created {project}")
    return 0


def cmd_options(args: argparse.Namespace) -> int:
    spec = load_spec()
    try:
        values, layers, _, _ = plan(spec, args.preset, args.config, args.set or [], None)
    except OptionError as error:
        for message in error.messages:
            print(message, file=sys.stderr)
        return 2
    except ScaffoldError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for key in sorted(values):
        print(f"{key}={values[key]!r} [{layers.get(key, 'default')}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create an Azure project in the azure:structure layout.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new = subparsers.add_parser("new")
    new.add_argument("--into", type=Path)
    new.add_argument("--name")
    new.add_argument("--destination", type=Path)
    new.add_argument("--preset")
    new.add_argument("--config", type=Path)
    new.add_argument("--set", action="append")
    new.add_argument("--dry-run", action="store_true")
    new.set_defaults(func=cmd_new)

    options = subparsers.add_parser("options")
    options.add_argument("--preset")
    options.add_argument("--config", type=Path)
    options.add_argument("--set", action="append")
    options.set_defaults(func=cmd_options)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "new" and args.into is None and args.name is None:
        parser.error("new requires --into or --name/--destination")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
