from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
    ".github/workflows/infra.yml",
    ".scaffold.azure.json",
    "infra/README.md",
    "infra/app.json",
    "infra/env/dev.tfvars",
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
            original = Path.write_bytes
            calls = {"count": 0}

            def failing_write(path: Path, data: bytes) -> int:
                calls["count"] += 1
                if calls["count"] == 5:
                    raise OSError("disk full")
                return original(path, data)

            with unittest.mock.patch.object(Path, "write_bytes", failing_write):
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
            (root / "infra/README.md").unlink()
            (root / "infra/env/dev.tfvars").unlink()
            (root / ".github/workflows/infra.yml").unlink()
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
            dev_tfvars = (root / "infra/env/dev.tfvars").read_text(encoding="utf-8")
            self.assertIn("budget_start_date", dev_tfvars)


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
                        "messaging": {"publishes": ["orders"], "consumes": ["orders"]},
                    }
                ],
            }
            project = generate(root, profile, "messaging=service-bus", "topics=orders")
            self.gate(project)


if __name__ == "__main__":
    unittest.main()
