from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents/azure/utility/scaffold/scripts/scaffold.py"
SPEC = importlib.util.spec_from_file_location("azure_scaffold", SCRIPT)
scaffold = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scaffold)

EMPTY_WORKLOADS_FILES = {
    ".github/actions/build-workload/action.yml",
    ".github/actions/terraform-login-init/action.yml",
    ".github/dependabot.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/deploy-environment.yml",
    ".github/workflows/destroy-preview.yml",
    ".github/workflows/pr-closed.yml",
    ".github/workflows/pr.yml",
    ".github/workflows/prod.yml",
    ".scaffold.azure.json",
    "infra/.gitignore",
    "infra/README.md",
    "infra/app.json",
    "infra/env/ci.tfvars",
    "infra/env/prod.tfvars",
    "infra/foundation/backend.tf",
    "infra/foundation/budget.tf",
    "infra/foundation/locals.tf",
    "infra/foundation/main.tf",
    "infra/foundation/outputs.tf",
    "infra/foundation/providers.tf",
    "infra/foundation/variables.tf",
    "infra/foundation/versions.tf",
    "infra/instance/backend.tf",
    "infra/instance/data.tf",
    "infra/instance/function-storage.tf",
    "infra/instance/identity.tf",
    "infra/instance/locals.tf",
    "infra/instance/outputs.tf",
    "infra/instance/providers.tf",
    "infra/instance/variables.tf",
    "infra/instance/versions.tf",
    "infra/instance/workloads.tf",
}

SANDBOX_FILES = EMPTY_WORKLOADS_FILES | {
    "infra/foundation/container-apps.tf",
    "infra/foundation/flex-consumption.tf",
    "infra/foundation/postgres.tf",
    "infra/instance/container-apps.tf",
    "infra/instance/flex-consumption.tf",
    "infra/instance/migration.tf",
    "infra/instance/postgres.tf",
}

SANDBOX_PROFILE = {
    "schema_version": 1,
    "name": "demo",
    "workloads": [
        {
            "id": "api",
            "kind": "http",
            "port": 8080,
            "probes": {"liveness": "/health/live", "readiness": "/health/ready"},
            "build": {"image": "echo build-api"},
        },
        {"id": "worker", "kind": "background", "build": {"image": "echo build-worker"}},
        {
            "id": "fn",
            "kind": "functions",
            "runtime": {"name": "dotnet-isolated", "version": "10.0"},
            "build": {"package": "echo build-fn"},
        },
    ],
    "database": {
        "engine": "postgres",
        "migration": {
            "build": {"image": "echo build-migrate"},
            "command": ["dotnet", "ef", "database", "update"],
        },
    },
    "settings": {
        "ConnectionStrings__Postgres": (
            "Host={postgres.host};Database={postgres.database};Username={postgres.user};Ssl Mode=Require"
        ),
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "{monitor.connection_string}",
        "AZURE_CLIENT_ID": "{identity.client_id}",
    },
}

AZURE_SCAFFOLD_TERRAFORM = os.environ.get("AZURE_SCAFFOLD_TERRAFORM") == "1"


def run(argv: list[str], input_func=input) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = scaffold.main(argv, input_func=input_func)
    return code, out.getvalue(), err.getvalue()


def files(project: Path) -> set[str]:
    return {
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file() and ".terraform" not in path.parts
    }


def write_profile(root: Path, profile: dict, relative: str = "infra/app.json") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path


def new_project(temporary: str) -> Path:
    root = Path(temporary) / "project"
    (root / "infra").mkdir(parents=True)
    return root


class ProfileValidationTests(unittest.TestCase):
    def check(self, profile: dict, expected_in_error: str, sets: list[str] | None = None) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            args = ["new", "--into", str(root), "--no-input"]
            for item in sets or []:
                args += ["--set", item]
            code, _, err = run(args)
            self.assertEqual(2, code, err)
            self.assertIn(expected_in_error, err)

    def test_missing_profile_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("missing profile", err)

    def test_invalid_json_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            (root / "infra" / "app.json").write_text("{not json", encoding="utf-8")
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("invalid JSON", err)

    def test_unknown_top_level_key_exits_2(self) -> None:
        self.check({**SANDBOX_PROFILE, "nope": True}, "unknown key 'nope'")

    def test_bad_schema_version_exits_2(self) -> None:
        self.check({**SANDBOX_PROFILE, "schema_version": 2}, "schema_version")

    def test_bad_name_exits_2(self) -> None:
        self.check({**SANDBOX_PROFILE, "name": "Not-Valid"}, "profile.name")

    def test_unknown_kind_exits_2(self) -> None:
        profile = {"schema_version": 1, "name": "demo", "workloads": [{"id": "api", "kind": "nope", "build": {}}]}
        self.check(profile, "kind")

    def test_duplicate_id_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {"id": "api", "kind": "background", "build": {"image": "x"}},
                {"id": "api", "kind": "background", "build": {"image": "x"}},
            ],
        }
        self.check(profile, "duplicate id")

    def test_bad_workload_id_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [{"id": "Not_Valid", "kind": "background", "build": {"image": "x"}}],
        }
        self.check(profile, ".id")

    def test_http_missing_port_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "probes": {"liveness": "/a", "readiness": "/b"},
                    "build": {"image": "x"},
                }
            ],
        }
        self.check(profile, ".port")

    def test_http_bad_probe_path_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "no-slash", "readiness": "/b"},
                    "build": {"image": "x"},
                }
            ],
        }
        self.check(profile, "probes.liveness")

    def test_http_probe_path_rejects_github_actions_expression_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "/${{ secrets.FOO }}", "readiness": "/b"},
                    "build": {"image": "x"},
                }
            ],
        }
        self.check(profile, "must be a URL path")

    def test_http_probe_path_rejects_shell_metacharacters_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "/a", "readiness": "/b$(touch pwned)"},
                    "build": {"image": "x"},
                }
            ],
        }
        self.check(profile, "must be a URL path")

    def test_functions_missing_runtime_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [{"id": "fn", "kind": "functions", "build": {"package": "x"}}],
        }
        self.check(profile, ".runtime")

    def test_http_on_app_service_missing_runtime_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "/a", "readiness": "/b"},
                    "build": {"package": "x"},
                }
            ],
        }
        self.check(profile, ".runtime", sets=["http_host=app-service"])

    def test_http_on_app_service_bad_runtime_name_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "/a", "readiness": "/b"},
                    "runtime": {"name": "ruby", "version": "1"},
                    "build": {"package": "x"},
                }
            ],
        }
        self.check(profile, ".runtime", sets=["http_host=app-service"])

    def test_missing_build_image_for_container_apps_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [{"id": "worker", "kind": "background", "build": {"package": "x"}}],
        }
        self.check(profile, "build.image")

    def test_missing_build_package_for_flex_consumption_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "fn",
                    "kind": "functions",
                    "runtime": {"name": "dotnet-isolated", "version": "10.0"},
                    "build": {"image": "x"},
                }
            ],
        }
        self.check(profile, "build.package")

    def test_build_image_rejects_github_actions_expression_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {"id": "worker", "kind": "background", "build": {"image": "echo ${{ secrets.FOO }}"}}
            ],
        }
        self.check(profile, "must not contain")

    def test_build_package_rejects_closing_braces_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "fn",
                    "kind": "functions",
                    "runtime": {"name": "dotnet-isolated", "version": "10.0"},
                    "build": {"package": "echo }} nope"},
                }
            ],
        }
        self.check(profile, "must not contain")

    def test_migration_build_image_rejects_github_actions_expression_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "database": {
                "engine": "postgres",
                "migration": {"build": {"image": "echo ${{ github.token }}"}, "command": ["x"]},
            },
        }
        self.check(profile, "must not contain")

    def test_migration_command_rejects_github_actions_expression_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "database": {
                "engine": "postgres",
                "migration": {"build": {"image": "x"}, "command": ["echo", "${{ secrets.FOO }}"]},
            },
        }
        self.check(profile, "must not contain")

    def test_both_build_image_and_package_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {"id": "worker", "kind": "background", "build": {"image": "echo x", "package": "echo y"}}
                ],
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)

    def test_unknown_workload_key_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [{"id": "worker", "kind": "background", "build": {"image": "x"}, "nope": True}],
        }
        self.check(profile, "unknown key 'nope'")

    def test_database_engine_must_be_postgres_exits_2(self) -> None:
        self.check({"schema_version": 1, "name": "demo", "workloads": [], "database": {"engine": "mysql"}}, "engine")

    def test_migration_missing_build_image_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "database": {"engine": "postgres", "migration": {"command": ["x"]}},
        }
        self.check(profile, "migration.build.image")

    def test_migration_command_must_be_list_of_strings_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "database": {"engine": "postgres", "migration": {"build": {"image": "x"}, "command": "nope"}},
        }
        self.check(profile, "migration.command")

    def test_unknown_token_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "settings": {"FOO": "{nope.token}"},
        }
        self.check(profile, "unknown token")

    def test_postgres_token_requires_database_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "settings": {"FOO": "{postgres.host}"},
        }
        self.check(profile, "requires a database")

    def test_servicebus_token_requires_messaging_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "settings": {"FOO": "{servicebus.namespace}"},
        }
        self.check(profile, "requires messaging=service-bus")

    def test_keyvault_token_requires_key_vault_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "settings": {"FOO": "{keyvault.uri}"},
        }
        self.check(profile, "requires key_vault")

    def test_bad_env_name_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "settings": {"not-valid": "x"},
        }
        self.check(profile, "not a valid environment variable name")

    def test_workload_settings_validated_the_same_way_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "worker",
                    "kind": "background",
                    "build": {"image": "x"},
                    "settings": {"FOO": "{nope.token}"},
                }
            ],
        }
        self.check(profile, "unknown token")

    def test_workload_messaging_unknown_key_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "worker",
                    "kind": "background",
                    "build": {"image": "x"},
                    "messaging": {"nope": ["orders"]},
                }
            ],
        }
        self.check(profile, "unknown key 'nope'", sets=["messaging=service-bus", "topics=orders"])

    def test_workload_messaging_topic_not_declared_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "worker",
                    "kind": "background",
                    "build": {"image": "x"},
                    "messaging": {"publishes": ["nope"]},
                }
            ],
        }
        self.check(profile, "is not declared in topics", sets=["messaging=service-bus", "topics=orders"])

    def test_workload_messaging_must_be_a_list_of_strings_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "worker",
                    "kind": "background",
                    "build": {"image": "x"},
                    "messaging": {"consumes": "orders"},
                }
            ],
        }
        self.check(profile, "messaging.consumes", sets=["messaging=service-bus", "topics=orders"])


