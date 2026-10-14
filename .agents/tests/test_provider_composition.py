from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from test_scaffold import ROOT, REAL_WRITER, SANDBOX_PROFILE, WRITER_REASON, scaffold


RUNTIME_SOURCE = ROOT / ".agents/azure/utility/scaffold/resources/azure_deploy.py"
SPEC = importlib.util.spec_from_file_location("azure_deploy", RUNTIME_SOURCE)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
SHA = "a" * 40
DIGEST = "b" * 64
ENV = {"SOURCE_SHA": SHA, "SOURCE_RUN_ID": "42", "DEPLOY_ENVIRONMENT": "prod", "PREVIEW_NUMBER": "", "TFSTATE_RESOURCE_GROUP": "state-rg", "TFSTATE_STORAGE_ACCOUNT": "stateaccount"}


def config_for(host: str = "container-apps", kind: str = "http") -> dict:
    return {"schema_version": 1, "name": "demo", "assets": {"api": {"host": host, "kind": kind, "command": "echo build", "readiness": "/ready"}}, "migration": False, "service_bus": False}


def artifact(directory: Path, config: dict) -> dict:
    directory.mkdir(exist_ok=True)
    assets = {}
    for identity, definition in config["assets"].items():
        image = definition["host"] == "container-apps"
        path = directory / (identity + (".tar" if image else ".zip"))
        asset = {"host": definition["host"], "path": path.name}
        if image:
            raw = json.dumps({"os": "linux", "architecture": "amd64"}).encode()
            image_id = "sha256:" + hashlib.sha256(raw).hexdigest()
            tag = "ciregistry.azurecr.io/demo-" + identity + ":" + SHA
            manifest = [{"Config": "config.json", "RepoTags": [tag], "Layers": ["layer.tar"]}]
            with tarfile.open(path, "w") as archive:
                for filename, data in (("config.json", raw), ("manifest.json", json.dumps(manifest).encode()), ("layer.tar", b"layer")):
                    info = tarfile.TarInfo(filename)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            asset.update(source_ref="ciregistry.azurecr.io/demo-" + identity + "@sha256:" + DIGEST, source_tag=tag, image_id=image_id, platform="linux/amd64")
        else:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("app.dll", "compiled application")
        asset["sha256"] = runtime.file_hash(path)
        assets[identity] = asset
    manifest = {"schema_version": 1, "name": "demo", "source_sha": SHA, "source_run_id": "42", "assets": assets}
    write_manifest(directory, manifest)
    return manifest


def write_manifest(directory: Path, manifest: dict) -> None:
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def plan_with_writer(writer, profile: dict | None = None, **kwargs):
    with patch.object(scaffold, "load_workflow_writer", return_value=writer):
        return scaffold.plan(scaffold.load_spec(), profile or {"schema_version": 1, "name": "demo", "workloads": []}, None, None, [], **kwargs)


