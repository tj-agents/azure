from __future__ import annotations

import argparse
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import time
import zipfile


WAIT_DEADLINE = ContextVar("wait_deadline", default=None)


class DeploymentError(Exception):
    pass


def command(*args: str, cwd: Path | None = None, env: dict | None = None, diagnostic: Path | None = None) -> str:
    deadline = WAIT_DEADLINE.get()
    limits = {}
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, 0)
        limits["timeout"] = remaining
    result = subprocess.run(args, cwd=cwd, env=env, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE if diagnostic is not None else None, **limits)
    if diagnostic is not None:
        diagnostic.write_text(result.stdout + (result.stderr or ""), encoding="utf-8")
    result.check_returncode()
    return result.stdout.strip()


def require(value: object, message: str) -> None:
    if not value:
        raise DeploymentError(message)


def json_command(*args: str, cwd: Path | None = None):
    return json.loads(command(*args, cwd=cwd))


def preview_id(number: str) -> str:
    if not number:
        return ""
    require(re.fullmatch(r"[1-9][0-9]*", number), "invalid preview number")
    identity = "pr" + number
    require(len(identity) <= 6, "preview identity exceeds the 6-character limit")
    return identity


def source_identity() -> tuple[str, str]:
    sha, run = os.environ.get("SOURCE_SHA", ""), os.environ.get("SOURCE_RUN_ID", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "invalid source commit")
    require(re.fullmatch(r"[1-9][0-9]*", run), "invalid source run")
    return sha, run


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(config, dict) and set(config) == {"schema_version", "name", "assets", "migration", "service_bus"}, "invalid deployment configuration")
    require(config["schema_version"] == 1 and type(config["schema_version"]) is int, "invalid deployment configuration version")
    require(isinstance(config["name"], str) and re.fullmatch(r"[a-z][a-z0-9]{1,11}", config["name"]), "invalid deployment name")
    require(type(config["migration"]) is bool and type(config["service_bus"]) is bool, "invalid deployment flags")
    require(isinstance(config["assets"], dict), "invalid deployment assets")
    for identity, asset in config["assets"].items():
        require(isinstance(identity, str) and (re.fullmatch(r"[a-z][a-z0-9-]{0,15}", identity) or identity == "database-migration"), "invalid asset identity")
        require(isinstance(asset, dict) and set(asset) == {"host", "command", "readiness", "kind"}, "invalid asset configuration")
        require(asset["host"] in {"container-apps", "app-service", "flex-consumption"}, "invalid asset host")
        require(isinstance(asset["command"], str) and asset["command"].strip() and "${{" not in asset["command"] and "}}" not in asset["command"], "invalid build command")
        require(isinstance(asset["readiness"], str) and re.fullmatch(r"/[A-Za-z0-9._~/?=&%-]*", asset["readiness"]), "invalid readiness path")
        require(asset["kind"] in {"http", "background", "functions", "migration"}, "invalid asset kind")
        require(asset["kind"] != "migration" or (identity == "database-migration" and config["migration"]), "invalid migration identity")
    require(config["migration"] == any(asset["kind"] == "migration" for asset in config["assets"].values()), "migration configuration mismatch")
    return config


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file(path: Path) -> bool:
    info = path.lstat()
    return path.is_file() and not path.is_symlink() and not (getattr(info, "st_file_attributes", 0) & 0x400)


def image_archive(path: Path, asset: dict) -> None:
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        require(len({item.name for item in members}) == len(members), "duplicate image archive members")
        for item in members:
            require(not item.name.startswith("/") and ".." not in Path(item.name).parts and "\\" not in item.name and not re.match(r"^[A-Za-z]:", item.name), "unsafe image archive path")
            require(item.isfile() or item.isdir(), "unsafe image archive member")
        stream = archive.extractfile("manifest.json")
        require(stream is not None, "missing image archive manifest")
        with stream:
            manifest = json.load(stream)
        require(isinstance(manifest, list) and len(manifest) == 1 and isinstance(manifest[0], dict), "image archive must contain one platform image")
        record = manifest[0]
        require(record.get("RepoTags") == [asset["source_tag"]], "image archive tag mismatch")
        config_name = record.get("Config")
        require(isinstance(config_name, str) and config_name in {item.name for item in members}, "missing image archive configuration")
        stream = archive.extractfile(config_name)
        require(stream is not None, "invalid image archive configuration")
        with stream:
            raw = stream.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, "image archive configuration too large")
        require("sha256:" + hashlib.sha256(raw).hexdigest() == asset["image_id"], "image archive identity mismatch")
        config = json.loads(raw)
        platform = "/".join(str(config.get(key, "")) for key in ("os", "architecture"))
        require(platform == asset["platform"] and re.fullmatch(r"linux/[a-z0-9_]+", platform), "image archive platform mismatch")
        layers = record.get("Layers")
        require(isinstance(layers, list) and layers and all(isinstance(layer, str) and layer in {item.name for item in members} for layer in layers), "missing image archive layers")