class TokenResolutionTests(unittest.TestCase):
    def test_literal_text_and_tokens_render_expected_expression(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "background",
                        "build": {"image": "x"},
                    }
                ],
                "database": {"engine": "postgres"},
                "settings": {
                    "CONN": "Host={postgres.host};User={postgres.user}",
                },
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            expected = (
                '"CONN" = "Host=${data.azurerm_postgresql_flexible_server.main.fqdn};'
                'User=${azurerm_user_assigned_identity.workload["api"].name}"'
            )
            self.assertIn(expected, workloads_tf)

    def test_single_token_renders_as_a_raw_expression(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [{"id": "api", "kind": "background", "build": {"image": "x"}}],
                "settings": {"AZURE_CLIENT_ID": "{identity.client_id}"},
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            self.assertIn(
                '"AZURE_CLIENT_ID" = azurerm_user_assigned_identity.workload["api"].client_id', workloads_tf
            )

    def test_workload_settings_merge_over_top_level_and_win_on_clash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "background",
                        "build": {"image": "x"},
                        "settings": {"AZURE_CLIENT_ID": "{identity.client_id}", "ONLY_WORKLOAD": "x"},
                    },
                    {"id": "worker", "kind": "background", "build": {"image": "x"}},
                ],
                "settings": {"AZURE_CLIENT_ID": "shared-literal", "SHARED": "y"},
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            self.assertRegex(
                workloads_tf,
                r'"AZURE_CLIENT_ID"\s*=\s*azurerm_user_assigned_identity\.workload\["api"\]\.client_id',
            )
            self.assertRegex(workloads_tf, r'"ONLY_WORKLOAD"\s*=\s*"x"')
            self.assertRegex(workloads_tf, r'"AZURE_CLIENT_ID"\s*=\s*"shared-literal"')
            self.assertRegex(workloads_tf, r'"SHARED"\s*=\s*"y"')

    def test_migration_uses_only_top_level_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "background",
                        "build": {"image": "x"},
                        "settings": {"ONLY_WORKLOAD": "x"},
                    }
                ],
                "database": {
                    "engine": "postgres",
                    "migration": {"build": {"image": "x"}, "command": ["x"]},
                },
                "settings": {"AZURE_CLIENT_ID": "{identity.client_id}"},
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            migration_tf = (root / "infra/instance/migration.tf").read_text(encoding="utf-8")
            self.assertIn("azurerm_user_assigned_identity.migrator.client_id", migration_tf)
            self.assertNotIn("ONLY_WORKLOAD", migration_tf)

    def test_migration_script_interpolates_real_identity_and_database_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [{"id": "api", "kind": "background", "build": {"image": "x"}}],
                "database": {
                    "engine": "postgres",
                    "migration": {"build": {"image": "x"}, "command": ["x"]},
                },
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            migration_tf = (root / "infra/instance/migration.tf").read_text(encoding="utf-8")

            self.assertIn('${azurerm_user_assigned_identity.workload["api"].name}', migration_tf)
            self.assertIn("${azurerm_user_assigned_identity.migrator.name}", migration_tf)
            self.assertIn("${local.database_name}", migration_tf)

            self.assertNotIn("current_database()", migration_tf)
            self.assertNotIn("'demo-api-id'", migration_tf)
            self.assertNotIn('"demo-api-id"', migration_tf)
            self.assertNotIn("'demo-migrator-id'", migration_tf)
            self.assertNotIn('"demo-migrator-id"', migration_tf)


class InteractiveModeTests(unittest.TestCase):
    def test_prompts_only_for_unset_options_and_validates_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            prompted_keys = []
            http_host_attempts = {"count": 0}

            def fake_input(prompt: str) -> str:
                key = prompt.split(" ")[0]
                prompted_keys.append(key)
                if key == "http_host":
                    http_host_attempts["count"] += 1
                    return "nope" if http_host_attempts["count"] == 1 else "app-service"
                return ""

            with unittest.mock.patch("sys.stdin.isatty", return_value=True):
                code, _, err = run(
                    ["new", "--into", str(root), "--set", "messaging=service-bus"], input_func=fake_input
                )
            self.assertEqual(0, code, err)
            self.assertNotIn("messaging", prompted_keys)
            self.assertIn("http_host", prompted_keys)
            self.assertGreaterEqual(prompted_keys.count("http_host"), 2)

    def test_dry_run_and_no_input_skip_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})

            def failing_input(prompt: str) -> str:
                raise AssertionError("should not prompt")

            with unittest.mock.patch("sys.stdin.isatty", return_value=True):
                code, _, err = run(["new", "--into", str(root), "--no-input"], input_func=failing_input)
                self.assertEqual(0, code, err)


class IntoConflictTests(unittest.TestCase):
    def test_into_refuses_when_a_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            (root / "infra" / "foundation").mkdir(parents=True)
            (root / "infra" / "foundation" / "versions.tf").write_text("existing\n", encoding="utf-8")
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(1, code)
            self.assertIn("versions.tf", err)
            self.assertEqual("existing\n", (root / "infra/foundation/versions.tf").read_text(encoding="utf-8"))

    def test_a_failed_write_removes_everything_it_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            original = Path.open
            calls = {"count": 0}

            def failing_open(path: Path, mode="r", *args, **kwargs):
                if mode == "xb":
                    calls["count"] += 1
                    if calls["count"] == 5:
                        raise OSError("disk full")
                return original(path, mode, *args, **kwargs)

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(1, code)
            self.assertIn("disk full", err)
            remaining = files(root)
            self.assertEqual({"infra/app.json"}, remaining)

    def test_external_profile_is_copied_and_identical_copy_is_not_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            external = Path(temporary) / "external.json"
            profile = {"schema_version": 1, "name": "demo", "workloads": []}
            external.write_text(json.dumps(profile), encoding="utf-8")
            code, _, err = run(["new", "--into", str(root), "--profile", str(external), "--no-input"])
            self.assertEqual(0, code, err)
            self.assertTrue((root / "infra/app.json").is_file())

            shutil.rmtree(root / "infra" / "foundation")
            shutil.rmtree(root / "infra" / "instance")
            shutil.rmtree(root / ".github")
            (root / "infra/README.md").unlink()
            (root / "infra/env/ci.tfvars").unlink()
            (root / "infra/env/prod.tfvars").unlink()
            (root / "infra/.gitignore").unlink()
            (root / ".scaffold.azure.json").unlink()

            code, _, err = run(["new", "--into", str(root), "--profile", str(external), "--no-input"])
            self.assertEqual(0, code, err)

    def test_external_profile_differing_from_existing_app_json_is_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            external = Path(temporary) / "external.json"
            external.write_text(
                json.dumps({"schema_version": 1, "name": "other", "workloads": []}), encoding="utf-8"
            )
            code, _, err = run(["new", "--into", str(root), "--profile", str(external), "--no-input"])
            self.assertEqual(1, code)
            self.assertIn("app.json", err)


