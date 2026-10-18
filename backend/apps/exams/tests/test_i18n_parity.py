"""O backend devolve códigos; a tradução mora no frontend (locales/pt-BR.json).

Este teste impede que um código novo (erro, aviso, status, sinalização...) chegue
à tela sem tradução, e que sobrem chaves órfãs no arquivo de locale.
"""

import json
import re
from pathlib import Path

import pytest

from apps.exams import models
from apps.exams.codes import ErrorCode, WarningCode

LOCALE = Path(__file__).resolve().parents[4] / "frontend" / "locales" / "pt-BR.json"


@pytest.fixture(scope="module")
def locale() -> dict:
    return json.loads(LOCALE.read_text(encoding="utf-8"))


def values(enum) -> set[str]:
    return {str(v) for v in enum.values}


GROUPS = {
    "errors": {str(c) for c in ErrorCode},
    "warnings": {str(c) for c in WarningCode},
    "status": values(models.DocumentStatus),
    "runStatus": values(models.RunStatus),
    "flag": values(models.ResultFlag),
    "reviewStatus": values(models.ReviewStatus),
    "origin": values(models.ResultOrigin),
    "category": values(models.AnalyteCategory),
    "sex": values(models.Sex),
    "method": values(models.ExtractionMethod),
}


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_backend_code_has_a_translation(locale, group):
    missing = GROUPS[group] - set(locale.get(group, {}))
    assert not missing, f"faltam em locales/pt-BR.json -> {group}: {sorted(missing)}"


@pytest.mark.parametrize("group", ["errors", "warnings"])
def test_no_orphan_codes_in_locale(locale, group):
    orphans = set(locale[group]) - GROUPS[group]
    assert not orphans, f"chaves sem código no backend em {group}: {sorted(orphans)}"


def test_locale_text_is_portuguese_with_accents_not_mojibake(locale):
    flat = json.dumps(locale, ensure_ascii=False)
    assert "Ã" not in flat and "Â" not in flat
    assert re.search(r"[áéíóúãõçÁÉÍÓÚ]", flat)
