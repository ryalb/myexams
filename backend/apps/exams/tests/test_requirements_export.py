"""`backend/requirements.txt` (o que a Vercel instala) tem de bater com o `uv.lock`.

Regenerar: `mise run backend-requirements`.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
EXPORT = [
    "uv",
    "export",
    "--frozen",
    "--no-dev",
    "--no-default-groups",
    "--no-hashes",
    "--no-emit-project",
    "--no-annotate",
    "--no-header",
]
# Só desenvolvimento local, testes e ferramentas: nunca no pacote da função.
NOT_IN_THE_CLOUD = {
    "daphne",
    "channels-redis",
    "redis",
    "pytest",
    "pytest-django",
    "reportlab",
    "ruff",
    "ty",
    "prek",
}


def pins(text: str) -> set[str]:
    found = set()
    for line in text.splitlines():
        line = line.split(";")[0].strip()
        if line and not line.startswith("#"):
            found.add(re.sub(r"[-_.]+", "-", line.lower()))
    return found


def test_requirements_txt_matches_the_lockfile():
    if shutil.which("uv") is None:
        pytest.skip(reason="uv não está no PATH")

    fresh = subprocess.run(EXPORT, cwd=BACKEND, capture_output=True, text=True, check=True).stdout
    committed = (BACKEND / "requirements.txt").read_text()

    assert pins(committed) == pins(fresh), "rode `mise run backend-requirements` e faça o commit"


def test_requirements_txt_has_no_dev_or_local_only_packages():
    names = {pin.split("==")[0] for pin in pins((BACKEND / "requirements.txt").read_text())}

    assert not names & NOT_IN_THE_CLOUD
    assert {"django", "celery", "channels", "pdfplumber", "psycopg"} <= names