class WriteTransactionTests(unittest.TestCase):
    profile = {"schema_version": 1, "name": "demo", "workloads": []}

    def scaffold(self, root: Path, external: Path | None = None, dry_run: bool = False):
        argv = ["new", "--into", str(root), "--no-input"]
        if external is not None:
            argv += ["--profile", str(external)]
        if dry_run:
            argv += ["--dry-run"]
        return run(argv)

    def existing_profile(self, root: Path) -> tuple[Path, bytes, int]:
        path = write_profile(root, self.profile)
        content = (json.dumps(self.profile, indent=2) + "\r\n").encode("utf-8")
        path.write_bytes(content)
        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        return path, content, path.stat().st_mtime_ns

    def symlink(self, link: Path, target: Path, directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")

    @contextlib.contextmanager
    def tracked_pins(self):
        pins, descriptors = [], []
        original_file = scaffold.CreatedPath.pin_file
        original_directory = scaffold.CreatedPath.pin_directory

        def track_file(created, descriptor):
            pins.append(created)
            original_file(created, descriptor)
            descriptors.append(created.descriptor)

        def track_directory(created):
            pins.append(created)
            original_directory(created)
            if created.descriptor is not None:
                descriptors.append(created.descriptor)

        with unittest.mock.patch.object(scaffold.CreatedPath, "pin_file", track_file), unittest.mock.patch.object(
            scaffold.CreatedPath, "pin_directory", track_directory
        ):
            yield pins, descriptors

    def assert_pins_released(self, pins, descriptors) -> None:
        for pin in pins:
            self.assertIsNone(pin.descriptor)
            self.assertIsNone(pin.windows_release)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_write_and_final_close_interruptions_release_all_pins_without_masking(self) -> None:
        for stage in ("write", "final close"):
            for interruption_type in (KeyboardInterrupt, SystemExit):
                for release_error_type in (OSError, KeyboardInterrupt, SystemExit):
                    with self.subTest(stage=stage, interruption=interruption_type, release_error=release_error_type):
                        with tempfile.TemporaryDirectory() as temporary:
                            root = new_project(temporary)
                            original_open = Path.open
                            original_release = scaffold.CreatedPath.release
                            interruption = interruption_type("original interruption")
                            attempts = []

                            @contextlib.contextmanager
                            def interrupted_open(target, mode="r", *args, **kwargs):
                                with original_open(target, mode, *args, **kwargs) as handle:
                                    if mode == "xb" and stage == "write":
                                        proxy = unittest.mock.Mock(wraps=handle)

                                        def interrupted_write(data):
                                            handle.write(data[:3])
                                            raise interruption

                                        proxy.write.side_effect = interrupted_write
                                        yield proxy
                                    else:
                                        yield handle
                                if mode == "xb" and stage == "final close" and target.name == ".scaffold.azure.json":
                                    raise interruption

                            def failing_release(created):
                                attempts.append(created)
                                original_release(created)
                                raise release_error_type("ownership release failed")

                            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(
                                Path, "open", interrupted_open
                            ), unittest.mock.patch.object(scaffold.CreatedPath, "release", failing_release):
                                with self.assertRaises(interruption_type) as caught:
                                    scaffold.write_project(root, {"nested/first.txt": "first", "nested/second.txt": "second"}, {})
                            self.assertIs(interruption, caught.exception)
                            self.assertEqual(list(reversed(pins)), attempts)
                            self.assert_pins_released(pins, descriptors)
                            expected = {"nested/first.txt"}
                            if stage == "final close":
                                expected |= {"nested/second.txt", ".scaffold.azure.json"}
                            self.assertEqual(expected, files(root))
                            moved = root / "released"
                            (root / "nested").rename(moved)
                            moved.rename(root / "nested")
                            first = root / "nested/first.txt"
                            first.rename(root / "renamed.txt")
                            (root / "renamed.txt").unlink()

    def test_success_release_interruptions_escape_after_all_attempts_inside_caller_handler(self) -> None:
        for error_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(error=error_type), tempfile.TemporaryDirectory() as temporary:
                root = new_project(temporary)
                original = scaffold.CreatedPath.release
                interruption = error_type("release interruption")
                attempts = []

                def interrupted_release(created):
                    attempts.append(created)
                    original(created)
                    if len(attempts) == 1:
                        raise interruption
                    raise OSError("later release failure")

                try:
                    raise RuntimeError("unrelated caller exception")
                except RuntimeError:
                    with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(
                        scaffold.CreatedPath, "release", interrupted_release
                    ):
                        with self.assertRaises(error_type) as caught:
                            scaffold.write_project(root, {"nested/file.txt": "finished"}, {})
                self.assertIs(interruption, caught.exception)
                self.assertEqual(list(reversed(pins)), attempts)
                self.assert_pins_released(pins, descriptors)
                self.assertEqual("finished", (root / "nested/file.txt").read_text())
                self.assertEqual({"nested/file.txt", ".scaffold.azure.json"}, files(root))
                (root / "nested").rename(root / "released")

    def test_normal_write_exceptions_remain_wrapped_and_rolled_back(self) -> None:
        for error_type in (RuntimeError, ValueError):
            with self.subTest(error=error_type), tempfile.TemporaryDirectory() as temporary:
                root = new_project(temporary)
                original = Path.open
                failure = error_type("normal write failure")

                @contextlib.contextmanager
                def failing_open(target, mode="r", *args, **kwargs):
                    with original(target, mode, *args, **kwargs) as handle:
                        proxy = unittest.mock.Mock(wraps=handle)
                        proxy.write.side_effect = failure
                        yield proxy

                with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(Path, "open", failing_open):
                    with self.assertRaises(scaffold.ScaffoldError) as caught:
                        scaffold.write_project(root, {"nested/file.txt": "content"}, {})
                self.assertIs(failure, caught.exception.__cause__)
                self.assertIn("rolled back newly created outputs", str(caught.exception))
                self.assert_pins_released(pins, descriptors)
                self.assertEqual([root / "infra"], list(root.iterdir()))
                self.assertEqual(set(), files(root))

    def test_second_output_preflight_failure_returns_one_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            path, content, modified = self.existing_profile(root)
            original = scaffold.output_conflicts
            calls = 0

            def failing_preflight(project, outputs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise PermissionError("second preflight denied")
                return original(project, outputs)

            with unittest.mock.patch.object(scaffold, "output_conflicts", failing_preflight):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertEqual(2, calls)
            self.assertIn("could not inspect outputs", err)
            self.assertIn("second preflight denied", err)
            self.assertNotIn("Traceback", err)
            self.assertNotIn("rolled back", err)
            self.assertEqual(content, path.read_bytes())
            self.assertEqual(modified, path.stat().st_mtime_ns)
            self.assertEqual({"infra/app.json"}, files(root))

    def test_success_releases_all_file_and_directory_pins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            with self.tracked_pins() as (pins, descriptors):
                code, _, err = self.scaffold(root)
            self.assertEqual(0, code, err)
            self.assertTrue(pins)
            self.assert_pins_released(pins, descriptors)
            renamed = root / "renamed"
            (root / ".github").rename(renamed)
            renamed.rename(root / ".github")

    def test_final_stream_close_failure_keeps_pins_live_then_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open
            checked = []

            @contextlib.contextmanager
            def failing_close(target: Path, mode="r", *args, **kwargs):
                with original(target, mode, *args, **kwargs) as handle:
                    yield handle
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    for pin in pins:
                        if pin.descriptor is not None:
                            self.assertEqual(pin.identity, scaffold.created_identity(os.fstat(pin.descriptor)))
                        else:
                            self.assertIsNotNone(pin.windows_release)
                    checked.append(True)
                    raise OSError("final stream close failed")

            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(Path, "open", failing_close):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("final stream close failed", err)
            self.assertEqual([True], checked)
            self.assert_pins_released(pins, descriptors)
            self.assertEqual({"infra/app.json"}, files(root))
            self.assertEqual([root / "infra"], list(root.iterdir()))

    def test_failed_file_pin_acquisition_retains_the_unproved_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(
                os, "dup", side_effect=OSError("file pin denied")
            ):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            retained = root / "infra/.gitignore"
            self.assertTrue(retained.is_file())
            self.assertIn("file pin denied", err)
            self.assertIn(f"retained paths: {retained}", err)
            self.assert_pins_released(pins, descriptors)
            self.assertEqual({"infra/app.json", "infra/.gitignore"}, files(root))

    def test_failed_directory_pin_acquisition_retains_the_unproved_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            failed = []

            def failing_pin(created):
                failed.append(created)
                raise PermissionError("directory pin denied")

            with unittest.mock.patch.object(scaffold.CreatedPath, "pin_directory", failing_pin):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertEqual(1, len(failed))
            self.assertTrue(failed[0].path.is_dir())
            self.assertEqual([], list(failed[0].path.iterdir()))
            self.assertIn("directory pin denied", err)
            self.assertIn(str(failed[0].path), err)
            self.assertIsNone(failed[0].descriptor)
            self.assertIsNone(failed[0].windows_release)
            self.assertEqual({"infra/app.json"}, files(root))

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing test")
    def test_windows_empty_directory_pin_denies_replacement_and_closes_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "created"
            directory.mkdir()
            original = scaffold.open_windows_directory
            released = []

            def counted_open(path):
                close = original(path)

                def counted_close():
                    released.append(path)
                    close()

                return counted_close

            with unittest.mock.patch.object(scaffold, "open_windows_directory", counted_open):
                pin = scaffold.CreatedPath(directory, directory=True)
                pin.pin_directory()
            try:
                self.assertIsNotNone(pin.windows_release)
                with self.assertRaises(PermissionError):
                    directory.rename(Path(temporary) / "replacement")
                with self.assertRaises(PermissionError):
                    directory.rmdir()
            finally:
                pin.release()
                pin.release()
            self.assertEqual([directory], released)
            directory.rmdir()

    def test_failed_directory_identity_probe_releases_the_acquired_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original_lstat = Path.lstat
            original_fstat = os.fstat
            original_pin = scaffold.CreatedPath.pin_directory
            failed = []

            def failing_lstat(path, *args, **kwargs):
                if failed and path == failed[0].path and failed[0].windows_release is not None:
                    raise OSError("directory identity denied")
                return original_lstat(path, *args, **kwargs)

            def failing_fstat(descriptor):
                info = original_fstat(descriptor)
                if scaffold.stat.S_ISDIR(info.st_mode):
                    raise OSError("directory identity denied")
                return info

            def acquire(created):
                failed.append(created)
                original_pin(created)

            with unittest.mock.patch.object(scaffold.CreatedPath, "pin_directory", acquire), unittest.mock.patch.object(
                Path, "lstat", failing_lstat
            ), unittest.mock.patch.object(os, "fstat", failing_fstat):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("directory identity denied", err)
            self.assertEqual(1, len(failed))
            pin = failed[0]
            self.assertIsNone(pin.descriptor)
            self.assertIsNone(pin.windows_release)
            self.assertTrue(pin.path.is_dir())
            self.assertEqual([], list(pin.path.iterdir()))
            self.assertIn(str(pin.path), err)
            pin.path.rmdir()

    def test_failed_file_identity_probe_releases_the_acquired_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original_dup = os.dup
            original_fstat = os.fstat
            duplicates = []

            def duplicate(descriptor):
                result = original_dup(descriptor)
                duplicates.append(result)
                return result

            def failing_identity(descriptor):
                if descriptor in duplicates:
                    raise OSError("file identity denied")
                return original_fstat(descriptor)

            with unittest.mock.patch.object(os, "dup", duplicate), unittest.mock.patch.object(os, "fstat", failing_identity):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("file identity denied", err)
            for descriptor in duplicates:
                with self.assertRaises(OSError):
                    original_fstat(descriptor)
            self.assertEqual({"infra/app.json", "infra/.gitignore"}, files(root))

    def test_success_release_failure_keeps_completed_outputs_and_releases_other_pins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = scaffold.CreatedPath.release
            failed = []

            def failing_release(created):
                original(created)
                if not failed:
                    failed.append(created.path)
                    raise OSError("ownership close failed")

            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(
                scaffold.CreatedPath, "release", failing_release
            ):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("created outputs but could not release", err)
            self.assertNotIn("rolled back", err)
            self.assert_pins_released(pins, descriptors)
            self.assertEqual(EMPTY_WORKLOADS_FILES, files(root))

    def test_failure_release_error_preserves_original_write_error_and_releases_other_pins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original_release = scaffold.CreatedPath.release
            original_open = Path.open
            failed = []

            def failing_release(created):
                original_release(created)
                if not failed:
                    failed.append(created.path)
                    raise OSError("ownership close failed")

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    raise OSError("original metadata failure")
                return original_open(target, mode, *args, **kwargs)

            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(
                scaffold.CreatedPath, "release", failing_release
            ), unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("original metadata failure", err)
            self.assert_pins_released(pins, descriptors)
            self.assertEqual(1, len(failed))

    @unittest.skipIf(os.name == "nt", "Windows pins deny completed unlink/recreate; denial is tested separately")
    def test_completed_unlink_recreate_keeps_old_file_pin_live_and_preserves_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open
            replaced = root / "infra/.gitignore"
            checked = []

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    pin = next(pin for pin in pins if pin.path == replaced)
                    self.assertEqual(pin.identity, scaffold.created_identity(os.fstat(pin.descriptor)))
                    replaced.unlink()
                    with original(replaced, "wb") as handle:
                        handle.write(b"replacement owner")
                    self.assertEqual(pin.identity, scaffold.created_identity(os.fstat(pin.descriptor)))
                    self.assertNotEqual(pin.identity, scaffold.created_identity(replaced.lstat()))
                    checked.append(True)
                    raise OSError("metadata disk full")
                return original(target, mode, *args, **kwargs)

            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertEqual([True], checked)
            self.assert_pins_released(pins, descriptors)
            self.assertEqual(b"replacement owner", replaced.read_bytes())
            self.assertIn(f"retained paths: {replaced}", err)
            self.assertEqual({"infra/app.json", "infra/.gitignore"}, files(root))

    @unittest.skipIf(os.name == "nt", "Windows pins deny completed rmdir/recreate; denial is tested separately")
    def test_completed_rmdir_recreate_keeps_old_directory_pin_live_and_preserves_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open
            replaced = root / ".github"
            checked = []

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    pin = next(pin for pin in pins if pin.path == replaced)
                    self.assertEqual(pin.identity, scaffold.created_identity(os.fstat(pin.descriptor)))
                    for child in sorted(replaced.rglob("*"), key=lambda child: len(child.parts), reverse=True):
                        if child.is_dir():
                            child.rmdir()
                        else:
                            child.unlink()
                    replaced.rmdir()
                    replaced.mkdir()
                    self.assertEqual(pin.identity, scaffold.created_identity(os.fstat(pin.descriptor)))
                    self.assertNotEqual(pin.identity, scaffold.created_identity(replaced.lstat()))
                    checked.append(True)
                    raise OSError("metadata disk full")
                return original(target, mode, *args, **kwargs)

            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertEqual([True], checked)
            self.assert_pins_released(pins, descriptors)
            self.assertTrue(replaced.is_dir())
            self.assertEqual([], list(replaced.iterdir()))
            self.assertIn(f"retained paths: {replaced}", err)
            self.assertEqual({"infra/app.json"}, files(root))

    def test_identical_external_profile_preserves_bytes_and_mtime_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            path, content, modified = self.existing_profile(root)
            external = Path(temporary) / "external.json"
            external.write_bytes(content)
            code, _, err = self.scaffold(root, external)
            self.assertEqual(0, code, err)
            self.assertEqual(content, path.read_bytes())
            self.assertEqual(modified, path.stat().st_mtime_ns)

    def test_identical_external_profile_survives_final_metadata_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            path, content, modified = self.existing_profile(root)
            external = Path(temporary) / "external.json"
            external.write_bytes(content)
            unrelated = root / "keep.txt"
            unrelated.write_bytes(b"caller-owned")
            original = Path.open

            @contextlib.contextmanager
            def failing_open(target: Path, mode="r", *args, **kwargs):
                with original(target, mode, *args, **kwargs) as handle:
                    if mode == "xb" and target.name == ".scaffold.azure.json":
                        proxy = unittest.mock.Mock(wraps=handle)

                        def fail_write(data):
                            handle.write(data[:3])
                            raise OSError("metadata disk full")

                        proxy.write.side_effect = fail_write
                        yield proxy
                    else:
                        yield handle

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root, external)
            self.assertEqual(1, code, err)
            self.assertIn("metadata disk full", err)
            self.assertEqual(content, path.read_bytes())
            self.assertEqual(modified, path.stat().st_mtime_ns)
            self.assertEqual(content, external.read_bytes())
            self.assertEqual(b"caller-owned", unrelated.read_bytes())
            self.assertEqual({"infra/app.json", "keep.txt"}, files(root))
            self.assertEqual({root / "infra"}, {entry for entry in root.iterdir() if entry.is_dir()})

    def test_new_external_profile_copy_is_rolled_back_on_metadata_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            external = write_profile(Path(temporary), self.profile, "external.json")
            content = external.read_bytes()
            original = Path.open

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    raise OSError("metadata disk full")
                return original(target, mode, *args, **kwargs)

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root, external)
            self.assertEqual(1, code, err)
            self.assertIn("metadata disk full", err)
            self.assertEqual(content, external.read_bytes())
            self.assertEqual(set(), files(root))
            self.assertEqual([root / "infra"], list(root.iterdir()))

    def test_partial_write_rolls_back_the_file_that_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            path, content, modified = self.existing_profile(root)
            original = Path.open
            count = 0

            @contextlib.contextmanager
            def failing_open(target: Path, mode="r", *args, **kwargs):
                nonlocal count
                with original(target, mode, *args, **kwargs) as handle:
                    if mode == "xb":
                        count += 1
                    if mode == "xb" and count == 5:
                        proxy = unittest.mock.Mock(wraps=handle)

                        def fail_write(data):
                            handle.write(data[:3])
                            raise RuntimeError("partial write failure")

                        proxy.write.side_effect = fail_write
                        yield proxy
                    else:
                        yield handle

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("partial write failure", err)
            self.assertEqual({"infra/app.json"}, files(root))
            self.assertEqual(content, path.read_bytes())
            self.assertEqual(modified, path.stat().st_mtime_ns)
            self.assertEqual([root / "infra"], list(root.iterdir()))

    def test_exclusive_create_collision_preserves_concurrent_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open
            collided = []
            count = 0

            def racing_open(target: Path, mode="r", *args, **kwargs):
                nonlocal count
                if mode == "xb":
                    count += 1
                    if count == 5:
                        with original(target, "wb") as handle:
                            handle.write(b"concurrent owner")
                        collided.append(target)
                return original(target, mode, *args, **kwargs)

            with unittest.mock.patch.object(Path, "open", racing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertEqual(1, len(collided))
            self.assertEqual(b"concurrent owner", collided[0].read_bytes())
            self.assertEqual({"infra/app.json", collided[0].relative_to(root).as_posix()}, files(root))

    def test_rollback_preserves_replacement_of_a_created_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open
            replaced = root / "infra/.gitignore"
            moved = Path(temporary) / "original-output"

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    if os.name == "nt":
                        with self.assertRaises(PermissionError):
                            replaced.rename(moved)
                        with self.assertRaises(PermissionError):
                            replaced.unlink()
                    else:
                        replaced.rename(moved)
                        with original(replaced, "wb") as handle:
                            handle.write(b"replacement owner")
                    raise OSError("metadata disk full")
                return original(target, mode, *args, **kwargs)

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            if os.name == "nt":
                self.assertFalse(replaced.exists())
                self.assertFalse(moved.exists())
                self.assertEqual({"infra/app.json"}, files(root))
                self.assertNotIn("retained paths:", err)
            else:
                self.assertEqual(b"replacement owner", replaced.read_bytes())
                self.assertIn(f"retained paths: {replaced}", err)
                self.assertEqual({"infra/app.json", "infra/.gitignore"}, files(root))

    def test_rollback_preserves_replacement_of_a_created_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open
            replaced = root / ".github"
            moved = Path(temporary) / "original-directory"

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    if os.name == "nt":
                        with self.assertRaises(PermissionError):
                            replaced.rename(moved)
                        with self.assertRaises(PermissionError):
                            (root / ".github/actions/build-workload").rmdir()
                    else:
                        replaced.rename(moved)
                        replaced.mkdir()
                    raise OSError("metadata disk full")
                return original(target, mode, *args, **kwargs)

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            if os.name == "nt":
                self.assertFalse(replaced.exists())
                self.assertFalse(moved.exists())
                self.assertNotIn("retained paths:", err)
            else:
                self.assertTrue(replaced.is_dir())
                self.assertEqual([], list(replaced.iterdir()))
                self.assertIn(f"retained paths: {replaced}", err)
            self.assertEqual({"infra/app.json"}, files(root))

    def test_rollback_retains_created_files_when_identity_is_unprovable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    raise OSError("metadata disk full")
                return original(target, mode, *args, **kwargs)

            with self.tracked_pins() as (pins, descriptors), unittest.mock.patch.object(
                Path, "open", failing_open
            ), unittest.mock.patch.object(scaffold, "created_identity", return_value=None):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assert_pins_released(pins, descriptors)
            retained = root / "infra/.gitignore"
            self.assertTrue(retained.is_file())
            self.assertIn(str(retained), err)
            self.assertIn("retained paths:", err)

    def test_rollback_preserves_concurrent_unrelated_files_and_their_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            original = Path.open
            foreign = []
            count = 0

            def failing_open(target: Path, mode="r", *args, **kwargs):
                nonlocal count
                if mode == "xb":
                    count += 1
                    if count == 5:
                        path = target.parent / "foreign.txt"
                        with original(path, "wb") as handle:
                            handle.write(b"unrelated")
                        foreign.append(path)
                        raise OSError("disk full")
                return original(target, mode, *args, **kwargs)

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertEqual(b"unrelated", foreign[0].read_bytes())
            self.assertTrue(foreign[0].parent.is_dir())
            self.assertEqual({"infra/app.json", foreign[0].relative_to(root).as_posix()}, files(root))

    def test_rollback_preserves_preexisting_empty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            existing = root / ".github"
            existing.mkdir()
            original = Path.open

            def failing_open(target: Path, mode="r", *args, **kwargs):
                if mode == "xb" and target.name == ".scaffold.azure.json":
                    raise OSError("metadata disk full")
                return original(target, mode, *args, **kwargs)

            with unittest.mock.patch.object(Path, "open", failing_open):
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertTrue(existing.is_dir())
            self.assertEqual([], list(existing.iterdir()))
            self.assertEqual({"infra/app.json"}, files(root))

    def test_conflicting_outputs_and_metadata_are_all_reported_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            targets = [root / "infra/foundation/versions.tf", root / ".scaffold.azure.json"]
            for target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"existing")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with unittest.mock.patch.object(scaffold, "write_project") as writer:
                code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            writer.assert_not_called()
            for target in targets:
                self.assertIn(str(target), err)
            self.assertEqual(before, {path: path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_existing_directory_at_profile_target_is_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            (root / "infra/app.json").mkdir()
            external = write_profile(Path(temporary), self.profile, "external.json")
            code, _, err = self.scaffold(root, external)
            self.assertEqual(1, code, err)
            self.assertIn("already exists", err)
            self.assertTrue((root / "infra/app.json").is_dir())
            self.assertEqual(set(), files(root))

    def test_non_directory_output_ancestor_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            (root / ".github").write_bytes(b"existing")
            code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("not a directory", err)
            self.assertEqual(b"existing", (root / ".github").read_bytes())
            self.assertEqual({"infra/app.json", ".github"}, files(root))

    def test_dangling_output_symlink_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            link = root / ".scaffold.azure.json"
            self.symlink(link, Path(temporary) / "missing.json")
            code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("redirected output path", err)
            self.assertTrue(link.is_symlink())
            self.assertEqual({"infra/app.json"}, files(root))

    def test_profile_target_symlink_is_rejected_even_when_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            external = write_profile(Path(temporary), self.profile, "external.json")
            link = root / "infra/app.json"
            self.symlink(link, external)
            content = external.read_bytes()
            code, _, err = self.scaffold(root, external)
            self.assertEqual(1, code, err)
            self.assertIn("redirected output path", err)
            self.assertTrue(link.is_symlink())
            self.assertEqual(content, external.read_bytes())
            self.assertEqual({"infra/app.json"}, files(root))

    def test_output_ancestor_symlink_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            self.symlink(root / ".github", outside, directory=True)
            code, _, err = self.scaffold(root)
            self.assertEqual(1, code, err)
            self.assertIn("redirected output path", err)
            self.assertEqual([], list(outside.iterdir()))
            self.assertEqual({"infra/app.json"}, files(root))

    def test_project_root_symlink_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            link = Path(temporary) / "redirect"
            self.symlink(link, root, directory=True)
            code, _, err = self.scaffold(link)
            self.assertEqual(1, code, err)
            self.assertIn("redirected output path", err)
            self.assertEqual({"infra/app.json"}, files(root))

    def test_project_ancestor_symlink_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            link = Path(temporary) / "redirect"
            self.symlink(link, Path(temporary), directory=True)
            code, _, err = self.scaffold(link / "project")
            self.assertEqual(1, code, err)
            self.assertIn("redirected output path", err)
            self.assertEqual({"infra/app.json"}, files(root))

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_output_ancestor_junction_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.profile)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            link = root / ".github"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                self.skipTest(f"junctions unavailable: {result.stderr}")
            try:
                code, _, err = self.scaffold(root)
                self.assertEqual(1, code, err)
                self.assertIn("redirected output path", err)
                self.assertEqual([], list(outside.iterdir()))
                self.assertEqual({"infra/app.json"}, files(root))
            finally:
                link.rmdir()

    def test_identical_profile_is_omitted_from_dry_run_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            path, content, modified = self.existing_profile(root)
            external = Path(temporary) / "external.json"
            external.write_bytes(content)
            code, out, err = self.scaffold(root, external, dry_run=True)
            self.assertEqual(0, code, err)
            self.assertNotIn(str(path), out.splitlines())
            self.assertEqual({"infra/app.json"}, files(root))
            self.assertEqual(content, path.read_bytes())
            self.assertEqual(modified, path.stat().st_mtime_ns)


class GenerationTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            before = files(root)
            code, out, err = run(["new", "--into", str(root), "--dry-run", "--no-input"])
            self.assertEqual(0, code, err)
            self.assertIn("http_host", out)
            self.assertIn("workloads.tf", out)
            self.assertEqual(before, files(root))

    def test_empty_workloads_file_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            self.assertEqual(EMPTY_WORKLOADS_FILES, files(root))
            metadata = json.loads((root / ".scaffold.azure.json").read_text(encoding="utf-8"))
            self.assertEqual("azure", metadata["package"])
            self.assertEqual("scaffold", metadata["skill"])
            self.assertEqual("infra/app.json", metadata["profile"])
            self.assertIn("profile_sha256", metadata)

    def test_sandbox_profile_file_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, SANDBOX_PROFILE)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            self.assertEqual(SANDBOX_FILES, files(root))

    def test_every_placeholder_is_resolved_and_files_are_lf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, SANDBOX_PROFILE)
            run(["new", "--into", str(root), "--no-input"])
            for path in root.rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    self.assertNotIn(b"\r\n", data, path)
                    self.assertIsNone(scaffold.PLACEHOLDER.search(data.decode("utf-8")), path)

    def test_scaffold_source_has_no_dotnet_or_company_specific_values(self) -> None:
        skill_dir = SCRIPT.parent.parent
        paths = list(skill_dir.rglob("*.tf.in")) + list(skill_dir.rglob("*.py")) + [SCRIPT]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Npgsql", text, path)
            self.assertNotIn("ConnectionStrings", text, path)
            self.assertNotIn(".NET", text, path)