def validate_artifact(config: dict, directory: Path) -> dict:
    require(directory.is_dir() and not directory.is_symlink() and not (getattr(directory.lstat(), "st_file_attributes", 0) & 0x400), "missing or unsafe artifact directory")
    sha, run = source_identity()
    manifest_path = directory / "manifest.json"
    require(regular_file(manifest_path), "unsafe artifact manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict) and set(manifest) == {"schema_version", "name", "source_sha", "source_run_id", "assets"}, "invalid artifact manifest")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1, "unsupported artifact version")
    require(manifest["name"] == config["name"] and manifest["source_sha"] == sha and manifest["source_run_id"] == run, "artifact source mismatch")
    assets = manifest["assets"]
    require(isinstance(assets, dict) and set(assets) == set(config["assets"]), "artifact workload mismatch")
    expected = {"manifest.json"}
    for identity, definition in config["assets"].items():
        asset = assets[identity]
        require(isinstance(asset, dict), "invalid artifact asset")
        image = definition["host"] == "container-apps"
        keys = {"host", "path", "sha256"} | ({"source_ref", "source_tag", "image_id", "platform"} if image else set())
        require(set(asset) == keys and asset["host"] == definition["host"], "artifact host mismatch")
        filename = identity + (".tar" if image else ".zip")
        require(asset["path"] == filename, "unsafe artifact path")
        expected.add(filename)
        path = directory / filename
        require(regular_file(path), "unsafe artifact file")
        require(isinstance(asset["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) and file_hash(path) == asset["sha256"], "artifact hash mismatch")
        if image:
            repository = config["name"] + "-" + identity
            require(isinstance(asset["source_ref"], str) and re.fullmatch(r"[a-z0-9]+\.azurecr\.io/" + repository + r"@sha256:[0-9a-f]{64}", asset["source_ref"]), "invalid image digest")
            require(asset["source_tag"] == asset["source_ref"].split("@")[0] + ":" + sha, "image source tag mismatch")
            require(isinstance(asset["image_id"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", asset["image_id"]), "invalid image identity")
            image_archive(path, asset)
        else:
            with zipfile.ZipFile(path) as archive:
                require(archive.infolist(), "empty deployment package")
                for item in archive.infolist():
                    require(not item.filename.startswith("/") and ".." not in Path(item.filename).parts and "\\" not in item.filename and not re.match(r"^[A-Za-z]:", item.filename) and not stat.S_ISLNK(item.external_attr >> 16), "unsafe package member")
                require(archive.testzip() is None, "corrupt deployment package")
    require({path.name for path in directory.iterdir()} == expected, "unexpected artifact files")
    return manifest


def backend(config: dict, root: str, identity: str = "") -> None:
    key = config["name"] + "/" + root + ("." + identity if identity else "") + ".tfstate"
    command("terraform", "init", "-input=false", "-reconfigure", "-backend-config=resource_group_name=" + os.environ["TFSTATE_RESOURCE_GROUP"], "-backend-config=storage_account_name=" + os.environ["TFSTATE_STORAGE_ACCOUNT"], "-backend-config=container_name=tfstate", "-backend-config=key=" + key, cwd=Path("infra") / root)


def tfvars() -> str:
    environment = os.environ["DEPLOY_ENVIRONMENT"]
    require(environment in {"ci", "preview", "prod"}, "invalid deployment environment")
    return "-var-file=../env/" + ("ci" if environment == "preview" else environment) + ".tfvars"


def apply(root: str, *args: str) -> None:
    directory = Path("infra") / root
    command("terraform", "plan", "-input=false", tfvars(), *args, "-out=tfplan", cwd=directory)
    command("terraform", "apply", "-input=false", "tfplan", cwd=directory)


def outputs(root: str) -> dict:
    result = json_command("terraform", "output", "-json", cwd=Path("infra") / root)
    return {key: value["value"] for key, value in result.items()}


def foundation(config: dict, converge: bool) -> dict:
    backend(config, "foundation")
    if converge:
        apply("foundation")
    return outputs("foundation")


def registry_login(value: str) -> None:
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9]+\.azurecr\.io", value), "invalid target registry")
    command("az", "acr", "login", "--name", value.split(".")[0])


def inspect(image: str) -> dict:
    value = json_command("docker", "inspect", image)
    require(isinstance(value, list) and len(value) == 1, "invalid local image")
    return value[0]


def pushed_ref(image: dict, repository: str) -> str:
    refs = [ref for ref in image.get("RepoDigests", []) if isinstance(ref, str) and re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", ref)]
    require(len(refs) == 1, "missing or ambiguous pushed image digest")
    return refs[0]


def build(config: dict) -> None:
    sha, run = source_identity()
    preview_id(os.environ.get("PREVIEW_NUMBER", ""))
    directory = Path(os.environ["RUNNER_TEMP"]) / "deployment"
    require(not directory.exists(), "deployment artifact directory already exists")
    directory.mkdir()
    values = foundation(config, not os.environ.get("PREVIEW_NUMBER"))
    registry = values.get("registry_login_server", "")
    if any(asset["host"] == "container-apps" for asset in config["assets"].values()):
        registry_login(registry)
    assets = {}
    for identity, definition in config["assets"].items():
        image = definition["host"] == "container-apps"
        path = directory / (identity + (".tar" if image else ".zip"))
        repository = registry + "/" + config["name"] + "-" + identity
        tag = repository + ":" + sha
        env = {**os.environ, "REGISTRY": registry, "REPOSITORY": config["name"] + "-" + identity, "TAG": sha, "IMAGE": tag, "PACKAGE": str(path)}
        command("bash", "-euo", "pipefail", "-c", definition["command"], env=env)
        asset = {"host": definition["host"], "path": path.name}
        if image:
            local = inspect(tag)
            require(not local.get("Variant"), "image variants are unsupported")
            asset.update(source_ref=pushed_ref(local, repository), source_tag=tag, image_id=local["Id"], platform=local["Os"] + "/" + local["Architecture"])
            command("docker", "save", "--output", str(path), tag)
        require(regular_file(path), "missing built asset")
        asset["sha256"] = file_hash(path)
        assets[identity] = asset
    manifest = {"schema_version": 1, "name": config["name"], "source_sha": sha, "source_run_id": run, "assets": assets}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    validate_artifact(config, directory)


def promote(config: dict, manifest: dict, directory: Path, registry: str) -> dict:
    images = {}
    if any(asset["host"] == "container-apps" for asset in manifest["assets"].values()):
        registry_login(registry)
    for identity, asset in manifest["assets"].items():
        if asset["host"] != "container-apps":
            continue
        command("docker", "load", "--input", str(directory / asset["path"]))
        local = inspect(asset["image_id"])
        require(local.get("Id") == asset["image_id"] and local.get("Os", "") + "/" + local.get("Architecture", "") == asset["platform"] and not local.get("Variant"), "loaded image identity mismatch")
        repository = registry + "/" + config["name"] + "-" + identity
        target = repository + ":" + manifest["source_sha"]
        command("docker", "tag", asset["image_id"], target)
        command("docker", "push", target)
        reference = pushed_ref(inspect(target), repository)
        require(reference.split("@")[1] == asset["source_ref"].split("@")[1], "promoted image digest mismatch")
        images[identity] = reference
    return images


def wait_for(probe, message: str) -> None:
    deadline = time.monotonic() + 300
    token = WAIT_DEADLINE.set(deadline)
    try:
        while time.monotonic() < deadline:
            ready = probe()
            remaining = deadline - time.monotonic()
            if ready and remaining > 0:
                return
            if remaining <= 0:
                break
            time.sleep(min(10, remaining))
        raise DeploymentError(message + " within 5 minutes")
    finally:
        WAIT_DEADLINE.reset(token)


def migration(values: dict) -> None:
    name = values["migration_job_name"]
    group = values["resource_group_name"]
    execution = json_command("az", "containerapp", "job", "start", "--name", name, "--resource-group", group)
    execution_name = execution.get("name")
    require(isinstance(execution_name, str) and execution_name, "missing migration execution identity")
    def completed():
        value = json_command("az", "containerapp", "job", "execution", "show", "--name", name, "--resource-group", group, "--job-execution-name", execution_name)
        status = value.get("properties", {}).get("status")
        require(status != "Failed", "migration execution failed")
        return status == "Succeeded"
    wait_for(completed, "migration execution did not succeed")


def remove_default_rules(values: dict) -> None:
    for entry in json.loads(values["service_bus_default_rule_subscriptions"]):
        args = ("--resource-group", values["resource_group_name"], "--namespace-name", entry["namespace"], "--topic-name", entry["topic"], "--subscription-name", entry["subscription"])
        rules = json_command("az", "servicebus", "topic", "subscription", "rule", "list", *args)
        require(isinstance(rules, list) and all(isinstance(rule, dict) and isinstance(rule.get("name"), str) for rule in rules), "invalid subscription rule list")
        if any(rule["name"] == "$Default" for rule in rules):
            command("az", "servicebus", "topic", "subscription", "rule", "delete", *args, "--name", "$Default")


def deploy(config: dict) -> None:
    identity = preview_id(os.environ.get("PREVIEW_NUMBER", ""))
    tfvars()
    directory = Path(os.environ["ARTIFACT_DIRECTORY"])
    manifest = validate_artifact(config, directory) if config["assets"] else None
    values = foundation(config, not identity)
    images = promote(config, manifest, directory, values.get("registry_login_server", "")) if manifest else {}
    image_file = Path(os.environ["RUNNER_TEMP"]) / "images.auto.tfvars.json"
    image_file.write_text(json.dumps({"images": images}), encoding="utf-8")
    backend(config, "instance", identity)
    args = ("-var-file=" + str(image_file.resolve()), "-var=ephemeral_id=" + identity)
    if config["migration"]:
        apply("instance", *args, "-target=azurerm_user_assigned_identity.migrator", "-target=azurerm_postgresql_flexible_server_active_directory_administrator.migrator", "-target=azurerm_postgresql_flexible_server_database.main", "-target=azurerm_container_app_job.migrate")
        migration(outputs("instance"))
    apply("instance", *args)
    values = outputs("instance")
    if config["service_bus"]:
        remove_default_rules(values)
    for identity, asset in config["assets"].items():
        if asset["host"] == "container-apps":
            continue
        target = values["workload_targets"][identity]
        group, name = values["resource_group_name"], target["name"]
        args = ("--name", name, "--resource-group", group, "--src-path", str(directory / manifest["assets"][identity]["path"]), "--type", "zip")
        if asset["host"] == "app-service":
            command("az", "webapp", "deploy", *args, "--slot", "staging")
            wait_for(lambda: http_ready(target["staging_hostname"], asset["readiness"]), identity + " staging slot did not become ready")
            command("az", "webapp", "deployment", "slot", "swap", "--name", name, "--resource-group", group, "--slot", "staging", "--target-slot", "production")
        else:
            command("az", "functionapp", "deploy", *args)


def http_ready(host: str, path: str) -> bool:
    require(isinstance(host, str) and re.fullmatch(r"[a-zA-Z0-9.-]+", host), "invalid serving hostname")
    try:
        return command("curl", "--silent", "--show-error", "--max-time", "20", "--output", os.devnull, "--write-out", "%{http_code}", "https://" + host + path) == "200"
    except subprocess.CalledProcessError:
        return False


def readiness(config: dict) -> None:
    values = outputs("instance")
    images = json.loads((Path(os.environ["RUNNER_TEMP"]) / "images.auto.tfvars.json").read_text())["images"]
    for identity, asset in config["assets"].items():
        if asset["kind"] == "migration":
            continue
        target = values["workload_targets"][identity]
        group, name = values["resource_group_name"], target["name"]
        if asset["host"] == "container-apps":
            expected_image = images[identity]
            require(isinstance(expected_image, str) and re.fullmatch(r"[a-z0-9]+\.azurecr\.io/[a-z0-9-]+@sha256:[0-9a-f]{64}", expected_image), "missing promoted image digest")
            def routes_to(properties, revision):
                traffic = properties.get("configuration", {}).get("ingress", {}).get("traffic", [])
                return any(item.get("weight") == 100 and (item.get("latestRevision") is True or item.get("revisionName") == revision) for item in traffic)
            def matching_image(current):
                return any(container.get("name") == "main" and container.get("image") == expected_image for container in current.get("template", {}).get("containers", []))
            def serving():
                properties = json_command("az", "containerapp", "show", "--name", name, "--resource-group", group).get("properties", {})
                revision = properties.get("latestRevisionName")
                if not revision:
                    return False
                current = json_command("az", "containerapp", "revision", "show", "--name", name, "--resource-group", group, "--revision", revision).get("properties", {})
                if not matching_image(current):
                    return False
                if asset["kind"] != "background":
                    if not routes_to(properties, revision) or not http_ready(target["hostname"], asset["readiness"]):
                        return False
                    properties = json_command("az", "containerapp", "show", "--name", name, "--resource-group", group).get("properties", {})
                    if properties.get("latestRevisionName") != revision or not routes_to(properties, revision):
                        return False
                    current = json_command("az", "containerapp", "revision", "show", "--name", name, "--resource-group", group, "--revision", revision).get("properties", {})
                return properties.get("latestReadyRevisionName") == revision and matching_image(current) and current.get("active") is True and current.get("runningState") == "Running" and current.get("healthState") == "Healthy"
            wait_for(serving, identity + " latest revision did not become serving")
        elif asset["host"] == "flex-consumption":
            def serving():
                app = json_command("az", "functionapp", "show", "--name", name, "--resource-group", group)
                return app.get("properties", {}).get("state", app.get("state")) == "Running" and http_ready(target["hostname"], asset["readiness"])
            wait_for(serving, identity + " function app did not become serving")
        else:
            wait_for(lambda: http_ready(target["hostname"], asset["readiness"]), identity + " production slot did not become ready")


def state_blob(config: dict, identity: str) -> dict | None:
    require(identity, "preview identity required")
    args = ("--account-name", os.environ["TFSTATE_STORAGE_ACCOUNT"], "--container-name", "tfstate", "--name", config["name"] + "/instance." + identity + ".tfstate", "--auth-mode", "login")
    value = json_command("az", "storage", "blob", "exists", *args)
    require(isinstance(value, dict) and type(value.get("exists")) is bool, "invalid state existence response")
    if not value["exists"]:
        return None
    return {"args": args}


def destroy(config: dict) -> None:
    identity = preview_id(os.environ.get("PREVIEW_NUMBER", ""))
    if state_blob(config, identity) is None:
        return
    backend(config, "instance", identity)
    command("terraform", "destroy", "-input=false", "-auto-approve", tfvars(), "-var=ephemeral_id=" + identity, cwd=Path("infra/instance"))


def recover(config: dict) -> None:
    identity = preview_id(os.environ.get("PREVIEW_NUMBER", ""))
    blob = state_blob(config, identity)
    if blob is None:
        return
    value = json_command("az", "storage", "blob", "show", *blob["args"])
    lease = value.get("properties", {}).get("lease", {})
    require(lease.get("status") in {"locked", "unlocked"} and lease.get("state") in {"available", "leased", "expired", "breaking", "broken"}, "invalid state lease response")
    if lease["status"] == "unlocked":
        return
    lease_args = tuple("--blob-name" if value == "--name" else value for value in blob["args"])
    command("az", "storage", "blob", "lease", "break", *lease_args, "--lease-break-period", "0")


def plan(config: dict) -> None:
    root = os.environ["TF_ROOT"]
    require(root in {"foundation", "instance"}, "invalid Terraform root")
    preview_id(os.environ.get("PREVIEW_NUMBER", ""))
    backend(config, root)
    directory = Path(os.environ["RUNNER_TEMP"]) / "terraform-plan"
    directory.mkdir(exist_ok=True)
    text = command("terraform", "plan", "-input=false", "-no-color", tfvars(), cwd=Path("infra") / root, diagnostic=directory / (root + "-plan.txt"))
    print(text)


def validate() -> None:
    root = os.environ["TF_ROOT"]
    require(root in {"foundation", "instance"}, "invalid Terraform root")
    directory = Path("infra") / root
    command("terraform", "fmt", "-check", "-recursive", cwd=directory)
    command("terraform", "init", "-input=false", "-backend=false", cwd=directory)
    command("terraform", "validate", cwd=directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("validate", "plan", "build", "deploy", "readiness", "destroy", "recover"))
    args = parser.parse_args(argv)
    try:
        config = load_config(Path("infra/deployment.json"))
        if args.operation == "validate":
            validate()
        else:
            globals()[args.operation](config)
    except (DeploymentError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, tarfile.TarError, zipfile.BadZipFile) as error:
        print("::error file=infra/scripts/azure_deploy.py::" + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
