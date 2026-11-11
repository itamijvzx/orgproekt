"""Inspect the built runtime image, not a source-tree approximation of its contents."""

import importlib
import importlib.metadata
import json
import os
import shutil
from pathlib import Path

forbidden = {"pytest", "ruff", "pip-audit", "virtualenv", "uv"}
installed = {
    dist.metadata["Name"].lower().replace("_", "-") for dist in importlib.metadata.distributions()
}
problems = [f"Development package installed: {name}" for name in sorted(forbidden & installed)]
for command in ("uv", "uvx", "pytest", "ruff", "pip-audit"):
    if shutil.which(command):
        problems.append(f"Development executable present: {command}")
for name in ("tests", "scripts", "pyproject.toml", "uv.lock"):
    if (Path("/app") / name).exists():
        problems.append(f"Build/test source present: {name}")
if os.geteuid() == 0:
    problems.append("Runtime user is root")
for name in ("app", "migrations", "ops", "alembic.ini", "embedding-model.json"):
    if not (Path("/app") / name).exists():
        problems.append(f"Required runtime file missing: {name}")
if not Path("/opt/orgproekt/embeddings/manifest.json").is_file():
    problems.append("Pinned search model missing")
for module in (
    "fastapi",
    "celery",
    "psycopg",
    "onnxruntime",
    "pypdf",
    "docx",
    "reportlab",
    "fido2",
):
    importlib.import_module(module)
if problems:
    raise SystemExit("\n".join(problems))
print(json.dumps({"result": "passed", "runtime_packages": len(installed), "uid": os.geteuid()}))