class OptionsSchemaTests(unittest.TestCase):
    def test_schema_prints_only_json(self) -> None:
        code, out, err = run(["options", "--schema"])
        self.assertEqual(0, code, err)
        self.assertEqual("", err)
        schema = json.loads(out)
        self.assertEqual(
            {"type": "enum", "allowed": ["container-apps", "app-service"], "default": "container-apps",
             "description": schema["http_host"]["description"]},
            schema["http_host"],
        )
        self.assertNotIn("allowed", schema["key_vault"])


class VnetIntegrationTests(unittest.TestCase):
    def test_app_service_gets_a_delegated_subnet_and_vnet_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "http",
                        "port": 8080,
                        "probes": {"liveness": "/a", "readiness": "/b"},
                        "runtime": {"name": "dotnet", "version": "10.0"},
                        "build": {"package": "echo build"},
                    }
                ],
            }
            write_profile(root, profile)
            code, _, err = run(
                ["new", "--into", str(root), "--set", "http_host=app-service", "--set", "vnet=true", "--no-input"]
            )
            self.assertEqual(0, code, err)
            network_tf = (root / "infra/foundation/network.tf").read_text(encoding="utf-8")
            self.assertIn('resource "azurerm_subnet" "app"', network_tf)
            self.assertIn('"Microsoft.Web/serverFarms"', network_tf)
            data_tf = (root / "infra/instance/data.tf").read_text(encoding="utf-8")
            self.assertIn('data "azurerm_subnet" "app"', data_tf)
            app_service_tf = (root / "infra/instance/app-service.tf").read_text(encoding="utf-8")
            self.assertIn("virtual_network_subnet_id = data.azurerm_subnet.app.id", app_service_tf)
            self.assertRegex(app_service_tf, r"vnet_route_all_enabled\s*=\s*true")

    def test_app_service_without_vnet_has_no_subnet_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "http",
                        "port": 8080,
                        "probes": {"liveness": "/a", "readiness": "/b"},
                        "runtime": {"name": "dotnet", "version": "10.0"},
                        "build": {"package": "echo build"},
                    }
                ],
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(0, code, err)
            app_service_tf = (root / "infra/instance/app-service.tf").read_text(encoding="utf-8")
            self.assertNotIn("virtual_network_subnet_id", app_service_tf)
            self.assertNotIn("vnet_route_all_enabled", app_service_tf)

    def test_flex_consumption_gets_a_delegated_subnet_and_vnet_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "fn",
                        "kind": "functions",
                        "runtime": {"name": "dotnet-isolated", "version": "10.0"},
                        "build": {"package": "echo build"},
                    }
                ],
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--set", "vnet=true", "--no-input"])
            self.assertEqual(0, code, err)
            network_tf = (root / "infra/foundation/network.tf").read_text(encoding="utf-8")
            self.assertIn('resource "azurerm_subnet" "functions"', network_tf)
            self.assertIn('"Microsoft.App/environments"', network_tf)
            flex_tf = (root / "infra/instance/flex-consumption.tf").read_text(encoding="utf-8")
            self.assertIn("virtual_network_subnet_id = data.azurerm_subnet.functions.id", flex_tf)


