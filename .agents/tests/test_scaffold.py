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

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents/azure/utility/scaffold/scripts/scaffold.py"
SPEC = importlib.util.spec_from_file_location("azure_scaffold", SCRIPT)
scaffold = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scaffold)

FOUNDATION_FILES = {
    ".github/workflows/infra.yml",
    ".scaffold.azure.json",
    "infra/README.md",
    "infra/env/dev.tfvars",
    "infra/foundation/backend.tf",
    "infra/foundation/locals.tf",
    "infra/foundation/main.tf",
    "infra/foundation/outputs.tf",
    "infra/foundation/providers.tf",
    "infra/foundation/variables.tf",
    "infra/foundation/versions.tf",
    "infra/instance/backend.tf",
    "infra/instance/data.tf",
    "infra/instance/identity.tf",
    "infra/instance/locals.tf",
    "infra/instance/outputs.tf",
    "infra/instance/providers.tf",
    "infra/instance/variables.tf",
    "infra/instance/versions.tf",
}

WEB_API_FILES = FOUNDATION_FILES | {
    "infra/foundation/network.tf",
    "infra/foundation/container-apps.tf",
    "infra/foundation/postgres.tf",
    "infra/foundation/key-vault.tf",
    "infra/instance/container-apps.tf",
    "infra/instance/postgres.tf",
    "infra/instance/key-vault.tf",
}

AZURE_SCAFFOLD_TERRAFORM = os.environ.get("AZURE_SCAFFOLD_TERRAFORM") == "1"


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = scaffold.main(argv)
    return code, out.getvalue(), err.getvalue()


def files(project: Path) -> set[str]:
    return {path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()}


class PrecedenceTests(unittest.TestCase):
    def test_layers_apply_lowest_to_highest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.json"
            config.write_text(json.dumps({"host": "app-service", "vnet": True}), encoding="utf-8")
            code, out, err = run(
                [
                    "options",
                    "--preset",
                    "web-api",
                    "--config",
                    str(config),
                    "--set",
                    "name=demo",
                    "--set",
                    "vnet=false",
                ]
            )
            self.assertEqual(0, code, err)
            lines = dict(line.split("=", 1) for line in out.splitlines())
            self.assertIn("[default]", lines["azurerm_version"])
            self.assertIn("[preset]", lines["database"])
            self.assertIn("[config]", lines["host"])
            self.assertIn("[set]", lines["vnet"])
            self.assertIn("[set]", lines["name"])

    def test_name_flag_loses_to_set(self) -> None:
        code, out, err = run(["options", "--preset", "foundation", "--set", "name=fromset"])
        self.assertEqual(0, code, err)
        self.assertIn("name='fromset' [set]", out)


class ValidationTests(unittest.TestCase):
    def test_unknown_key_in_config_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.json"
            config.write_text(json.dumps({"nope": True}), encoding="utf-8")
            code, _, err = run(["options", "--config", str(config), "--set", "name=demo"])
            self.assertEqual(2, code)
            self.assertIn("nope", err)

    def test_bad_enum_exits_2(self) -> None:
        code, _, err = run(["options", "--set", "name=demo", "--set", "host=nope"])
        self.assertEqual(2, code)
        self.assertIn("host", err)

    def test_pattern_failure_exits_2(self) -> None:
        code, _, err = run(["options", "--set", "name=Not-Valid"])
        self.assertEqual(2, code)
        self.assertIn("name", err)

    def test_missing_name_exits_2(self) -> None:
        code, _, err = run(["options"])
        self.assertEqual(2, code)
        self.assertIn("name", err)

    def test_subscriptions_without_the_topic_exits_2(self) -> None:
        code, _, err = run(
            [
                "options",
                "--set",
                "name=demo",
                "--set",
                "messaging=service-bus",
                "--set",
                "topics=orders",
                "--set",
                "subscriptions=unknown",
            ]
        )
        self.assertEqual(2, code)
        self.assertIn("subscriptions", err)

    def test_topics_without_messaging_exits_2(self) -> None:
        code, _, err = run(["options", "--set", "name=demo", "--set", "topics=orders"])
        self.assertEqual(2, code)
        self.assertIn("topics", err)


class GenerationTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code, out, err = run(["new", "--name", "demo", "--destination", str(root), "--dry-run"])
            self.assertEqual(0, code, err)
            self.assertIn("demo", out)
            self.assertEqual([], list(root.iterdir()))

    def test_into_refuses_when_a_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "infra" / "foundation").mkdir(parents=True)
            (root / "infra" / "foundation" / "versions.tf").write_text("existing\n", encoding="utf-8")
            code, _, err = run(["new", "--into", str(root), "--set", "name=demo"])
            self.assertEqual(1, code)
            self.assertIn("versions.tf", err)
            self.assertEqual(["infra"], [path.name for path in root.iterdir()])
            self.assertEqual("existing\n", (root / "infra/foundation/versions.tf").read_text(encoding="utf-8"))

    def test_name_refuses_when_the_project_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "demo").mkdir()
            code, _, err = run(["new", "--name", "demo", "--destination", str(root)])
            self.assertEqual(1, code)
            self.assertIn("demo", err)

    def test_foundation_preset_file_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code, _, err = run(["new", "--name", "demo", "--destination", str(root), "--preset", "foundation"])
            self.assertEqual(0, code, err)
            self.assertEqual(FOUNDATION_FILES, files(root / "demo"))
            metadata = json.loads((root / "demo/.scaffold.azure.json").read_text(encoding="utf-8"))
            self.assertEqual("azure", metadata["package"])
            self.assertEqual("scaffold", metadata["skill"])
            self.assertEqual("foundation", metadata["preset"])

    def test_web_api_preset_file_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code, _, err = run(["new", "--name", "demo", "--destination", str(root), "--preset", "web-api"])
            self.assertEqual(0, code, err)
            self.assertEqual(WEB_API_FILES, files(root / "demo"))

    def test_every_placeholder_is_resolved_and_files_are_lf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run(["new", "--name", "demo", "--destination", str(root), "--preset", "web-api"])
            for path in (root / "demo").rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    self.assertNotIn(b"\r\n", data, path)
                    self.assertNotIn(b"__", data, path)


def generate(root: Path, name: str, *sets: str) -> Path:
    args = ["new", "--name", name, "--destination", str(root)]
    for item in sets:
        args += ["--set", item]
    code, _, err = run(args)
    assert code == 0, err
    return root / name


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
                self.assertEqual(0, result.returncode, f"{project.name}/{root}: {' '.join(command)}\n{result.stdout}\n{result.stderr}")

    def test_foundation_preset_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code, _, err = run(["new", "--name", "demo", "--destination", str(root), "--preset", "foundation"])
            self.assertEqual(0, code, err)
            self.gate(root / "demo")

    def test_web_api_preset_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code, _, err = run(["new", "--name", "webapi", "--destination", str(root), "--preset", "web-api"])
            self.assertEqual(0, code, err)
            self.gate(root / "webapi")

    def test_every_component_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = generate(
                root,
                "full",
                "host=container-apps",
                "database=postgres-flexible",
                "key_vault=true",
                "vnet=true",
                "messaging=service-bus",
                "topics=orders,profile-updated",
                "subscriptions=profile-updated",
            )
            self.gate(project)

    def test_app_service_host_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = generate(
                root,
                "appsvc",
                "host=app-service",
                "database=postgres-flexible",
                "key_vault=true",
                "vnet=true",
            )
            self.gate(project)


if __name__ == "__main__":
    unittest.main()