class CompositionUnitTests(unittest.TestCase):
    def test_whole_pure_map_is_merged_and_prospective_overlay_wins(self):
        writer = Mock()
        writer.repository_metadata.return_value = {"global.json": "disk", "infra/global.json": "ancestor"}
        writer.render.return_value = {".github/workflows/ci.yml": "opaque workflow", ".scaffold.github-actions.json": "opaque metadata"}
        profile = copy.deepcopy(SANDBOX_PROFILE)
        profile["verify"] = ["first", "second"]
        with tempfile.TemporaryDirectory() as temporary:
            _, _, files, _ = plan_with_writer(writer, profile, project=Path(temporary), repository_files={"global.json": "prospective"})
        config, repository = writer.render.call_args.args
        self.assertEqual(["first", "second"], [step["env"]["VERIFY_COMMAND"] for step in config["verify"]["unit"]["steps"]])
        self.assertEqual([".", "infra/foundation", "infra/instance"], config["roots"])
        self.assertEqual("prospective", repository["global.json"])
        self.assertEqual("ancestor", repository["infra/global.json"])
        self.assertEqual(files["infra/deployment.json"], repository["infra/deployment.json"])
        self.assertIn(".scaffold.github-actions.json", files)
        writer.repository_metadata.assert_called_once()
        writer.cmd_new.assert_not_called()
        writer.write_files.assert_not_called()

    def test_no_verification_and_foundation_only_have_no_build(self):
        values = scaffold.resolve(scaffold.load_spec(), None, None, [])[0]
        for verify in (None, []):
            profile = {"schema_version": 1, "name": "demo", "workloads": []}
            if verify is not None:
                profile["verify"] = verify
            config = scaffold.provider_config(profile, values, scaffold.compute_derived(profile, values))
            self.assertEqual({}, config["verify"])
            self.assertNotIn("build", config)
            self.assertTrue(config["prod-release"])

    def test_provider_uses_catalog_names_and_step_scoped_arm_values(self):
        values = scaffold.resolve(scaffold.load_spec(), None, None, [])[0]
        config = scaffold.provider_config(SANDBOX_PROFILE, values, scaffold.compute_derived(SANDBOX_PROFILE, values))
        phases = config["provider"]["phases"]
        self.assertEqual("hashicorp/setup-terraform", phases["setup"]["steps"][0]["uses"])
        self.assertEqual("azure/login", phases["setup"]["steps"][1]["uses"])
        self.assertEqual("vars.AZURE_FOUNDATION_READY == 'true'", config["provider"]["ready"])
        self.assertEqual({"root": ["foundation", "instance"]}, phases["validate"]["matrix"])
        for phase in ("deploy", "readiness", "destroy", "recover", "plan"):
            self.assertNotIn("env", phases[phase])
            self.assertEqual("${{ vars.AZURE_CLIENT_ID }}", phases[phase]["steps"][0]["env"]["ARM_CLIENT_ID"])
        self.assertTrue(config["build"]["oidc"])
        self.assertNotIn("build", phases["deploy"]["steps"][0]["run"])
        uploads = phases["plan"]["steps"][1:]
        self.assertEqual(2, len({step["with"]["name"] for step in uploads}))
        self.assertTrue(all(step["with"]["overwrite"] is False for step in uploads))

    def test_missing_unusable_and_incomplete_writer_fail_before_outputs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"GITHUB_ACTIONS_SCAFFOLD": ""}):
            project = Path(temporary)
            (project / "infra").mkdir()
            (project / "infra/app.json").write_text('{"schema_version":1,"name":"demo","workloads":[]}')
            for source, message in ((None, "workflows require"), (project / "absent.py", "unusable github-actions"), (project / "incomplete.py", "must expose")):
                if source and source.name == "incomplete.py":
                    source.write_text("def render(config, repository_files): return {}\n")
                before = set(project.rglob("*"))
                args = ["new", "--into", str(project), "--no-input"] + (["--github-actions-scaffold", str(source)] if source else [])
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(1, scaffold.main(args))
                self.assertIn(message, error.getvalue())
                self.assertEqual(before, set(project.rglob("*")))

    def test_explicit_source_overrides_environment(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"GITHUB_ACTIONS_SCAFFOLD": "missing.py"}):
            source = Path(temporary) / "writer.py"
            source.write_text("def render(config, repository_files): return {}\ndef repository_metadata(project, config): return {}\n")
            self.assertEqual({}, scaffold.load_workflow_writer(source).render({}, {}))

    def test_workflows_false_needs_no_writer(self):
        with patch.object(scaffold, "load_workflow_writer", side_effect=AssertionError("writer must not be loaded")):
            _, _, files, _ = scaffold.plan(scaffold.load_spec(), {"schema_version": 1, "name": "demo", "workloads": []}, None, None, ["workflows=false"])
        self.assertFalse(any(path.startswith(".github/") for path in files))
        self.assertNotIn("infra/deployment.json", files)

    def test_conflicting_unsafe_and_nonmap_writer_outputs_are_rejected(self):
        for output in ({"infra/instance/workloads.tf": "collision"}, {".scaffold.azure.json": "metadata collision"}, {"../escape": "unsafe"}, {"/absolute": "unsafe"}, {"bad\\path": "unsafe"}, []):
            with self.subTest(output=output):
                writer = Mock()
                writer.render.return_value = output
                with self.assertRaises(scaffold.ScaffoldError):
                    plan_with_writer(writer)

    def test_combined_files_have_one_outer_transaction_and_owned_rollback(self):
        writer = Mock()
        writer.repository_metadata.return_value = {}
        writer.render.return_value = {".github/workflows/ci.yml": "shared", ".scaffold.github-actions.json": "shared metadata"}
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "infra").mkdir()
            profile = project / "infra/app.json"
            profile.write_text('{"schema_version":1,"name":"demo","workloads":[]}')
            original = Path.open
            def fail_metadata(path, mode="r", *args, **kwargs):
                if mode == "xb" and path.name == ".scaffold.azure.json":
                    self.assertTrue((project / ".scaffold.github-actions.json").exists())
                    self.assertTrue((project / "infra/scripts/azure_deploy.py").exists())
                    raise OSError("final metadata failure")
                return original(path, mode, *args, **kwargs)
            with patch.object(scaffold, "load_workflow_writer", return_value=writer), patch.object(Path, "open", fail_metadata), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, scaffold.main(["new", "--into", str(project), "--no-input"]))
            self.assertEqual([profile], [path for path in project.rglob("*") if path.is_file()])
        writer.cmd_new.assert_not_called()
        writer.write_files.assert_not_called()

    def test_existing_shared_metadata_is_a_conflict_before_any_write(self):
        writer = Mock()
        writer.repository_metadata.return_value = {}
        writer.render.return_value = {".scaffold.github-actions.json": "new"}
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "infra").mkdir()
            (project / "infra/app.json").write_text('{"schema_version":1,"name":"demo","workloads":[]}')
            (project / ".scaffold.github-actions.json").write_text("existing")
            original = Path.open
            modes = []
            def opened(path, mode="r", *args, **kwargs):
                modes.append(mode)
                return original(path, mode, *args, **kwargs)
            with patch.object(scaffold, "load_workflow_writer", return_value=writer), patch.object(Path, "open", opened), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, scaffold.main(["new", "--into", str(project), "--no-input"]))
            self.assertNotIn("xb", modes)

    def test_database_migration_assets_do_not_collide_with_valid_workload_ids(self):
        profile = copy.deepcopy(SANDBOX_PROFILE)
        profile["workloads"] = [{"id": identity, "kind": "background", "build": {"image": "echo build"}} for identity in ("migration", "migrator")]
        values = scaffold.resolve(scaffold.load_spec(), None, None, [])[0]
        self.assertEqual([], scaffold.validate_profile(profile, values))
        config = scaffold.deployment_config(profile, values, scaffold.compute_derived(profile, values))
        self.assertEqual({"migration", "migrator", "database-migration"}, set(config["assets"]))
        self.assertEqual("migration", config["assets"]["database-migration"]["kind"])
        self.assertIn('lookup(var.images, "database-migration",', scaffold.render_migration_tf(profile))

    def test_resource_outputs_reference_actual_resource_attributes(self):
        values = scaffold.resolve(scaffold.load_spec(), None, None, [])[0]
        profile = copy.deepcopy(SANDBOX_PROFILE)
        for http, functions in (("container-apps", "flex-consumption"), ("app-service", "container-apps")):
            with self.subTest(http=http, functions=functions):
                profile["workloads"][0]["runtime"] = {"name": "dotnet", "version": "10.0"}
                values.update(http_host=http, functions_host=functions)
                text = scaffold.render_resource_outputs(profile, values, scaffold.compute_derived(profile, values))
                for identity in ("api", "worker", "fn"):
                    self.assertIn('main["' + identity + '"].name', text)
                self.assertIn("value = azurerm_container_app_job.migrate.name", text)
                self.assertIn("value = data.azurerm_resource_group.main.name", text)
                if http == "app-service":
                    self.assertIn('azurerm_linux_web_app_slot.staging["api"].default_hostname', text)