class StickySettingsTests(unittest.TestCase):
    def test_sticky_on_background_workload_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {"id": "worker", "kind": "background", "build": {"image": "x"}, "sticky": ["FOO"]}
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("only valid on an App Service host", err)

    def test_sticky_on_http_container_apps_host_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "/a", "readiness": "/b"},
                    "build": {"image": "x"},
                    "settings": {"FOO": "bar"},
                    "sticky": ["FOO"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("only valid on an App Service host", err)

    def test_unknown_sticky_name_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "/a", "readiness": "/b"},
                    "runtime": {"name": "dotnet", "version": "10.0"},
                    "build": {"package": "x"},
                    "sticky": ["NOPE"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("'NOPE' is not a key of this workload's effective settings", err)

    def test_sticky_must_be_a_list_of_strings_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 80,
                    "probes": {"liveness": "/a", "readiness": "/b"},
                    "runtime": {"name": "dotnet", "version": "10.0"},
                    "build": {"package": "x"},
                    "sticky": "FOO",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("sticky: must be a list of strings", err)

    def app_service_profile(self, sticky: object = None) -> dict:
        workload = {
            "id": "api",
            "kind": "http",
            "port": 80,
            "probes": {"liveness": "/a", "readiness": "/b"},
            "runtime": {"name": "dotnet", "version": "10.0"},
            "build": {"package": "echo build"},
            "settings": {"WORKLOAD_ONLY": "x"},
        }
        if sticky is not None:
            workload["sticky"] = sticky
        return {
            "schema_version": 1,
            "name": "demo",
            "workloads": [workload],
            "settings": {"SHARED": "y"},
        }

    def test_absent_sticky_renders_no_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.app_service_profile())
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(0, code, err)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            self.assertIn("sticky          = []", workloads_tf)
            app_service_tf = (root / "infra/instance/app-service.tf").read_text(encoding="utf-8")
            self.assertIn('dynamic "sticky_settings"', app_service_tf)
            self.assertIn("length(each.value.sticky) > 0", app_service_tf)

    def test_malformed_settings_with_sticky_exits_2_without_writes(self) -> None:
        for scope in ("profile", "workload"):
            for settings in (7, [["KEY"]]):
                with self.subTest(scope=scope, settings=settings), tempfile.TemporaryDirectory() as temporary:
                    root = new_project(temporary)
                    profile = self.app_service_profile(sticky=[])
                    target = profile if scope == "profile" else profile["workloads"][0]
                    target["settings"] = settings
                    write_profile(root, profile)
                    before = files(root)

                    code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])

                    self.assertEqual(2, code, err)
                    self.assertIn("settings: must be an object", err)
                    self.assertEqual(before, files(root))

    def test_empty_sticky_list_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.app_service_profile(sticky=[]))
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(0, code, err)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            self.assertIn("sticky          = []", workloads_tf)

    def test_sticky_names_may_come_from_profile_or_workload_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.app_service_profile(sticky=["WORKLOAD_ONLY", "SHARED"]))
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(0, code, err)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            self.assertIn('sticky          = ["WORKLOAD_ONLY", "SHARED"]', workloads_tf)

    def test_sticky_settings_block_renders_in_the_web_app_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, self.app_service_profile(sticky=["WORKLOAD_ONLY"]))
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(0, code, err)
            app_service_tf = (root / "infra/instance/app-service.tf").read_text(encoding="utf-8")
            main_resource = app_service_tf.split('resource "azurerm_linux_web_app_slot"')[0]
            self.assertIn('dynamic "sticky_settings"', main_resource)
            self.assertIn("app_setting_names = sticky_settings.value", main_resource)
            slot_resource = app_service_tf.split('resource "azurerm_linux_web_app_slot"')[1]
            self.assertNotIn("sticky_settings", slot_resource)


class KeyVaultOperatorAccessTests(unittest.TestCase):
    def test_current_principal_gets_secrets_officer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--set", "key_vault=true", "--no-input"])
            self.assertEqual(0, code, err)
            key_vault_tf = (root / "infra/foundation/key-vault.tf").read_text(encoding="utf-8")
            self.assertIn('role_definition_name = "Key Vault Secrets Officer"', key_vault_tf)
            self.assertIn("data.azurerm_client_config.current.object_id", key_vault_tf)

    def test_only_workloads_that_reference_keyvault_uri_get_secrets_user(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "background",
                        "build": {"image": "x"},
                        "settings": {"KV_URI": "{keyvault.uri}"},
                    },
                    {"id": "worker", "kind": "background", "build": {"image": "x"}},
                ],
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--set", "key_vault=true", "--no-input"])
            self.assertEqual(0, code, err)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            self.assertRegex(
                workloads_tf, re.compile(r"api = \{.*?uses_key_vault\s*=\s*true", re.DOTALL)
            )
            self.assertRegex(
                workloads_tf, re.compile(r"worker = \{.*?uses_key_vault\s*=\s*false", re.DOTALL)
            )
            self.assertIn("key_vault_workloads", workloads_tf)
            key_vault_tf = (root / "infra/instance/key-vault.tf").read_text(encoding="utf-8")
            self.assertIn("for_each = local.key_vault_workloads", key_vault_tf)


class BudgetTests(unittest.TestCase):
    def test_foundation_gets_a_resource_group_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            budget_tf = (root / "infra/foundation/budget.tf").read_text(encoding="utf-8")
            self.assertIn('resource "azurerm_consumption_budget_resource_group" "main"', budget_tf)
            self.assertIn("threshold      = 80", budget_tf)
            self.assertIn("threshold      = 100", budget_tf)
            self.assertIn('contact_roles  = ["Owner"]', budget_tf)
            variables_tf = (root / "infra/foundation/variables.tf").read_text(encoding="utf-8")
            self.assertIn('variable "budget_amount"', variables_tf)
            self.assertIn('variable "budget_start_date"', variables_tf)
            self.assertIn('variable "budget_alert_emails"', variables_tf)
            ci_tfvars = (root / "infra/env/ci.tfvars").read_text(encoding="utf-8")
            self.assertIn("budget_start_date", ci_tfvars)


SHA_REF_PATTERN = re.compile(r"uses:\s*([^\s@]+)@([0-9a-f]{40})\s*#\s*(v[0-9][\w.()\- ]*)")


