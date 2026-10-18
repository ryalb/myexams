"""Formato `myexams-reports/1`: a ponte entre `export_reports` e `import_json`.

    PDFs --export_reports--> JSON (revisado por humanos) --import_json--> banco

Este módulo serializa o que o pipeline extraiu (exportar), valida o arquivo editado sem tocar no
banco (`parse_file`) e o grava (`import_documents`), reaproveitando as funções puras de
normalização do app (`recompute`, `flags`, `reference`, `numbers`, `units`, `validate`) e as regras
de duplicidade/histórico de `pipeline.persist`.

AUTORIDADE NA IMPORTAÇÃO (o que vale quando o arquivo foi editado)
-------------------------------------------------------------------
Por resultado:
- `keep: false` (ou a linha apagada): não é importada.
- Entradas: `analyte` (slug do catálogo), `value_raw`, `unit_raw`, `reference_raw`, `collected_at`.
  São o texto impresso no laudo; é neles que se edita.
- Cada linha exportada leva um `fingerprint` das entradas + valores derivados. Se a linha continua
  igual (a impressão digital confere), ela entra exatamente como exportada.
- Se algo mudou (entrada OU campo derivado, ex. `value_numeric`/`flag`) ou a linha é nova (sem
  `fingerprint`), e não há `"reviewed": true`, os campos derivados (`value_numeric`, `comparator`,
  `value_text`, `unit`, `value_canonical`, `ref_*`, `flag`, `interpretation`) são recalculados a
  partir das entradas com o mesmo código do app: o que foi digitado nos derivados é descartado.
  Os avisos também são refeitos (só sobrevive `page_read_by_vision`).
- `"reviewed": true`: a linha entra exatamente como escrita, sem recalcular nada (você assume os
  números; os tipos ainda são validados).
- `review_status`: `accepted` (ou `confirmed`) e `user_corrected` são sempre respeitados (`accepted`
  vira "Confirmado"). Com `auto`/`needs_review`: linha intacta mantém; linha recalculada vira
  `user_corrected`, ou `needs_review` se os avisos refeitos pedem revisão.
Por laudo:
- Campos do cabeçalho (pedido, médico, paciente, datas, laboratório, avisos) valem como escritos.
- `status` do JSON só precisa ser `completed` ou `needs_review`; o status gravado é recalculado
  com a regra do app (`needs_review` se sobra resultado a revisar ou há aviso do laudo).
- `status: failed` (ou `keep: false`, ou nenhuma linha mantida): o laudo é pulado, não é criado.
- Mesmo `sha256` para o usuário, ou mesmo número de pedido no mesmo laboratório: pulado, nunca
  sobrescrito. Linhas `lab_previous` repetem a regra do pipeline (dia já conhecido é descartado;
  resultado atual substitui o "anterior" semeado por outro laudo).
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .extraction.analytes import AnalyteResolver
from .extraction.pipeline import (
    drop_superseded_previous,
    find_accession_clash,
    history_key,
    known_history_keys,
    laboratory_for,
    review_status_of_document,
)
from .extraction.recompute import recompute_result, rederived_warnings
from .extraction.text import normalize_name
from .extraction.validate import needs_review
from .models import (
    CURRENT_ORIGINS,
    Analyte,
    Comparator,
    DocumentStatus,
    ExamDocument,
    ExamResult,
    ExtractionMethod,
    Laboratory,
    Profile,
    ReferenceKind,
    ResultFlag,
    ResultOrigin,
    ReviewStatus,
)

FORMAT = "myexams-reports/1"

NOTE = (
    "Este arquivo contém dados pessoais de saúde (resultados de exames, nome e nascimento do "
    "paciente): não versione, não envie e guarde-o numa pasta ignorada pelo git. "
    "Como editar: cada resultado tem 'keep' (false = não importar; apagar a linha também vale), "
    "'reviewed' (true = importar os números exatamente como escritos) e 'review_status' "
    "('accepted' limpa a revisão). Edite os textos impressos (value_raw, unit_raw, reference_raw) "
    "ou o 'analyte' (slug do catálogo, ou null): os campos derivados (valor numérico, faixa, "
    "sinalização) são recalculados na importação. 'summary' é só para leitura e 'fingerprint' "
    "não deve ser editado. Laudos com status 'failed' não são importados. "
    "Importe com: manage.py import_json arquivo.json --email SEU@EMAIL [--dry-run]."
)

SKIPPED_STATUSES = (DocumentStatus.FAILED,)
IMPORTABLE_STATUSES = (DocumentStatus.COMPLETED, DocumentStatus.NEEDS_REVIEW)

# Entradas e valores derivados que o `fingerprint` cobre (ver o cabeçalho do módulo).
FINGERPRINT_KEYS = (
    "analyte",
    "value_raw",
    "unit_raw",
    "reference_raw",
    "collected_at",
    "comparator",
    "value_numeric",
    "value_text",
    "unit",
    "value_canonical",
    "ref_kind",
    "ref_low",
    "ref_high",
    "ref_low_inclusive",
    "ref_high_inclusive",
    "flag",
)

_FLAG_LABELS = {
    ResultFlag.LOW: "baixo",
    ResultFlag.HIGH: "alto",
    ResultFlag.NORMAL: "normal",
    ResultFlag.ABNORMAL: "alterado",
}


class ReportsFileError(Exception):
    """Arquivo inválido. A mensagem nunca carrega valores de exame, só campo e posição."""


# --------------------------------------------------------------------------------------------
# Exportar
# --------------------------------------------------------------------------------------------


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def _datetime_text(value: datetime | None) -> str | None:
    return None if value is None else timezone.localtime(value).isoformat()


def _canonical(value):
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def fingerprint(values: dict) -> str:
    """Impressão digital das entradas + derivados de uma linha (`values` com tipos Python)."""
    payload = json.dumps([_canonical(values.get(key)) for key in FINGERPRINT_KEYS])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _fingerprint_of_result(result: ExamResult) -> str:
    values = {key: getattr(result, key) for key in FINGERPRINT_KEYS if key != "analyte"}
    values["analyte"] = result.analyte.slug if result.analyte else None
    return fingerprint(values)


def _br(text: str | None) -> str:
    return (text or "").replace(".", ",")


def summary_of(result: ExamResult) -> str:
    """Linha curta para leitura humana: `Hemoglobina 15,1 g/dL (13,3 a 16,5) normal`."""
    name = result.analyte.name_pt if result.analyte else result.name_raw
    value = result.value_raw or result.value_text
    parts: list[str] = [name, value, result.unit_raw or result.unit]
    text = " ".join(part for part in parts if part)
    low, high = _decimal_text(result.ref_low), _decimal_text(result.ref_high)
    if result.ref_kind == ReferenceKind.RANGE:
        text += f" ({_br(low)} a {_br(high)})"
    elif result.ref_kind == ReferenceKind.UPPER:
        text += f" (até {_br(high)})"
    elif result.ref_kind == ReferenceKind.LOWER:
        text += f" (a partir de {_br(low)})"
    label = _FLAG_LABELS.get(result.flag)
    if label:
        text += f" {label}"
    if result.origin == ResultOrigin.LAB_PREVIOUS and result.collected_at:
        text = f"[anterior {timezone.localtime(result.collected_at):%d/%m/%Y}] {text}"
    return text


def serialize_result(result: ExamResult) -> dict:
    return {
        "summary": summary_of(result),
        "keep": True,
        "reviewed": False,
        "review_status": result.review_status,
        "analyte": result.analyte.slug if result.analyte else None,
        "name_raw": result.name_raw,
        "section_raw": result.section_raw,
        "origin": result.origin,
        "page": result.page,
        "position": result.position,
        "value_raw": result.value_raw,
        "unit_raw": result.unit_raw,
        "reference_raw": result.reference_raw,
        "flag_printed": result.flag_printed,
        "comparator": result.comparator,
        "value_numeric": _decimal_text(result.value_numeric),
        "value_text": result.value_text,
        "unit": result.unit,
        "value_canonical": _decimal_text(result.value_canonical),
        "ref_kind": result.ref_kind,
        "ref_low": _decimal_text(result.ref_low),
        "ref_high": _decimal_text(result.ref_high),
        "ref_low_inclusive": result.ref_low_inclusive,
        "ref_high_inclusive": result.ref_high_inclusive,
        "ref_bands": result.ref_bands,
        "flag": result.flag,
        "interpretation": result.interpretation,
        "collected_at": _datetime_text(result.collected_at),
        "method": result.method,
        "specimen": result.specimen,
        "performing_lab": result.performing_lab,
        "notes": result.notes,
        "warnings": result.warnings,
        "source_line": result.source_line,
        "fingerprint": _fingerprint_of_result(result),
    }


def serialize_document(
    document: ExamDocument, *, source_file: str, include_text: bool = False
) -> dict:
    results = document.results.select_related("analyte").order_by("page", "position", "pk")
    data = {
        "source_file": source_file,
        "sha256": document.sha256,
        "keep": True,
        "status": document.status,
        "error_code": document.error_code,
        "error_params": document.error_params,
        "warnings": document.warnings,
        "laboratory": document.laboratory.name if document.laboratory else None,
        "accession_number": document.accession_number,
        "ordering_physician": document.ordering_physician,
        "patient_name_on_doc": document.patient_name_on_doc,
        "patient_birth_date": _date_text(document.patient_birth_date),
        "collected_on": _date_text(document.collected_on),
        "issued_at": _datetime_text(document.issued_at),
        "page_count": document.page_count,
        "extraction_method": document.extraction_method,
    }
    if include_text:
        data["page_text"] = document.page_text
    data["results"] = [serialize_result(r) for r in results]
    return data


def failed_document_entry(
    *, source_file: str, sha256: str, error_code: str, error_params: dict | None = None
) -> dict:
    """Arquivo que nem chegou ao pipeline (não é PDF, ilegível, grande demais)."""
    return {
        "source_file": source_file,
        "sha256": sha256,
        "keep": True,
        "status": DocumentStatus.FAILED,
        "error_code": error_code,
        "error_params": error_params or {},
        "warnings": [],
        "laboratory": None,
        "accession_number": "",
        "ordering_physician": "",
        "patient_name_on_doc": "",
        "patient_birth_date": None,
        "collected_on": None,
        "issued_at": None,
        "page_count": 0,
        "extraction_method": "",
        "results": [],
    }


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def build_file(documents: Iterable[dict]) -> dict:
    """Envelope do arquivo, com os laudos em ordem estável (nome do arquivo, depois hash)."""
    ordered = sorted(documents, key=lambda d: (d["source_file"].lower(), d["sha256"]))
    return {
        "format": FORMAT,
        "generated_at": timezone.now().isoformat(timespec="seconds"),
        "contains_health_data": True,
        "note": NOTE,
        "documents": ordered,
    }


# --------------------------------------------------------------------------------------------
# Ler e validar (sem banco)
# --------------------------------------------------------------------------------------------

_TEXT_LIMITS: dict[str, int | None] = {
    "name_raw": 255,
    "section_raw": 255,
    "value_raw": 255,
    "unit_raw": 60,
    "reference_raw": None,
    "flag_printed": 20,
    "value_text": 255,
    "unit": 40,
    "interpretation": 255,
    "method": 255,
    "specimen": 100,
    "performing_lab": 255,
    "notes": None,
    "source_line": None,
}
_DECIMAL_KEYS = ("value_numeric", "value_canonical", "ref_low", "ref_high")
_BOOL_KEYS = ("ref_low_inclusive", "ref_high_inclusive")
_CHOICES = {
    "origin": (ResultOrigin, ResultOrigin.CURRENT),
    "comparator": (Comparator, Comparator.EQ),
    "ref_kind": (ReferenceKind, ReferenceKind.NONE),
    "flag": (ResultFlag, ResultFlag.UNKNOWN),
}
_DECIDED_REVIEW = (ReviewStatus.CONFIRMED, ReviewStatus.USER_CORRECTED)
_MAX_ABS_DECIMAL = Decimal(10) ** 14
_QUANTUM = Decimal("0.000001")


@dataclass
class ParsedResult:
    index: int
    fields: dict
    slug: str | None
    keep: bool
    reviewed: bool
    review_status: str
    fingerprint: str | None


@dataclass
class ParsedDocument:
    index: int
    source_file: str
    sha256: str
    status: str
    keep: bool
    error_code: str
    fields: dict
    laboratory_name: str
    page_text: list
    results: list[ParsedResult] = field(default_factory=list)


class _Where:
    """Posição no arquivo para as mensagens de erro (nunca o valor)."""

    def __init__(self, document: int, source: str = "", result: int | None = None) -> None:
        self.document, self.source, self.result = document, source, result

    def fail(self, key: str, problem: str) -> ReportsFileError:
        where = f"documento {self.document}"
        if self.source:
            where += f" ({self.source})"
        if self.result is not None:
            where += f", resultado {self.result}"
        return ReportsFileError(f"{where}: campo '{key}' {problem}")


def _get_str(data: dict, key: str, where: _Where, *, limit: int | None, required=False) -> str:
    value = data.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise where.fail(key, "deve ser texto")
    if required and not value.strip():
        raise where.fail(key, "é obrigatório")
    if limit is not None and len(value) > limit:
        raise where.fail(key, f"passa de {limit} caracteres")
    return value


def _get_int(data: dict, key: str, where: _Where, *, default: int, minimum: int = 0) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise where.fail(key, f"deve ser um inteiro >= {minimum}")
    return value


def _get_bool(data: dict, key: str, where: _Where, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise where.fail(key, "deve ser true ou false")
    return value


def _get_decimal(data: dict, key: str, where: _Where) -> Decimal | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise where.fail(key, "deve ser um número")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise where.fail(key, "não é um número válido") from None
    if not number.is_finite() or abs(number) >= _MAX_ABS_DECIMAL:
        raise where.fail(key, "está fora do intervalo aceito")
    return number.quantize(_QUANTUM)


def _get_date(data: dict, key: str, where: _Where) -> date | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    parsed = None
    if isinstance(value, str):
        try:
            parsed = parse_date(value)
        except ValueError:
            parsed = None
    if parsed is None:
        raise where.fail(key, "deve ser uma data AAAA-MM-DD")
    return parsed


def _get_datetime(data: dict, key: str, where: _Where) -> datetime | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    parsed = None
    if isinstance(value, str):
        try:
            parsed = parse_datetime(value)
            if parsed is None and (day := parse_date(value)) is not None:
                parsed = datetime.combine(day, datetime.min.time())
        except ValueError:
            parsed = None
    if parsed is None:
        raise where.fail(key, "deve ser data e hora ISO (AAAA-MM-DDTHH:MM:SS+00:00)")
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _get_warnings(data: dict, where: _Where, key: str = "warnings") -> list[dict]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise where.fail(key, "deve ser uma lista")
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("params", {}), dict)
        ):
            raise where.fail(key, "deve ser uma lista de {code, params}")
    return [{"code": w["code"], "params": w.get("params", {})} for w in value]


def _get_choice(data: dict, key: str, where: _Where, enum, default) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or value not in enum.values:
        raise where.fail(key, f"deve ser um de: {', '.join(enum.values)}")
    return value


def _parse_result(data, index: int, where: _Where) -> ParsedResult:
    if not isinstance(data, dict):
        raise where.fail("results", "deve conter objetos")
    fields: dict = {}
    for key, limit in _TEXT_LIMITS.items():
        fields[key] = _get_str(data, key, where, limit=limit, required=key == "name_raw")
    for key in _DECIMAL_KEYS:
        fields[key] = _get_decimal(data, key, where)
    for key in _BOOL_KEYS:
        fields[key] = _get_bool(data, key, where, default=True)
    for key, (enum, default) in _CHOICES.items():
        fields[key] = _get_choice(data, key, where, enum, default)
    fields["page"] = _get_int(data, "page", where, default=1, minimum=1)
    fields["position"] = _get_int(data, "position", where, default=0)
    fields["collected_at"] = _get_datetime(data, "collected_at", where)
    fields["warnings"] = _get_warnings(data, where)
    bands = data.get("ref_bands", [])
    if not isinstance(bands, list) or not all(isinstance(b, dict) for b in bands):
        raise where.fail("ref_bands", "deve ser uma lista de objetos")
    fields["ref_bands"] = bands

    slug = data.get("analyte")
    if slug is not None and (not isinstance(slug, str) or not slug.strip()):
        raise where.fail("analyte", "deve ser o slug do exame (texto) ou null")

    status = data.get("review_status", ReviewStatus.AUTO)
    if status == "accepted":
        status = ReviewStatus.CONFIRMED
    if not isinstance(status, str) or status not in ReviewStatus.values:
        raise where.fail("review_status", "deve ser auto, needs_review, accepted ou user_corrected")

    stamp = data.get("fingerprint")
    if stamp is not None and not isinstance(stamp, str):
        raise where.fail("fingerprint", "deve ser texto")
    return ParsedResult(
        index=index,
        fields=fields,
        slug=slug.strip() if slug else None,
        keep=_get_bool(data, "keep", where, default=True),
        reviewed=_get_bool(data, "reviewed", where, default=False),
        review_status=status,
        fingerprint=stamp,
    )


def _parse_document(data, index: int) -> ParsedDocument:
    if not isinstance(data, dict):
        raise ReportsFileError(f"documento {index}: deve ser um objeto")
    source = data.get("source_file")
    where = _Where(index, source if isinstance(source, str) else "")
    source_file = Path(_get_str(data, "source_file", where, limit=None, required=True)).name
    if not source_file:
        raise where.fail("source_file", "é obrigatório")
    where = _Where(index, source_file)

    sha = data.get("sha256")
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(c not in "0123456789abcdefABCDEF" for c in sha)
    ):
        raise where.fail("sha256", "deve ter 64 caracteres hexadecimais")
    status = data.get("status")
    if status not in (*IMPORTABLE_STATUSES, *SKIPPED_STATUSES):
        raise where.fail("status", "deve ser completed, needs_review ou failed")

    fields = {
        "accession_number": _get_str(data, "accession_number", where, limit=64),
        "ordering_physician": _get_str(data, "ordering_physician", where, limit=255),
        "patient_name_on_doc": _get_str(data, "patient_name_on_doc", where, limit=255),
        "patient_birth_date": _get_date(data, "patient_birth_date", where),
        "collected_on": _get_date(data, "collected_on", where),
        "issued_at": _get_datetime(data, "issued_at", where),
        "page_count": _get_int(data, "page_count", where, default=0),
        "extraction_method": data.get("extraction_method") or "",
        "warnings": _get_warnings(data, where),
    }
    if fields["extraction_method"] not in ExtractionMethod.values + [""]:
        raise where.fail("extraction_method", "tem um valor desconhecido")
    laboratory = data.get("laboratory")
    if laboratory is not None and not isinstance(laboratory, str):
        raise where.fail("laboratory", "deve ser texto ou null")
    page_text = data.get("page_text", [])
    if not isinstance(page_text, list):
        raise where.fail("page_text", "deve ser uma lista")

    document = ParsedDocument(
        index=index,
        source_file=source_file,
        sha256=sha.lower(),
        status=status,
        keep=_get_bool(data, "keep", where, default=True),
        error_code=str(data.get("error_code") or ""),
        fields=fields,
        laboratory_name=(laboratory or "").strip(),
        page_text=page_text,
    )
    results = data.get("results", [])
    if not isinstance(results, list):
        raise where.fail("results", "deve ser uma lista")
    if status in IMPORTABLE_STATUSES and document.keep:
        # Laudo que será pulado não precisa ter as linhas válidas.
        for number, item in enumerate(results, start=1):
            document.results.append(_parse_result(item, number, _Where(index, source_file, number)))
    return document


def parse_file(data) -> list[ParsedDocument]:
    """Valida o JSON inteiro antes de qualquer escrita. Levanta `ReportsFileError`."""
    if not isinstance(data, dict) or not isinstance(data.get("format"), str):
        raise ReportsFileError("Arquivo sem o campo 'format': não parece um export do MyExams.")
    version = data["format"]
    if version != FORMAT:
        if version.startswith("myexams-reports/"):
            raise ReportsFileError(
                f"Versão do formato não suportada: '{version}' (este comando lê '{FORMAT}')."
            )
        raise ReportsFileError(f"Formato desconhecido: '{version}' (esperado '{FORMAT}').")
    documents = data.get("documents")
    if not isinstance(documents, list):
        raise ReportsFileError("Campo 'documents' ausente ou não é uma lista.")
    return [_parse_document(item, number) for number, item in enumerate(documents, start=1)]


def load_file(path: Path) -> list[ParsedDocument]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ReportsFileError(f"Arquivo não encontrado: {path}") from None
    except (OSError, UnicodeDecodeError):
        raise ReportsFileError(f"Não foi possível ler o arquivo: {path}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportsFileError(
            f"O arquivo não é um JSON válido (linha {exc.lineno}, coluna {exc.colno})."
        ) from None
    return parse_file(data)


def slugs_in(documents: Iterable[ParsedDocument]) -> set[str]:
    return {
        r.slug
        for d in documents
        if d.keep and d.status in IMPORTABLE_STATUSES
        for r in d.results
        if r.keep and r.slug
    }


# --------------------------------------------------------------------------------------------
# Importar
# --------------------------------------------------------------------------------------------


@dataclass
class DocumentOutcome:
    name: str
    status: str  # created | skipped_existing | skipped_failed | skipped_keep | skipped_empty
    results: int = 0
    history: int = 0


@dataclass
class ImportReport:
    outcomes: list[DocumentOutcome] = field(default_factory=list)
    rows_dropped: int = 0
    rows_rederived: int = 0
    rows_accepted: int = 0
    history_deduplicated: int = 0

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def results(self) -> int:
        return sum(o.results for o in self.outcomes)

    @property
    def history(self) -> int:
        return sum(o.history for o in self.outcomes)


class _Importer:
    def __init__(self, user, analytes: dict[str, Analyte]) -> None:
        self.user = user
        self.analytes = analytes
        profile = Profile.objects.filter(user=user).first()
        self.sex = (profile.sex or None) if profile else None
        self.birth_date = profile.birth_date if profile else None
        self.resolver = AnalyteResolver(user=user)

    def build_row(self, parsed: ParsedResult, report: ImportReport) -> ExamResult:
        analyte = self.analytes[parsed.slug] if parsed.slug else None
        result = ExamResult(user=self.user, analyte=analyte, raw={"source": "import_json"})
        for key, value in parsed.fields.items():
            setattr(result, key, value)
        result.review_status = parsed.review_status

        intact = parsed.fingerprint == _fingerprint_of_result(result)
        if intact or parsed.reviewed:
            return result

        recompute_result(result, sex=self.sex, birth_date=self.birth_date, resolver=self.resolver)
        result.warnings = rederived_warnings(result, parsed.fields["warnings"])
        if parsed.review_status not in _DECIDED_REVIEW:
            result.review_status = (
                ReviewStatus.NEEDS_REVIEW
                if needs_review(result.warnings)
                else ReviewStatus.USER_CORRECTED
            )
        report.rows_rederived += 1
        return result

    def _already_exists(self, parsed: ParsedDocument) -> bool:
        """Mesmo hash, ou mesmo pedido no mesmo laboratório (as regras de `pipeline.persist`).

        Não cria o laboratório só para conferir: se ele não existe, nenhum laudo dele existe.
        """
        if ExamDocument.objects.filter(user=self.user, sha256=parsed.sha256).exists():
            return True
        name = parsed.laboratory_name
        laboratory = (
            Laboratory.objects.filter(normalized_name=normalize_name(name)).first()
            if name
            else None
        )
        if name and laboratory is None:
            return False
        return (
            find_accession_clash(self.user, laboratory, parsed.fields["accession_number"])
            is not None
        )

    def import_document(self, parsed: ParsedDocument, report: ImportReport) -> DocumentOutcome:
        name = parsed.source_file
        if parsed.status in SKIPPED_STATUSES:
            return DocumentOutcome(name, "skipped_failed")
        if not parsed.keep:
            return DocumentOutcome(name, "skipped_keep")
        kept = [r for r in parsed.results if r.keep]
        report.rows_dropped += len(parsed.results) - len(kept)
        if not any(r.fields["origin"] in CURRENT_ORIGINS for r in kept):
            return DocumentOutcome(name, "skipped_empty")

        if self._already_exists(parsed):
            return DocumentOutcome(name, "skipped_existing")
        laboratory = laboratory_for(parsed.laboratory_name)

        rows = [self.build_row(r, report) for r in kept]
        report.rows_accepted += sum(1 for r in kept if r.review_status == ReviewStatus.CONFIRMED)

        document = ExamDocument.objects.create(
            user=self.user,
            laboratory=laboratory,
            sha256=parsed.sha256,
            original_filename=name[:255],
            status=DocumentStatus.COMPLETED,
            upload_path="",
            page_text=parsed.page_text,
            **parsed.fields,
        )

        user_seen = known_history_keys(self.user, exclude=document)
        current_keys = {
            key
            for r in rows
            if r.origin in CURRENT_ORIGINS
            and (key := history_key(r.analyte, r.collected_at)) is not None
        }
        drop_superseded_previous(self.user, current_keys, exclude=document)

        stored: list[ExamResult] = []
        seen_previous: set = set()
        deduplicated = 0
        for row in rows:
            if row.origin == ResultOrigin.LAB_PREVIOUS:
                key = history_key(row.analyte, row.collected_at)
                if key is not None and (
                    key in user_seen or key in current_keys or key in seen_previous
                ):
                    deduplicated += 1
                    continue
                if key is not None:
                    seen_previous.add(key)
            row.document = document
            row.position = len(stored)
            stored.append(row)
        ExamResult.objects.bulk_create(stored)
        report.history_deduplicated += deduplicated

        document.status = review_status_of_document(stored, document.warnings)
        document.save(update_fields=["status", "updated_at"])
        current = sum(1 for r in stored if r.origin in CURRENT_ORIGINS)
        return DocumentOutcome(name, "created", results=current, history=len(stored) - current)


def import_documents(
    user, documents: list[ParsedDocument], analytes: dict[str, Analyte]
) -> ImportReport:
    """Grava um laudo por transação. Reexecutar é seguro: o que já existe é pulado."""
    importer = _Importer(user, analytes)
    report = ImportReport()
    for parsed in documents:
        with transaction.atomic():
            report.outcomes.append(importer.import_document(parsed, report))
    return report