@unittest.skipUnless(REAL_WRITER, WRITER_REASON)
class RealWriterIntegrationTests(unittest.TestCase):
    def test_shared_writer_owns_complete_graph_and_pin_catalog(self):
        profile = copy.deepcopy(SANDBOX_PROFILE)
        profile["verify"] = ["first", "second"]
        _, _, files, _ = scaffold.plan(scaffold.load_spec(), profile, None, None, [])
        writer = scaffold.load_workflow_writer()
        values = scaffold.resolve(scaffold.load_spec(), None, None, [])[0]
        _, documents = writer.render_documents(scaffold.provider_config(profile, values, scaffold.compute_derived(profile, values)), {})
        pr = documents[".github/workflows/pr.yml"]["jobs"]
        self.assertIn("verify-unit", pr)
        self.assertFalse(documents[".github/workflows/pr.yml"]["concurrency"]["cancel-in-progress"])
        self.assertEqual(["lint", "validate"], pr["plan"]["needs"])
        deploy = documents[".github/workflows/deploy-environment.yml"]["jobs"]["deploy"]
        self.assertIn("pull-requests", deploy["permissions"])
        self.assertTrue(any(step.get("uses", "").startswith("actions/download-artifact@") for step in deploy["steps"]))
        self.assertEqual(1, sum(step.get("id") == "artifact" for step in pr["build"]["steps"]))
        self.assertIn(".github/scripts/lint-workflows.py", files)
        for text in (files[path] for path in files if path.endswith(".yml")):
            for line in text.splitlines():
                if '"uses":' in line and "@" in line:
                    self.assertRegex(line, r"@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+")

    def test_repository_sdk_metadata_comes_from_declared_roots_and_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "infra").mkdir()
            (project / "global.json").write_text('{"sdk":{"version":"8.0.408"}}')
            (project / "infra/global.json").write_text('{"sdk":{"version":"9.0.203"}}')
            profile = {"schema_version": 1, "name": "demo", "workloads": [], "verify": ["echo arbitrary"]}
            _, _, files, _ = scaffold.plan(scaffold.load_spec(), profile, None, None, [], project=project, repository_files={"global.json": '{"sdk":{"version":"10.0.100"}}'})
        ci = files[".github/workflows/ci.yml"]
        self.assertIn('"global-json-file": "global.json"', ci)
        self.assertIn('"global-json-file": "infra/global.json"', ci)
        self.assertNotIn("8.0.408", ci)

    def test_prod_release_option_is_a_shared_writer_passthrough(self):
        _, _, files, _ = scaffold.plan(scaffold.load_spec(), {"schema_version": 1, "name": "demo", "workloads": []}, None, None, ["prod-release=false"])
        self.assertNotIn(".github/workflows/prod.yml", files)
        self.assertFalse(json.loads(files[".scaffold.github-actions.json"])["options"]["prod-release"])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "artifact"
        (Path(self.temporary.name) / "images.auto.tfvars.json").write_text(json.dumps({"images": {"api": "prodregistry.azurecr.io/demo-api@sha256:" + DIGEST}}))
        self.env = {**ENV, "RUNNER_TEMP": self.temporary.name, "ARTIFACT_DIRECTORY": str(self.directory)}
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.commands = patch.object(runtime, "command", side_effect=AssertionError("unstubbed command"))
        self.commands.start()
        self.addCleanup(self.commands.stop)
        self.sleep = patch.object(runtime.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_valid_image_and_package_artifacts(self):
        for host in ("container-apps", "app-service", "flex-consumption"):
            with self.subTest(host=host):
                config = config_for(host)
                expected = artifact(self.directory, config)
                self.assertEqual(expected, runtime.validate_artifact(config, self.directory))
                for path in self.directory.iterdir():
                    path.unlink()

    def test_manifest_rejects_source_name_version_and_asset_shape_mismatches(self):
        config = config_for()
        original = artifact(self.directory, config)
        for key, value in (("source_sha", "c" * 40), ("source_run_id", "99"), ("name", "other"), ("schema_version", True), ("assets", {}), ("assets", {**original["assets"], "unexpected": {}})):
            with self.subTest(key=key, value=value):
                manifest = copy.deepcopy(original)
                manifest[key] = value
                write_manifest(self.directory, manifest)
                with self.assertRaises(runtime.DeploymentError):
                    runtime.deploy(config)
                runtime.command.assert_not_called()

    def test_manifest_rejects_host_path_hash_digest_tag_image_and_platform_mismatches(self):
        config = config_for()
        original = artifact(self.directory, config)
        for key, value in (("host", "app-service"), ("path", "../api.tar"), ("path", "/api.tar"), ("path", "C:\\api.tar"), ("sha256", "0" * 64), ("source_ref", "ciregistry.azurecr.io/demo-api:latest"), ("source_ref", "other.azurecr.io/other@sha256:" + DIGEST), ("source_tag", "ciregistry.azurecr.io/demo-api:other"), ("image_id", "sha256:" + "0" * 64), ("platform", "linux/arm64")):
            with self.subTest(key=key):
                manifest = copy.deepcopy(original)
                manifest["assets"]["api"][key] = value
                write_manifest(self.directory, manifest)
                with self.assertRaises(runtime.DeploymentError):
                    runtime.deploy(config)
                runtime.command.assert_not_called()

    def test_missing_and_unexpected_artifact_files_fail(self):
        config = config_for()
        artifact(self.directory, config)
        (self.directory / "extra").write_text("unlisted")
        with self.assertRaisesRegex(runtime.DeploymentError, "unexpected"):
            runtime.validate_artifact(config, self.directory)
        (self.directory / "extra").unlink()
        (self.directory / "api.tar").unlink()
        with self.assertRaises(OSError):
            runtime.validate_artifact(config, self.directory)

    def test_unsafe_package_members_fail_even_with_matching_hash(self):
        config = config_for("app-service")
        manifest = artifact(self.directory, config)
        with zipfile.ZipFile(self.directory / "api.zip", "w") as archive:
            archive.writestr("../../escape", "bad")
        manifest["assets"]["api"]["sha256"] = runtime.file_hash(self.directory / "api.zip")
        write_manifest(self.directory, manifest)
        with self.assertRaisesRegex(runtime.DeploymentError, "unsafe package"):
            runtime.validate_artifact(config, self.directory)

    def test_multi_image_and_unsafe_archives_fail_with_matching_hash(self):
        config = config_for()
        for unsafe in (False, True):
            with self.subTest(unsafe=unsafe):
                manifest = artifact(self.directory, config)
                with tarfile.open(self.directory / "api.tar", "a") as archive:
                    info = tarfile.TarInfo("../escape" if unsafe else "manifest.json")
                    body = b"bad" if unsafe else b"[{},{}]"
                    info.size = len(body)
                    archive.addfile(info, io.BytesIO(body))
                manifest["assets"]["api"]["sha256"] = runtime.file_hash(self.directory / "api.tar")
                write_manifest(self.directory, manifest)
                with self.assertRaises(runtime.DeploymentError):
                    runtime.validate_artifact(config, self.directory)

    def test_unsafe_deployment_config_fails_before_commands(self):
        original = config_for()
        path = Path(self.temporary.name) / "config.json"
        for change in (lambda c: c.update(name="../escape"), lambda c: c["assets"]["api"].update(host="other"), lambda c: c["assets"]["api"].update(command="echo ${{ inputs.text }}"), lambda c: c["assets"]["api"].update(readiness="/$(bad)"), lambda c: c.update(migration=True), lambda c: c.update(unknown=True)):
            config = copy.deepcopy(original)
            change(config)
            path.write_text(json.dumps(config))
            with self.assertRaises(runtime.DeploymentError):
                runtime.load_config(path)

    def test_config_read_only_load_preserves_bytes_and_metadata(self):
        path = Path(self.temporary.name) / "deployment.json"
        path.write_text(json.dumps(config_for()))
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.assertEqual(config_for(), runtime.load_config(path))
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_deploy_rejects_artifact_before_foundation_or_instance_commands(self):
        config = config_for()
        manifest = artifact(self.directory, config)
        manifest["source_sha"] = "wrong"
        write_manifest(self.directory, manifest)
        with self.assertRaises(runtime.DeploymentError):
            runtime.deploy(config)
        runtime.command.assert_not_called()

    def test_cross_registry_promotion_loads_exact_image_and_checks_digest(self):
        config = config_for()
        manifest = artifact(self.directory, config)
        asset = manifest["assets"]["api"]
        target = "prodregistry.azurecr.io/demo-api:" + SHA
        local = {"Id": asset["image_id"], "Os": "linux", "Architecture": "amd64"}
        calls = []
        def stub(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("docker", "inspect"):
                return json.dumps([{**local, "RepoDigests": ["prodregistry.azurecr.io/demo-api@sha256:" + DIGEST]}])
            self.assertIn(args[:2], (("az", "acr"), ("docker", "load"), ("docker", "tag"), ("docker", "push")))
            return ""
        with patch.object(runtime, "command", side_effect=stub):
            images = runtime.promote(config, manifest, self.directory, "prodregistry.azurecr.io")
        self.assertEqual({"api": "prodregistry.azurecr.io/demo-api@sha256:" + DIGEST}, images)
        self.assertIn(("docker", "tag", asset["image_id"], target), calls)
        self.assertFalse(any("ciregistry" in " ".join(call) for call in calls))
        self.assertFalse(any(call[:2] in (("docker", "build"), ("docker", "pull")) for call in calls))

    def test_promotion_digest_and_loaded_image_mismatch_fail(self):
        config = config_for()
        manifest = artifact(self.directory, config)
        asset = manifest["assets"]["api"]
        for changed_id, digest in ((asset["image_id"], "0" * 64), ("sha256:" + "0" * 64, DIGEST)):
            with self.subTest(changed_id=changed_id):
                def stub(*args, **kwargs):
                    if args[:2] == ("docker", "inspect"):
                        return json.dumps([{"Id": changed_id, "Os": "linux", "Architecture": "amd64", "RepoDigests": ["prodregistry.azurecr.io/demo-api@sha256:" + digest]}])
                    return ""
                with patch.object(runtime, "command", side_effect=stub), self.assertRaises(runtime.DeploymentError):
                    runtime.promote(config, manifest, self.directory, "prodregistry.azurecr.io")

    def test_build_on_pr_reads_foundation_and_ci_can_converge(self):
        config = config_for("app-service")
        with patch.object(runtime, "foundation", return_value={}) as foundation:
            def build_stub(*args, **kwargs):
                self.assertEqual("bash", args[0])
                with zipfile.ZipFile(kwargs["env"]["PACKAGE"], "w") as archive:
                    archive.writestr("app.dll", "compiled")
                return ""
            with patch.object(runtime, "command", side_effect=build_stub):
                for number in ("42", ""):
                    with patch.dict(os.environ, {"PREVIEW_NUMBER": number}):
                        runtime.build(config)
                    foundation.assert_called_with(config, not number)
                    result = runtime.validate_artifact(config, Path(self.temporary.name) / "deployment")
                    self.assertEqual(SHA, result["source_sha"])
                    for path in (Path(self.temporary.name) / "deployment").iterdir():
                        path.unlink()
                    (Path(self.temporary.name) / "deployment").rmdir()

    def test_image_build_records_repo_digest_and_saves_one_archive(self):
        config = config_for()
        source = artifact(self.directory, config)
        asset = source["assets"]["api"]
        calls = []
        def stub(*args, **kwargs):
            calls.append(args)
            if args[0] == "bash":
                self.assertEqual("ciregistry.azurecr.io/demo-api:" + SHA, kwargs["env"]["IMAGE"])
            elif args[:2] == ("docker", "inspect"):
                return json.dumps([{"Id": asset["image_id"], "Os": "linux", "Architecture": "amd64", "RepoDigests": [asset["source_ref"]]}])
            elif args[:2] == ("docker", "save"):
                Path(args[3]).write_bytes((self.directory / "api.tar").read_bytes())
            elif args[:2] != ("az", "acr"):
                self.fail("unexpected command " + str(args))
            return ""
        with patch.object(runtime, "foundation", return_value={"registry_login_server": "ciregistry.azurecr.io"}), patch.object(runtime, "command", side_effect=stub):
            runtime.build(config)
        self.assertEqual(1, sum(call[:2] == ("docker", "save") for call in calls))
        built = json.loads((Path(self.temporary.name) / "deployment/manifest.json").read_text())
        self.assertEqual(asset["source_ref"], built["assets"]["api"]["source_ref"])

    def test_foundation_only_deploy_does_not_need_artifact(self):
        config = {**config_for(), "assets": {}}
        with patch.object(runtime, "foundation", return_value={}) as foundation, patch.object(runtime, "backend") as backend, patch.object(runtime, "apply") as apply, patch.object(runtime, "outputs", return_value={}), patch.object(runtime, "validate_artifact", side_effect=AssertionError("no artifact")), patch.object(runtime, "promote", side_effect=AssertionError("no promotion")):
            runtime.deploy(config)
        foundation.assert_called_once_with(config, True)
        backend.assert_called_once_with(config, "instance", "")
        self.assertEqual(1, apply.call_count)

    def test_invalid_source_identity_fails_before_build_outputs(self):
        for sha, run in (("bad", "42"), (SHA, "0"), (SHA, "42x")):
            with self.subTest(sha=sha, run=run), patch.dict(os.environ, {"SOURCE_SHA": sha, "SOURCE_RUN_ID": run}), self.assertRaises(runtime.DeploymentError):
                runtime.build(config_for())
            self.assertFalse((Path(self.temporary.name) / "deployment").exists())
        runtime.command.assert_not_called()

    def test_missing_or_ambiguous_repo_digest_fails(self):
        for refs in ([], ["ciregistry.azurecr.io/demo-api:latest"], ["other.azurecr.io/demo-api@sha256:" + DIGEST], ["ciregistry.azurecr.io/demo-api@sha256:" + DIGEST] * 2):
            with self.subTest(refs=refs), self.assertRaises(runtime.DeploymentError):
                runtime.pushed_ref({"RepoDigests": refs}, "ciregistry.azurecr.io/demo-api")

    def test_preview_identity_keeps_every_digit_and_rejects_over_budget(self):
        self.assertEqual("pr42", runtime.preview_id("42"))
        self.assertEqual("pr9999", runtime.preview_id("9999"))
        for number in ("10042", "0", "42x", "", "0042"):
            if number == "":
                self.assertEqual("", runtime.preview_id(number))
            else:
                with self.assertRaises(runtime.DeploymentError):
                    runtime.preview_id(number)

    def test_destroy_absent_state_does_not_initialize_or_destroy_any_root(self):
        with patch.dict(os.environ, {"PREVIEW_NUMBER": "42", "DEPLOY_ENVIRONMENT": "preview"}), patch.object(runtime, "command", return_value='{"exists":false}') as command:
            runtime.destroy(config_for())
        self.assertEqual(1, command.call_count)
        self.assertIn("demo/instance.pr42.tfstate", command.call_args.args)
        self.assertEqual(("az", "storage", "blob", "exists"), command.call_args.args[:4])

    def test_destroy_existing_state_uses_exact_key_and_only_instance(self):
        with patch.dict(os.environ, {"PREVIEW_NUMBER": "42", "DEPLOY_ENVIRONMENT": "preview"}), patch.object(runtime, "command", side_effect=['{"exists":true}', "", ""]) as command:
            runtime.destroy(config_for())
        calls = command.call_args_list
        self.assertEqual(("terraform", "init"), calls[1].args[:2])
        self.assertIn("-backend-config=key=demo/instance.pr42.tfstate", calls[1].args)
        self.assertEqual(("terraform", "destroy"), calls[2].args[:2])
        self.assertIn("-var=ephemeral_id=pr42", calls[2].args)
        self.assertTrue(all(call.kwargs.get("cwd", Path("infra/instance")) == Path("infra/instance") for call in calls[1:]))

    def test_state_query_auth_and_malformed_responses_fail_closed(self):
        for response in (subprocess.CalledProcessError(1, ["az"]), "not json", '{"exists":"false"}'):
            with self.subTest(response=response), patch.dict(os.environ, {"PREVIEW_NUMBER": "42"}), patch.object(runtime, "command", side_effect=response if isinstance(response, Exception) else None, return_value=response) as command:
                with self.assertRaises((runtime.DeploymentError, ValueError, subprocess.CalledProcessError)):
                    runtime.destroy(config_for())
                self.assertEqual(1, command.call_count)

    def test_recover_absent_and_unleased_are_idempotent_and_locked_breaks_exact_blob(self):
        for exists, locked in ((False, False), (True, False), (True, True)):
            with self.subTest(exists=exists, locked=locked):
                responses = [json.dumps({"exists": exists})]
                if exists:
                    responses.append(json.dumps({"properties": {"lease": {"status": "locked" if locked else "unlocked", "state": "leased" if locked else "available"}}}))
                if locked:
                    responses.append("")
                with patch.dict(os.environ, {"PREVIEW_NUMBER": "42"}), patch.object(runtime, "command", side_effect=responses) as command:
                    runtime.recover(config_for())
                self.assertEqual(1 + int(exists) + int(locked), command.call_count)
                if locked:
                    self.assertEqual(("az", "storage", "blob", "lease", "break"), command.call_args.args[:5])
                    self.assertIn("demo/instance.pr42.tfstate", command.call_args.args)
                    self.assertIn("--blob-name", command.call_args.args)
                    self.assertNotIn("--name", command.call_args.args)

    def test_servicebus_default_removal_is_idempotent_and_query_failures_fail(self):
        values = {"resource_group_name": "actual-rg", "service_bus_default_rule_subscriptions": json.dumps([{"namespace": "actual-namespace", "topic": "orders", "subscription": "pr42-worker-orders"}])}
        for rules in ([{"name": "$Default"}, {"name": "handled-types"}], [{"name": "handled-types"}]):
            responses = [json.dumps(rules)] + ([""] if len(rules) == 2 else [])
            with patch.object(runtime, "command", side_effect=responses) as command:
                runtime.remove_default_rules(values)
            self.assertEqual(len(responses), command.call_count)
            self.assertIn("actual-namespace", command.call_args.args)
            self.assertIn("pr42-worker-orders", command.call_args.args)
            if len(rules) == 2: self.assertIn("$Default", command.call_args.args)
        with patch.object(runtime, "command", side_effect=subprocess.CalledProcessError(1, ["az"])), self.assertRaises(subprocess.CalledProcessError):
            runtime.remove_default_rules(values)

    def test_unknown_lease_and_lease_query_errors_fail(self):
        for response in ('{"properties":{"lease":{}}}', subprocess.CalledProcessError(1, ["az"])):
            with patch.dict(os.environ, {"PREVIEW_NUMBER": "42"}), patch.object(runtime, "command", side_effect=['{"exists":true}', response]) as command:
                with self.assertRaises((runtime.DeploymentError, subprocess.CalledProcessError)):
                    runtime.recover(config_for())
                self.assertEqual(2, command.call_count)

    def test_validation_and_plan_use_backendless_gate_and_real_backend(self):
        with patch.dict(os.environ, {"TF_ROOT": "foundation", "DEPLOY_ENVIRONMENT": "preview"}), patch.object(runtime, "command", return_value="plan text") as command:
            runtime.validate()
            self.assertEqual([("terraform", "fmt", "-check", "-recursive"), ("terraform", "init", "-input=false", "-backend=false"), ("terraform", "validate")], [call.args for call in command.call_args_list])
            command.reset_mock()
            with contextlib.redirect_stdout(io.StringIO()):
                runtime.plan(config_for())
            self.assertIn("-backend-config=key=demo/foundation.tfstate", command.call_args_list[0].args)
            self.assertIn("-var-file=../env/ci.tfvars", command.call_args.args)
            self.assertEqual(Path(self.temporary.name) / "terraform-plan/foundation-plan.txt", command.call_args.kwargs["diagnostic"])

    def test_migration_uses_actual_job_and_exact_execution_and_fails_on_failure(self):
        for region in ("uks", "neu"):
            for environment, preview in (("ci", ""), ("prod", ""), ("ci", "-pr42")):
                name = region + "-" + environment + "-demo" + preview + "-migrate-caj"
                values = {"resource_group_name": "actual-rg", "migration_job_name": name}
                for status in ("Succeeded", "Failed", "Running"):
                    with self.subTest(name=name, status=status):
                        def stub(*args, **kwargs):
                            self.assertIn(name, args)
                            if args[:4] == ("az", "containerapp", "job", "start"):
                                return '{"name":"exact-execution"}'
                            self.assertIn("exact-execution", args)
                            return json.dumps({"properties": {"status": status}})
                        with patch.object(runtime, "command", side_effect=stub):
                            if status == "Succeeded":
                                runtime.migration(values)
                            else:
                                with self.assertRaises(runtime.DeploymentError):
                                    runtime.migration(values)

    def test_container_readiness_checks_latest_active_running_healthy_revision_and_traffic(self):
        config = config_for()
        values = {"resource_group_name": "actual-rg", "workload_targets": {"api": {"name": "neu-ci-demo-pr42-api-ca", "hostname": "actual.azurecontainerapps.io"}}}
        app = {"properties": {"latestRevisionName": "new", "latestReadyRevisionName": "new", "configuration": {"ingress": {"traffic": [{"revisionName": "new", "weight": 100}]}}}}
        revision = {"properties": {"active": True, "healthState": "Healthy", "runningState": "Running", "template": {"containers": [{"name": "main", "image": "prodregistry.azurecr.io/demo-api@sha256:" + DIGEST}]}}}
        for change in (None, "old", "inactive", "unhealthy", "stopped", "traffic", "http", "image"):
            current_app, current_revision = copy.deepcopy(app), copy.deepcopy(revision)
            if change == "old": current_app["properties"]["latestReadyRevisionName"] = "old"
            if change == "inactive": current_revision["properties"]["active"] = False
            if change == "unhealthy": current_revision["properties"]["healthState"] = "Unhealthy"
            if change == "stopped": current_revision["properties"]["runningState"] = "Stopped"
            if change == "image": current_revision["properties"]["template"]["containers"][0]["image"] = "old-image"
            if change == "traffic": current_app["properties"]["configuration"]["ingress"]["traffic"][0]["weight"] = 0
            def stub(*args, **kwargs):
                self.assertIn("neu-ci-demo-pr42-api-ca", args)
                if args[:3] == ("az", "containerapp", "show"):
                    return json.dumps(current_app)
                self.assertIn("new", args)
                return json.dumps(current_revision)
            with self.subTest(change=change), patch.object(runtime, "outputs", return_value=values), patch.object(runtime, "command", side_effect=stub), patch.object(runtime, "http_ready", return_value=change != "http"):
                if change is None:
                    runtime.readiness(config)
                else:
                    with self.assertRaises(runtime.DeploymentError):
                        runtime.readiness(config)

    def test_background_revision_needs_running_health_without_http(self):
        config = config_for(kind="background")
        values = {"resource_group_name": "actual-rg", "workload_targets": {"api": {"name": "actual-worker"}}}
        responses = [json.dumps({"properties": {"latestRevisionName": "new", "latestReadyRevisionName": "new"}}), json.dumps({"properties": {"active": True, "healthState": "Healthy", "runningState": "Running", "template": {"containers": [{"name": "main", "image": "prodregistry.azurecr.io/demo-api@sha256:" + DIGEST}]}}})]
        with patch.object(runtime, "outputs", return_value=values), patch.object(runtime, "command", side_effect=responses), patch.object(runtime, "http_ready", side_effect=AssertionError("background has no ingress")):
            runtime.readiness(config)

    def test_function_readiness_requires_running_and_actual_host(self):
        config = config_for("flex-consumption", "functions")
        for state, host_ready in (("Running", True), ("Stopped", True), ("Running", False)):
            values = {"resource_group_name": "actual-rg", "workload_targets": {"api": {"name": "neu-prod-demo-api-func", "hostname": "actual.azurewebsites.net"}}}
            with self.subTest(state=state, host_ready=host_ready), patch.object(runtime, "outputs", return_value=values), patch.object(runtime, "command", return_value=json.dumps({"properties": {"state": state, "defaultHostName": "actual.azurewebsites.net"}})) as command, patch.object(runtime, "http_ready", return_value=host_ready) as http:
                if state == "Running" and host_ready:
                    runtime.readiness(config)
                    http.assert_called_with("actual.azurewebsites.net", "/ready")
                else:
                    with self.assertRaises(runtime.DeploymentError): runtime.readiness(config)
                self.assertIn("neu-prod-demo-api-func", command.call_args.args)

    def test_flex_compiled_zip_uses_onedeploy_without_build_commands(self):
        config = config_for("flex-consumption", "functions")
        artifact(self.directory, config)
        values = {"resource_group_name": "actual-rg", "workload_targets": {"api": {"name": "neu-prod-demo-api-func", "hostname": "actual.azurewebsites.net"}}}
        with patch.object(runtime, "foundation", return_value={}), patch.object(runtime, "backend"), patch.object(runtime, "apply"), patch.object(runtime, "promote", return_value={}), patch.object(runtime, "outputs", return_value=values), patch.object(runtime, "command", return_value="") as command:
            runtime.deploy(config)
        command.assert_called_once_with("az", "functionapp", "deploy", "--name", "neu-prod-demo-api-func", "--resource-group", "actual-rg", "--src-path", str(self.directory / "api.zip"), "--type", "zip")

    def test_failed_plan_keeps_stdout_and_stderr_diagnostics_and_fails(self):
        self.commands.stop()
        result = subprocess.CompletedProcess(("terraform", "plan"), 1, stdout="partial plan\n", stderr="backend error\n")
        success = subprocess.CompletedProcess(("terraform", "init"), 0, stdout="", stderr="")
        with patch.dict(os.environ, {"TF_ROOT": "instance", "DEPLOY_ENVIRONMENT": "preview"}), patch.object(runtime.subprocess, "run", side_effect=[success, result]) as process, self.assertRaises(subprocess.CalledProcessError) as caught:
            runtime.plan(config_for())
        self.assertEqual("partial plan\n", caught.exception.output)
        self.assertEqual("backend error\n", caught.exception.stderr)
        self.assertEqual("partial plan\nbackend error\n", (Path(self.temporary.name) / "terraform-plan/instance-plan.txt").read_text())
        self.assertEqual(subprocess.PIPE, process.call_args.kwargs["stderr"])
        self.assertFalse(process.call_args.kwargs["check"])

    def test_app_service_staging_swap_and_production_checks_use_actual_targets_and_fail_closed(self):
        config = config_for("app-service")
        artifact(self.directory, config)
        for region in ("uks", "neu"):
            for environment, preview in (("ci", ""), ("prod", ""), ("preview", "42")):
                name = region + "-" + ("ci" if environment == "preview" else environment) + "-demo" + ("-pr" + preview if preview else "") + "-api-app"
                values = {"resource_group_name": "actual-rg", "workload_targets": {"api": {"name": name, "hostname": "production.azurewebsites.net", "staging_hostname": "staging.azurewebsites.net"}}}
                for failure in (None, "staging", "swap", "production"):
                    calls = []
                    def stub(*args, **kwargs):
                        calls.append(args)
                        self.assertIn(name, args)
                        if "swap" in args and failure == "swap": raise subprocess.CalledProcessError(1, args)
                        return ""
                    def http(host, path):
                        self.assertEqual("/ready", path)
                        return not ((host.startswith("staging") and failure == "staging") or (host.startswith("production") and failure == "production"))
                    with self.subTest(name=name, failure=failure), patch.dict(os.environ, {"DEPLOY_ENVIRONMENT": environment, "PREVIEW_NUMBER": preview}), patch.object(runtime, "foundation", return_value={}) as foundation, patch.object(runtime, "backend"), patch.object(runtime, "apply"), patch.object(runtime, "promote", return_value={}), patch.object(runtime, "outputs", return_value=values), patch.object(runtime, "command", side_effect=stub), patch.object(runtime, "http_ready", side_effect=http):
                        if failure is None:
                            runtime.deploy(config)
                            runtime.readiness(config)
                        else:
                            with self.assertRaises((runtime.DeploymentError, subprocess.CalledProcessError)):
                                runtime.deploy(config)
                                runtime.readiness(config)
                        foundation.assert_called_with(config, not preview)
                    self.assertEqual(failure != "staging", any("swap" in call for call in calls))

    def test_command_execution_uses_argument_vector_and_propagates_failure(self):
        self.commands.stop()
        with patch.object(runtime.subprocess, "run", return_value=Mock(stdout=" value \n")) as process:
            self.assertEqual("value", runtime.command("az", "show", "untrusted; text", cwd=Path("infra")))
        self.assertEqual(("az", "show", "untrusted; text"), process.call_args.args[0])
        self.assertNotIn("shell", process.call_args.kwargs)
        with patch.object(runtime.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["az"])):
            with self.assertRaises(subprocess.CalledProcessError): runtime.command("az")


if __name__ == "__main__":
    unittest.main()