class SchemasTests(unittest.TestCase):
    def test_default_schema_is_public_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [{"id": "api", "kind": "background", "build": {"image": "x"}}],
                "database": {"engine": "postgres", "migration": {"build": {"image": "x"}, "command": ["x"]}},
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            migration_tf = (root / "infra/instance/migration.tf").read_text(encoding="utf-8")
            self.assertIn('GRANT USAGE ON SCHEMA "public"', migration_tf)
            self.assertNotIn("CREATE SCHEMA", migration_tf)

    def test_two_schemas_render_grants_for_both(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [{"id": "api", "kind": "background", "build": {"image": "x"}}],
                "database": {
                    "engine": "postgres",
                    "schemas": ["public", "sandbox"],
                    "migration": {"build": {"image": "x"}, "command": ["x"]},
                },
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            migration_tf = (root / "infra/instance/migration.tf").read_text(encoding="utf-8")
            self.assertIn(
                'CREATE SCHEMA IF NOT EXISTS "sandbox" AUTHORIZATION "${azurerm_user_assigned_identity.migrator.name}"',
                migration_tf,
            )
            self.assertNotIn('CREATE SCHEMA IF NOT EXISTS "public"', migration_tf)
            for schema in ("public", "sandbox"):
                self.assertIn(f'GRANT USAGE ON SCHEMA "{schema}"', migration_tf)
                self.assertIn(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}"', migration_tf)
                self.assertIn(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}"', migration_tf)
                self.assertIn(f'IN SCHEMA "{schema}" GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES', migration_tf)
                self.assertIn(f'IN SCHEMA "{schema}" GRANT USAGE, SELECT ON SEQUENCES', migration_tf)

    def test_invalid_schema_name_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "database": {"engine": "postgres", "schemas": ["Not-Valid"]},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("profile.database.schemas", err)

    def test_duplicate_schema_name_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "database": {"engine": "postgres", "schemas": ["public", "public"]},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("duplicate schema", err)


class ServiceBusConsumeTests(unittest.TestCase):
    def consume_profile(self, consumes: object) -> dict:
        return {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "worker",
                    "kind": "background",
                    "build": {"image": "x"},
                    "messaging": {"consumes": consumes},
                }
            ],
        }

    def test_empty_types_list_exits_2(self) -> None:
        profile = self.consume_profile([{"topic": "orders", "types": []}])
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(
                ["new", "--into", str(root), "--set", "messaging=service-bus", "--set", "topics=orders", "--no-input"]
            )
            self.assertEqual(2, code)
            self.assertIn("types", err)

    def test_invalid_type_exits_2(self) -> None:
        profile = self.consume_profile([{"topic": "orders", "types": ["not valid!"]}])
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(
                ["new", "--into", str(root), "--set", "messaging=service-bus", "--set", "topics=orders", "--no-input"]
            )
            self.assertEqual(2, code)
            self.assertIn("types", err)

    def test_duplicate_type_exits_2(self) -> None:
        profile = self.consume_profile([{"topic": "orders", "types": ["created", "created"]}])
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(
                ["new", "--into", str(root), "--set", "messaging=service-bus", "--set", "topics=orders", "--no-input"]
            )
            self.assertEqual(2, code)
            self.assertIn("duplicate type", err)

    def test_per_subscription_filter_renders_from_its_own_types(self) -> None:
        profile = self.consume_profile([{"topic": "orders", "types": ["created", "updated"]}])
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(
                ["new", "--into", str(root), "--set", "messaging=service-bus", "--set", "topics=orders", "--no-input"]
            )
            self.assertEqual(0, code, err)
            service_bus_tf = (root / "infra/instance/service-bus.tf").read_text(encoding="utf-8")
            self.assertIn(
                'sql_filter      = "[type] IN (${join(", ", [for type in each.value.types : format("\'%s\'", type)])})"',
                service_bus_tf,
            )
            self.assertNotIn("service_bus_handled_types", service_bus_tf)
            workloads_tf = (root / "infra/instance/workloads.tf").read_text(encoding="utf-8")
            self.assertIn('types = ["created", "updated"]', workloads_tf)

    def test_default_rule_removal_step_present_only_with_consumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = self.consume_profile([{"topic": "orders", "types": ["created"]}])
            write_profile(root, profile)
            code, _, err = run(
                ["new", "--into", str(root), "--set", "messaging=service-bus", "--set", "topics=orders", "--no-input"]
            )
            self.assertEqual(0, code, err)
            deploy_environment = (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")
            self.assertIn("Remove the default Service Bus subscription rule", deploy_environment)
            self.assertIn("service_bus_default_rule_subscriptions", deploy_environment)

        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            deploy_environment = (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")
            self.assertNotIn("Remove the default Service Bus subscription rule", deploy_environment)


class VerifyFieldTests(unittest.TestCase):
    def test_verify_commands_rejects_actions_expression_exits_2(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "verify": ["echo ${{ secrets.FOO }}"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("must not contain", err)

    def test_verify_rejects_empty_string_exits_2(self) -> None:
        profile = {"schema_version": 1, "name": "demo", "workloads": [], "verify": [""]}
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(2, code)
            self.assertIn("profile.verify", err)

    def test_verify_job_runs_before_azure_jobs_in_pr_and_ci(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [],
                "verify": ["dotnet restore", "dotnet build"],
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
            ci_yml = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
            for text in (pr_yml, ci_yml):
                self.assertIn("Verify the app stack", text)
                self.assertIn("dotnet restore", text)
                self.assertIn("dotnet build", text)
                self.assertLess(text.index("verify:"), text.index("validate:"))

    def test_no_verify_job_when_profile_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
            self.assertNotIn("Verify the app stack", pr_yml)


class WorkflowsGateTests(unittest.TestCase):
    def test_workflows_false_writes_no_github_files_but_keeps_gitignore_and_prod_tfvars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--set", "workflows=false", "--no-input"])
            self.assertEqual(0, code, err)
            written = files(root)
            self.assertFalse(any(path.startswith(".github/") for path in written))
            self.assertIn("infra/.gitignore", written)
            self.assertIn("infra/env/prod.tfvars", written)


class GeneratedWorkflowConformanceTests(unittest.TestCase):
    def test_gitignore_excludes_state_but_keeps_the_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            gitignore = (root / "infra/.gitignore").read_text(encoding="utf-8")
            for pattern in (
                ".terraform/",
                "*.tfstate",
                "*.tfstate.*",
                "*.tfplan",
                "tfplan",
                "crash.log",
                "crash.*.log",
                "override.tf",
                "override.tf.json",
                "*_override.tf",
                "*_override.tf.json",
            ):
                self.assertIn(pattern, gitignore.splitlines())
            self.assertNotIn(".terraform.lock.hcl", gitignore)

    def test_every_remote_uses_is_pinned_to_a_full_sha_with_a_version_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, SANDBOX_PROFILE)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            found = False
            for path in (root / ".github").rglob("*.yml"):
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("uses:") or stripped.startswith("uses: ./"):
                        continue
                    self.assertRegex(stripped, SHA_REF_PATTERN, f"{path}: {stripped}")
                    found = True
            self.assertTrue(found)

    def test_every_checkout_persists_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, SANDBOX_PROFILE)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            checked = 0
            for path in (root / ".github").rglob("*.yml"):
                text = path.read_text(encoding="utf-8")
                for match in re.finditer(r"uses: actions/checkout@[0-9a-f]{40}.*\n\s*with:\n\s*persist-credentials: false", text):
                    checked += 1
            self.assertGreater(checked, 0)

    def test_top_level_permissions_are_contents_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, SANDBOX_PROFILE)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            checked = 0
            for path in (root / ".github/workflows").glob("*.yml"):
                text = path.read_text(encoding="utf-8")
                self.assertIn("permissions:\n  contents: read", text, path)
                checked += 1
            self.assertEqual(6, checked)

    def test_ci_complete_aggregator_in_pr_and_ci(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, SANDBOX_PROFILE)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            for name in ("pr.yml", "ci.yml"):
                text = (root / ".github/workflows" / name).read_text(encoding="utf-8")
                self.assertIn("ci-complete:", text)
                self.assertIn("if: always()", text)

    def test_ephemeral_id_is_not_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
            self.assertNotIn(": -4", pr_yml)
            self.assertIn('id="pr$digits"', pr_yml)
            self.assertIn("::error file=.github/workflows/pr.yml::", pr_yml)

    def test_preview_state_key_uses_pr_instance_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            deploy_environment = (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")
            self.assertIn("demo/instance.{0}.tfstate", deploy_environment)
            destroy_preview = (root / ".github/workflows/destroy-preview.yml").read_text(encoding="utf-8")
            self.assertIn("demo/instance.${{ inputs.ephemeral-id }}.tfstate", destroy_preview)

    def test_dependabot_configures_github_actions_ecosystem_weekly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            dependabot = (root / ".github/dependabot.yml").read_text(encoding="utf-8")
            self.assertIn("package-ecosystem: github-actions", dependabot)
            self.assertIn("interval: weekly", dependabot)
            self.assertIn("groups:\n      github-actions:\n        patterns:\n          - \"*\"", dependabot)

    def test_build_workload_resolves_the_pushed_image_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            action = (root / ".github/actions/build-workload/action.yml").read_text(encoding="utf-8")
            self.assertIn("docker inspect --format='{{index .RepoDigests 0}}'", action)
            self.assertIn('"$WORKLOAD_ID" "$digest_ref"', action)

    def test_app_service_deploy_checks_health_after_the_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "http",
                        "port": 8080,
                        "probes": {"liveness": "/health/live", "readiness": "/health/ready"},
                        "runtime": {"name": "dotnet", "version": "10.0"},
                        "build": {"package": "echo build"},
                    }
                ],
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--set", "http_host=app-service", "--no-input"])
            self.assertEqual(0, code, err)
            deploy_environment = (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")
            self.assertEqual(1, deploy_environment.count('READINESS_PATH: "/health/ready"'))
            self.assertEqual(2, deploy_environment.count('"https://{}$READINESS_PATH"'))
            self.assertIn("slot staging --target-slot production", deploy_environment)
            after_swap = deploy_environment.split("--slot staging --target-slot production")[1]
            self.assertIn("az webapp show", after_swap)
            self.assertIn("$READINESS_PATH", after_swap)

    def test_flex_consumption_deploy_waits_for_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "fn",
                        "kind": "functions",
                        "runtime": {"name": "dotnet-isolated", "version": "10.0"},
                        "build": {"package": "echo build"},
                    }
                ],
            }
            write_profile(root, profile)
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            deploy_environment = (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")
            self.assertIn("az functionapp show", deploy_environment)
            self.assertIn("--query state", deploy_environment)
            self.assertIn('"$state" = "Running"', deploy_environment)


def find_working_bash() -> str | None:
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates += [
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, timeout=10)
        except OSError:
            continue
        if result.returncode == 0 and result.stdout.strip() == "ok":
            return candidate
    return None


WORKING_BASH = find_working_bash()


def extract_block(text: str, step_name: str, block_marker: str) -> str:
    marker = f"- name: {step_name}\n"
    start = text.index(marker)
    run_start = text.index(block_marker, start) + len(block_marker)
    lines = text[run_start:].splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip(" "))
    body: list[str] = []
    for line in lines:
        if line.strip() == "":
            body.append("")
            continue
        if len(line) - len(line.lstrip(" ")) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body)


def extract_run_block(text: str, step_name: str) -> str:
    return extract_block(text, step_name, "run: |\n")


def extract_script_block(text: str, step_name: str) -> str:
    return extract_block(text, step_name, "script: |\n")


def write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


