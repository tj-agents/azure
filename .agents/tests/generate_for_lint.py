from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents/azure/utility/scaffold/scripts/scaffold.py"
FIXTURE_PROFILE = Path(__file__).resolve().parent / "fixtures" / "sandbox-profile.json"


def sandbox_profile() -> dict:
    return json.loads(FIXTURE_PROFILE.read_text(encoding="utf-8"))


def app_service_profile() -> dict:
    profile = sandbox_profile()
    api, _, fn = profile["workloads"]
    api["runtime"] = {"name": "dotnet", "version": "10.0"}
    api["build"] = {"package": "echo build-api"}
    fn["build"] = {"image": "echo build-fn"}
    return profile


CASES = [
    ("empty", {"schema_version": 1, "name": "empty", "workloads": []}, []),
    ("sandbox-public", sandbox_profile(), ["--set", "messaging=service-bus", "--set", "topics=orders"]),
    ("sandbox-private", sandbox_profile(), ["--preset", "private", "--set", "messaging=service-bus", "--set", "topics=orders"]),
    ("sandbox-app-service", app_service_profile(), ["--preset", "private", "--set", "http_host=app-service", "--set", "functions_host=container-apps", "--set", "messaging=service-bus", "--set", "topics=orders"]),
]


def generate(out_dir: Path) -> list[Path]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    presets: list[Path] = []
    for name, profile, extra_args in CASES:
        project = out_dir / name
        (project / "infra").mkdir(parents=True)
        (project / "infra" / "app.json").write_text(json.dumps(profile), encoding="utf-8")
        args = [sys.executable, "-B", str(SCRIPT), "new", "--into", str(project), "--no-input", *extra_args]
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stdout, file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            raise SystemExit(f"scaffold generation failed for case {name!r}")
        presets.append(project)
    return presets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    presets = generate(args.out_dir)
    for project in presets:
        print(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