def to_posix_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def run_script_with_stubs(script: str, stubs: dict[str, str], env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    temporary = tempfile.mkdtemp(prefix="azure-scaffold-stubs-")
    try:
        bin_dir = Path(temporary) / "bin"
        bin_dir.mkdir()
        write_stub(bin_dir, "sleep", "exit 0")
        for stub_name, stub_body in stubs.items():
            write_stub(bin_dir, stub_name, stub_body)
        posix_bin_dir = to_posix_path(bin_dir)
        env = dict(os.environ)
        env["RESOURCE_GROUP"] = "stub-rg"
        env["RUNNER_TEMP"] = posix_bin_dir
        env.update(env_extra)
        stubbed_script = f'export PATH="{posix_bin_dir}:/usr/bin:/bin:$PATH"\n' + script
        return subprocess.run(
            [WORKING_BASH, "-c", stubbed_script], env=env, capture_output=True, text=True, timeout=120
        )
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


AZ_STUB = """
if [ "$1" = "webapp" ] && [ "$2" = "deploy" ]; then exit 0; fi
if [ "$1" = "webapp" ] && [ "$2" = "deployment" ] && [ "$3" = "slot" ] && [ "$4" = "show" ]; then echo "stub-host"; exit 0; fi
if [ "$1" = "webapp" ] && [ "$2" = "deployment" ] && [ "$3" = "slot" ] && [ "$4" = "swap" ]; then echo swap >> "$STUB_CALLS_FILE"; exit 0; fi
if [ "$1" = "webapp" ] && [ "$2" = "show" ]; then echo "stub-host"; exit 0; fi
if [ "$1" = "functionapp" ] && [ "$2" = "deploy" ]; then exit 0; fi
if [ "$1" = "functionapp" ] && [ "$2" = "show" ]; then echo "${STUB_STATE:-}"; exit 0; fi
if [ "$1" = "containerapp" ] && [ "$2" = "revision" ]; then echo "${STUB_HEALTH:-}"; exit 0; fi
exit 1
"""

CURL_STUB = """
if [ -n "${STUB_CALLS_FILE:-}" ] && grep -q swap "$STUB_CALLS_FILE" 2>/dev/null; then
  printf '%s' "${STUB_HTTP_CODE_POST:-${STUB_HTTP_CODE:-000}}"
else
  printf '%s' "${STUB_HTTP_CODE_PRE:-${STUB_HTTP_CODE:-000}}"
fi
"""


@unittest.skipUnless(WORKING_BASH or os.environ.get("CI") == "true", "a working bash is required to execute the generated deploy scripts")
class ReadinessLoopExecutionTests(unittest.TestCase):
    def deploy_environment_text(self, profile: dict, *sets: str) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, profile)
            args = ["new", "--into", str(root), "--no-input"]
            for item in sets:
                args += ["--set", item]
            code, _, err = run(args)
            self.assertEqual(0, code, err)
            return (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")

    def test_flex_consumption_wait_fails_then_succeeds(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "fn",
                    "kind": "functions",
                    "runtime": {"name": "dotnet-isolated", "version": "10.0"},
                    "build": {"package": "echo build"},
                }
            ],
        }
        text = self.deploy_environment_text(profile)
        script = extract_run_block(text, "Deploy fn")

        result = run_script_with_stubs(script, {"az": AZ_STUB}, {"STUB_STATE": "Stopped"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "::error file=.github/workflows/deploy-environment.yml::fn did not become Running within 5 minutes",
            result.stderr,
        )

        result = run_script_with_stubs(script, {"az": AZ_STUB}, {"STUB_STATE": "Running"})
        self.assertEqual(0, result.returncode, result.stderr)

    def test_container_apps_wait_fails_then_succeeds(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [{"id": "worker", "kind": "background", "build": {"image": "echo build"}}],
        }
        text = self.deploy_environment_text(profile)
        script = extract_run_block(text, "Wait for worker")

        result = run_script_with_stubs(script, {"az": AZ_STUB}, {"STUB_HEALTH": "Unhealthy"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "::error file=.github/workflows/deploy-environment.yml::worker did not become Healthy within 5 minutes",
            result.stderr,
        )

        result = run_script_with_stubs(script, {"az": AZ_STUB}, {"STUB_HEALTH": "Healthy"})
        self.assertEqual(0, result.returncode, result.stderr)

    def test_app_service_deploy_fails_before_swap_then_succeeds(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 8080,
                    "probes": {"liveness": "/health/live", "readiness": "/health/ready"},
                    "runtime": {"name": "dotnet", "version": "10.0"},
                    "build": {"package": "echo build"},
                }
            ],
        }
        text = self.deploy_environment_text(profile, "http_host=app-service")
        script = extract_run_block(text, "Deploy api")

        with tempfile.TemporaryDirectory() as temporary:
            calls_file = Path(temporary) / "calls"
            calls_file.write_text("", encoding="utf-8")
            result = run_script_with_stubs(
                script,
                {"az": AZ_STUB, "curl": CURL_STUB},
                {"STUB_HTTP_CODE": "500", "STUB_CALLS_FILE": str(calls_file), "READINESS_PATH": "/health/ready"},
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn(
                "::error file=.github/workflows/deploy-environment.yml::api staging slot did not become ready "
                "within 5 minutes",
                result.stderr,
            )
            self.assertEqual("", calls_file.read_text(encoding="utf-8"), "swap must not run before staging is ready")

        with tempfile.TemporaryDirectory() as temporary:
            calls_file = Path(temporary) / "calls"
            calls_file.write_text("", encoding="utf-8")
            result = run_script_with_stubs(
                script,
                {"az": AZ_STUB, "curl": CURL_STUB},
                {"STUB_HTTP_CODE": "200", "STUB_CALLS_FILE": str(calls_file), "READINESS_PATH": "/health/ready"},
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("swap", calls_file.read_text(encoding="utf-8"))

    def test_app_service_deploy_fails_if_production_never_becomes_ready_after_the_swap(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [
                {
                    "id": "api",
                    "kind": "http",
                    "port": 8080,
                    "probes": {"liveness": "/health/live", "readiness": "/health/ready"},
                    "runtime": {"name": "dotnet", "version": "10.0"},
                    "build": {"package": "echo build"},
                }
            ],
        }
        text = self.deploy_environment_text(profile, "http_host=app-service")
        script = extract_run_block(text, "Deploy api")

        with tempfile.TemporaryDirectory() as temporary:
            calls_file = Path(temporary) / "calls"
            calls_file.write_text("", encoding="utf-8")
            result = run_script_with_stubs(
                script,
                {"az": AZ_STUB, "curl": CURL_STUB},
                {
                    "STUB_HTTP_CODE_PRE": "200",
                    "STUB_HTTP_CODE_POST": "500",
                    "STUB_CALLS_FILE": str(calls_file),
                    "READINESS_PATH": "/health/ready",
                },
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn(
                "::error file=.github/workflows/deploy-environment.yml::api production slot did not become ready "
                "within 5 minutes",
                result.stderr,
            )
            self.assertIn("swap", calls_file.read_text(encoding="utf-8"))


NODE_HARNESS_TEMPLATE = """
const tagType = process.env.MOCK_TAG_TYPE;
const tagObjSha = process.env.MOCK_TAG_SHA;
const derefSha = process.env.MOCK_DEREF_SHA || "";
const latestSha = process.env.MOCK_LATEST_SHA || "";
process.env.TAG_NAME = "v1.2.3";

const github = {
  rest: {
    git: {
      getRef: async () => ({ data: { object: { sha: tagObjSha, type: tagType } } }),
      getTag: async () => ({ data: { object: { sha: derefSha } } }),
    },
    actions: {
      listWorkflowRuns: async () => ({
        data: { workflow_runs: latestSha ? [{ head_sha: latestSha }] : [] },
      }),
    },
  },
};
const context = { repo: { owner: "o", repo: "r" } };
let failedMessage = null;
const core = { setFailed: (msg) => { failedMessage = msg; } };

(async () => {
  try {
__SCRIPT__
  } finally {
    console.log(JSON.stringify({ failed: failedMessage }));
  }
})();
"""


def find_working_node() -> str | None:
    candidate = shutil.which("node")
    if not candidate:
        return None
    try:
        result = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=10)
    except OSError:
        return None
    return candidate if result.returncode == 0 else None


WORKING_NODE = find_working_node()


@unittest.skipUnless(WORKING_NODE or os.environ.get("CI") == "true", "node is required to execute the release-commit script with mocked APIs")
class ReleaseCommitScriptExecutionTests(unittest.TestCase):
    def script(self) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, {"schema_version": 1, "name": "demo", "workloads": []})
            code, _, err = run(["new", "--into", str(root), "--no-input"])
            self.assertEqual(0, code, err)
            prod_yml = (root / ".github/workflows/prod.yml").read_text(encoding="utf-8")
            return extract_script_block(prod_yml, "Compare the tag's commit against ci.yml's latest success on main")

    def run_harness(self, mock_env: dict[str, str]) -> dict:
        indented = "\n".join("  " + line if line else line for line in self.script().splitlines())
        harness = NODE_HARNESS_TEMPLATE.replace("__SCRIPT__", indented)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(harness)
            harness_path = handle.name
        try:
            env = dict(os.environ)
            env.update(mock_env)
            result = subprocess.run(
                [WORKING_NODE, harness_path], env=env, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            os.unlink(harness_path)

    def test_lightweight_tag_matching_the_latest_green_run_succeeds(self) -> None:
        outcome = self.run_harness(
            {"MOCK_TAG_TYPE": "commit", "MOCK_TAG_SHA": "abc123", "MOCK_LATEST_SHA": "abc123"}
        )
        self.assertIsNone(outcome["failed"])

    def test_lightweight_tag_mismatching_the_latest_green_run_fails(self) -> None:
        outcome = self.run_harness(
            {"MOCK_TAG_TYPE": "commit", "MOCK_TAG_SHA": "abc123", "MOCK_LATEST_SHA": "def456"}
        )
        self.assertIn("abc123", outcome["failed"])
        self.assertIn("def456", outcome["failed"])

    def test_annotated_tag_is_dereferenced_before_comparison(self) -> None:
        outcome = self.run_harness(
            {
                "MOCK_TAG_TYPE": "tag",
                "MOCK_TAG_SHA": "tag-object-sha",
                "MOCK_DEREF_SHA": "abc123",
                "MOCK_LATEST_SHA": "abc123",
            }
        )
        self.assertIsNone(outcome["failed"])

    def test_annotated_tag_mismatching_the_dereferenced_commit_fails(self) -> None:
        outcome = self.run_harness(
            {
                "MOCK_TAG_TYPE": "tag",
                "MOCK_TAG_SHA": "tag-object-sha",
                "MOCK_DEREF_SHA": "abc123",
                "MOCK_LATEST_SHA": "def456",
            }
        )
        self.assertIn("abc123", outcome["failed"])
        self.assertNotIn("tag-object-sha", outcome["failed"])

    def test_no_successful_ci_run_fails(self) -> None:
        outcome = self.run_harness({"MOCK_TAG_TYPE": "commit", "MOCK_TAG_SHA": "abc123", "MOCK_LATEST_SHA": ""})
        self.assertEqual("no successful ci.yml run found on main", outcome["failed"])


class ErrorsAndDomainDesignTests(unittest.TestCase):
    def generated(self, profile: dict | None = None, *sets: str) -> Path:
        self._temporary = tempfile.TemporaryDirectory()
        root = new_project(self._temporary.name)
        write_profile(root, profile or {"schema_version": 1, "name": "demo", "workloads": []})
        args = ["new", "--into", str(root), "--no-input"]
        for item in sets:
            args += ["--set", item]
        code, _, err = run(args)
        self.assertEqual(0, code, err)
        return root

    def tearDown(self) -> None:
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()

    def test_pr_workflow_has_the_verification_concurrency_group(self) -> None:
        root = self.generated()
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        self.assertIn(
            "concurrency:\n  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}\n"
            "  cancel-in-progress: true",
            pr_yml,
        )
        self.assertIn("types: [opened, synchronize, reopened, labeled]", pr_yml)

    def test_ci_and_prod_use_fixed_concurrency_groups(self) -> None:
        root = self.generated()
        ci_yml = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:\n  group: ci\n  cancel-in-progress: false", ci_yml)
        prod_yml = (root / ".github/workflows/prod.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:\n  group: prod\n  cancel-in-progress: false", prod_yml)

    def test_prod_triggers_only_on_semver_tags(self) -> None:
        root = self.generated()
        prod_yml = (root / ".github/workflows/prod.yml").read_text(encoding="utf-8")
        self.assertIn('- "v[0-9]+.[0-9]+.[0-9]+"', prod_yml)
        self.assertNotIn('"v*"', prod_yml)

    def test_assert_release_commit_job_gates_the_deploy(self) -> None:
        root = self.generated()
        prod_yml = (root / ".github/workflows/prod.yml").read_text(encoding="utf-8")
        self.assertIn("assert-release-commit:", prod_yml)
        self.assertIn("uses: actions/github-script@", prod_yml)
        self.assertIn('workflow_id: "ci.yml"', prod_yml)
        self.assertIn('branch: "main"', prod_yml)
        self.assertIn('status: "success"', prod_yml)
        self.assertIn("core.setFailed", prod_yml)
        job_text = prod_yml.split("assert-release-commit:")[1].split("\n  validate:")[0]
        self.assertIn("actions: read", job_text)
        self.assertNotIn("id-token: write", job_text)
        self.assertIn("needs: [assert-release-commit, validate]", prod_yml)
        self.assertIn("needs.assert-release-commit.result == 'success'", prod_yml)

    def test_preview_apply_and_destroy_share_the_same_concurrency_group(self) -> None:
        root = self.generated()
        deploy_environment = (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")
        self.assertIn("group: ${{ inputs.ephemeral-id != '' && format('preview-{0}', inputs.ephemeral-id)", deploy_environment)
        destroy_preview = (root / ".github/workflows/destroy-preview.yml").read_text(encoding="utf-8")
        self.assertIn("group: preview-${{ inputs.ephemeral-id }}", destroy_preview)

    def test_break_preview_lock_job_runs_only_when_cancelled(self) -> None:
        root = self.generated()
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        self.assertIn("break-preview-lock:", pr_yml)
        self.assertIn(
            "if: ${{ cancelled() && github.event.pull_request.head.repo.full_name == github.repository }}", pr_yml
        )
        self.assertIn("az storage blob lease break", pr_yml)
        self.assertIn("--auth-mode login", pr_yml)
        self.assertIn("id-token: write", pr_yml.split("break-preview-lock:")[1].split("steps:")[0])

    def test_every_runs_on_job_sets_timeout_minutes(self) -> None:
        root = self.generated(SANDBOX_PROFILE)
        for path in sorted((root / ".github/workflows").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            jobs_text = text.split("\njobs:\n", 1)[1]
            jobs = re.split(r"\n  (?=[a-z][a-z0-9-]*:)", "\n" + jobs_text)
            for job in jobs:
                if not job.strip():
                    continue
                if re.search(r"\n {4}uses: \./\.github/workflows/", job):
                    continue
                self.assertIn("timeout-minutes:", job, f"{path}: {job.splitlines()[0]}")

    def test_deploy_dependent_jobs_use_cancelled_and_result_pattern(self) -> None:
        root = self.generated()
        ci_yml = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("if: ${{ !cancelled() && needs.validate.result == 'success' && vars.AZURE_FOUNDATION_READY == 'true' }}", ci_yml)
        prod_yml = (root / ".github/workflows/prod.yml").read_text(encoding="utf-8")
        self.assertIn(
            "if: ${{ !cancelled() && needs.assert-release-commit.result == 'success' "
            "&& needs.validate.result == 'success' }}",
            prod_yml,
        )
        pr_closed = (root / ".github/workflows/pr-closed.yml").read_text(encoding="utf-8")
        self.assertIn("!cancelled() && needs.compute-preview-id.result == 'success'", pr_closed)

    def test_annotations_use_error_file_format(self) -> None:
        root = self.generated()
        for name in ("pr.yml", "ci.yml"):
            text = (root / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn(f"::error file=.github/workflows/{name}::", text)

    def test_environments_are_ci_preview_prod(self) -> None:
        root = self.generated()
        ci_yml = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("environment: ci", ci_yml)
        deploy_environment = (root / ".github/workflows/deploy-environment.yml").read_text(encoding="utf-8")
        self.assertIn("environment: ${{ inputs.environment }}", deploy_environment)
        destroy_preview = (root / ".github/workflows/destroy-preview.yml").read_text(encoding="utf-8")
        self.assertIn("environment: preview", destroy_preview)
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        self.assertIn("environment: preview", pr_yml)
        prod_yml = (root / ".github/workflows/prod.yml").read_text(encoding="utf-8")
        self.assertIn("environment: prod", prod_yml)

    def test_no_dev_remnant_names_the_old_environment(self) -> None:
        root = self.generated(SANDBOX_PROFILE)
        self.assertTrue((root / "infra/env/ci.tfvars").is_file())
        self.assertFalse((root / "infra/env/dev.tfvars").exists())
        ci_tfvars = (root / "infra/env/ci.tfvars").read_text(encoding="utf-8")
        self.assertIn('env    = "ci"', ci_tfvars)
        for path in (root / ".github").rglob("*.yml"):
            text = path.read_text(encoding="utf-8").replace("/dev/null", "")
            self.assertNotRegex(text, r"\bdev\b", path)

    def test_preview_requires_the_deploy_preview_label_and_no_var(self) -> None:
        root = self.generated()
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'deploy-preview')", pr_yml)
        self.assertNotIn("AZURE_PREVIEW_ENABLED", pr_yml)
        pr_closed = (root / ".github/workflows/pr-closed.yml").read_text(encoding="utf-8")
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'deploy-preview')", pr_closed)
        self.assertNotIn("AZURE_PREVIEW_ENABLED", pr_closed)

    def test_fork_safety_check_on_every_signing_in_pr_job(self) -> None:
        root = self.generated()
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        self.assertEqual(
            3, pr_yml.count("github.event.pull_request.head.repo.full_name == github.repository")
        )
        pr_closed = (root / ".github/workflows/pr-closed.yml").read_text(encoding="utf-8")
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", pr_closed)

    def test_compute_preview_id_job_has_no_checkout(self) -> None:
        root = self.generated()
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        job_text = pr_yml.split("compute-preview-id:")[1].split("\n  deploy-preview:")[0]
        self.assertNotIn("actions/checkout", job_text)

    def test_hcl_list_escapes_migration_command_items(self) -> None:
        profile = {
            "schema_version": 1,
            "name": "demo",
            "workloads": [],
            "database": {
                "engine": "postgres",
                "migration": {
                    "build": {"image": "x"},
                    "command": ['say "hi"', "back\\slash", "lit${eral"],
                },
            },
        }
        root = self.generated(profile)
        migration_tf = (root / "infra/instance/migration.tf").read_text(encoding="utf-8")
        self.assertIn(r'command = ["say \"hi\"", "back\\slash", "lit$${eral"]', migration_tf)

    @unittest.skipUnless(WORKING_BASH or os.environ.get("CI") == "true", "a working bash is required to execute the generated ephemeral-id script")
    def test_ephemeral_id_never_collides_ten_thousand_apart(self) -> None:
        root = self.generated()
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        script = extract_run_block(pr_yml, "Compute the ephemeral id")
        ids = {}
        for pr_number in (10001, 20001):
            with tempfile.TemporaryDirectory() as temporary:
                output_file = Path(temporary) / "output"
                output_file.write_text("", encoding="utf-8")
                result = subprocess.run(
                    [WORKING_BASH, "-c", script],
                    env={**os.environ, "PR_NUMBER": str(pr_number), "GITHUB_OUTPUT": str(output_file)},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn("exceeds the 6-character limit", result.stderr)
                ids[pr_number] = f"pr{pr_number}"
        self.assertNotEqual(ids[10001], ids[20001])

    @unittest.skipUnless(WORKING_BASH or os.environ.get("CI") == "true", "a working bash is required to execute the generated ephemeral-id script")
    def test_ephemeral_id_within_budget_succeeds(self) -> None:
        root = self.generated()
        pr_yml = (root / ".github/workflows/pr.yml").read_text(encoding="utf-8")
        script = extract_run_block(pr_yml, "Compute the ephemeral id")
        with tempfile.TemporaryDirectory() as temporary:
            output_file = Path(temporary) / "output"
            output_file.write_text("", encoding="utf-8")
            result = subprocess.run(
                [WORKING_BASH, "-c", script],
                env={**os.environ, "PR_NUMBER": "42", "GITHUB_OUTPUT": str(output_file)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("ephemeral-id=pr42\n", output_file.read_text(encoding="utf-8"))


def generate(root: Path, profile: dict, *sets: str) -> Path:
    write_profile(root, profile)
    args = ["new", "--into", str(root), "--no-input"]
    for item in sets:
        args += ["--set", item]
    code, _, err = run(args)
    assert code == 0, err
    return root


@unittest.skipUnless(AZURE_SCAFFOLD_TERRAFORM, "set AZURE_SCAFFOLD_TERRAFORM=1 to run the Terraform gate")
class TerraformGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("terraform") is None:
            raise AssertionError("AZURE_SCAFFOLD_TERRAFORM=1 but terraform is not on PATH")
        cls.cache_dir = tempfile.mkdtemp(prefix="azure-scaffold-tfcache-")
        cls.env = os.environ | {"TF_PLUGIN_CACHE_DIR": cls.cache_dir}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.cache_dir, ignore_errors=True)

    def gate(self, project: Path) -> None:
        for root in ("foundation", "instance"):
            directory = project / "infra" / root
            for command in (
                ["terraform", "fmt", "-check", "-recursive"],
                ["terraform", "init", "-backend=false", "-input=false"],
                ["terraform", "validate"],
            ):
                result = subprocess.run(command, cwd=directory, capture_output=True, text=True, env=self.env)
                self.assertEqual(
                    0, result.returncode, f"{project.name}/{root}: {' '.join(command)}\n{result.stdout}\n{result.stderr}"
                )

    def test_empty_workloads_profile_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            project = generate(root, {"schema_version": 1, "name": "demo", "workloads": []})
            self.gate(project)

    def test_sandbox_profile_passes_the_gate_with_public_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            project = generate(root, SANDBOX_PROFILE)
            self.gate(project)

    def test_sandbox_profile_passes_the_gate_with_private_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            write_profile(root, SANDBOX_PROFILE)
            code, _, err = run(["new", "--into", str(root), "--preset", "private", "--no-input"])
            self.assertEqual(0, code, err)
            self.gate(root)

    def test_sandbox_profile_passes_the_gate_with_app_service_and_container_apps_functions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = json.loads(json.dumps(SANDBOX_PROFILE))
            profile["workloads"][0]["runtime"] = {"name": "dotnet", "version": "10.0"}
            profile["workloads"][0]["build"] = {"package": "echo build-api"}
            profile["workloads"][0]["sticky"] = ["AZURE_CLIENT_ID"]
            profile["workloads"][2]["build"] = {"image": "echo build-fn"}
            project = generate(
                root, profile, "http_host=app-service", "functions_host=container-apps", "vnet=true", "key_vault=true"
            )
            self.gate(project)

    def test_messaging_case_with_servicebus_namespace_token_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = new_project(temporary)
            profile = {
                "schema_version": 1,
                "name": "demo",
                "workloads": [
                    {
                        "id": "api",
                        "kind": "http",
                        "port": 8080,
                        "probes": {"liveness": "/health/live", "readiness": "/health/ready"},
                        "build": {"image": "echo build-api"},
                        "settings": {"ServiceBus__Namespace": "{servicebus.namespace}"},
                        "messaging": {
                            "publishes": ["orders"],
                            "consumes": [{"topic": "orders", "types": ["order-placed"]}],
                        },
                    }
                ],
            }
            project = generate(root, profile, "messaging=service-bus", "topics=orders")
            self.gate(project)


if __name__ == "__main__":
    unittest.main()
