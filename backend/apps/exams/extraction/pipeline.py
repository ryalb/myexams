"""Pipeline de extração: PDF -> páginas -> blocos -> LLM -> normalização -> banco.

O LLM só transcreve. Números, unidades, faixas de referência, sinalização e
resolução de exame são determinísticos (módulos irmãos) e passam por checagens
de revisão (`validate`). O PDF nunca é lido aqui depois de `ingest`: o texto das
páginas fica no banco, o que permite reprocessar sem o arquivo.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from time import monotonic

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..codes import ErrorCode, WarningCode
from ..models import (
    CURRENT_ORIGINS,
    Analyte,
    Comparator,
    DocumentStatus,
    ExamDocument,
    ExamResult,
    ExtractionMethod,
    Laboratory,
    ReferenceKind,
    ResultFlag,
    ResultOrigin,
    ReviewStatus,
)
from . import blocks as blocks_mod
from . import layouts, softlab
from .analytes import AnalyteResolver
from .blocks import find_source_line
from .dates import age_on, parse_br_date, parse_br_datetime
from .errors import ExtractionError
from .flags import compute_flag, compute_qualitative_flag, printed_flag_direction
from .linearize import PageText, read_pages
from .llm import LLMClient
from .numbers import parse_number
from .reference import (
    Band,
    applicable_bands,
    interpretation_for,
    normal_band,
    parse_reference,
)
from .telemetry import OUTCOME_ERROR, OUTCOME_OK, OUTCOME_TIMEOUT, LLMTelemetry
from .text import compact, fold, normalize_name
from .units import convert, infer_unit, normalize_unit
from .validate import is_implausible, names_match, needs_review, value_is_grounded, warning

logger = logging.getLogger(__name__)

HEADER_MAX_CHARS = 4000
# O LLM só é chamado para o cabeçalho quando o regex acha menos que isto (de 5 campos).
HEADER_REGEX_MIN_FIELDS = 3
_DATE_TIME = r"\d{2}/\d{2}/\d{4}(?:[\s\-–|]*\d{2}:\d{2}(?::\d{2})?)?"
# "O.S: 021-..." (com separador) ou "O.S 021-..." (sem, mas só se o número começa por dígito).
# Laudo no layout "ficha": `Ficha: | 123456` (o número do pedido). `Data da Ficha:` não conta.
_ACCESSION = re.compile(
    r"\bO\.?\s?S\b\.?\s*[:|]\s*\|?\s*(\w[\w\-./]+)|\bO\.\s?S\.?\s+(\d[\w\-./]*)"
    r"|(?<!da )\bficha\s*:\s*\|?\s*(\d[\w\-./]*)"
    r"|\bpedido\s*:\s*\|?\s*(\d[\w\-./]*)|\bn[uú]mero\s*\.?\s*:\s*\|?\s*(\d[\w\-./]*)",
    re.IGNORECASE,
)
_BIRTH = re.compile(r"nasc(?:imento)?\.?\s*:?\s*\|?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
_ISSUED = re.compile(
    rf"(?:emiss[aã]o|data da ficha|data cadastro)\s*:?\s*\|?\s*({_DATE_TIME})", re.IGNORECASE
)
# Layout Softl@b: `Pedido: 999000-01 NOME DO PACIENTE` (o nome vem logo depois do número).
_PATIENT = re.compile(
    r"cliente\s*:?\s*\|?\s*([^|\n]+?)(?:\s*\||\s+O\.?S\b|$)"
    r"|\bpedido\s*:\s*\|?\s*\d[\w\-./]*\s+([^|\n]+?)(?:\s*\||$)",
    re.IGNORECASE | re.MULTILINE,
)
# `Médico:` (layout "ficha") pode vir vazio: `Médico: | Data da Ficha: | ...`.
_PHYSICIAN = re.compile(
    r"solicitante\s*:?\s*\|?\s*([^|\n]+?)(?:\s*\||$)"
    r"|m[eé]dico\s*:\s*\|?(?!\s*data\b)\s*([^|\n]+?)(?:\s*\||$)",
    re.IGNORECASE | re.MULTILINE,
)
_COLLECTED = re.compile(rf"data da coleta[\s:|]*({_DATE_TIME})", re.IGNORECASE)
_ATTENDED = re.compile(rf"atendimento\s*:?\s*\|?\s*({_DATE_TIME})", re.IGNORECASE)

# Laboratório: o logotipo costuma ser imagem, então o nome vem do domínio do e-mail/site.
_EMAIL_DOMAIN = re.compile(r"[\w.+\-]+@([\w\-]+(?:\.[\w\-]+)+)")
_WEBSITE = re.compile(r"\bwww\.([\w\-]+(?:\.[\w\-]+)*)", re.IGNORECASE)
_GENERIC_DOMAINS = frozenset(
    {"gmail", "hotmail", "outlook", "yahoo", "uol", "bol", "icloud", "terra", "live", "msn"}
)
# Rótulos de segundo nível que acompanham o TLD (`exemplo.com.br`, `exemplo.org.br`).
_SECOND_LEVEL_LABELS = frozenset(
    {"com", "org", "net", "gov", "edu", "med", "ind", "mil", "blog", "eco", "adm", "esp"}
)
MIN_DOMAIN_STEM_LETTERS = 3
# Faixas da página onde o papel timbrado do laboratório mora (topo e rodapé).
LAB_DOMAIN_TOP_LINES = 15
LAB_DOMAIN_BOTTOM_LINES = 10
# Layout Softl@b: rodapé `LAB. NOME DO LABORATÓRIO CRM-PJ 31` (antes de `DIREÇÃO: ...`).
_LAB_FOOTER = re.compile(
    r"^\s*LAB\.\s+([^\W\d_][^\n|]*?)\s+CRM[\s\-]*PJ\b", re.IGNORECASE | re.MULTILINE
)
_NAME_PARTICLES = frozenset({"de", "da", "do", "dos", "das", "e"})


@dataclass
class Header:
    laboratory_name: str = ""
    accession_number: str = ""
    patient_name: str = ""
    patient_birth_date: object = None
    ordering_physician: str = ""
    issued_at: datetime | None = None
    collected_at: datetime | None = None


@dataclass
class Analysis:
    header: Header
    drafts: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class Outcome:
    status: str
    stats: dict


def _text(value) -> str:
    return "" if value is None else str(value).strip()


_COLUMN_MARKER = re.compile(r"\s*\|\s*")


def _clean_field(value: str) -> str:
    """Remove o separador de colunas ' | ' do linearizador que o LLM às vezes copia."""
    return " ".join(_COLUMN_MARKER.sub(" ", value).split())


def _is_integer_count_unit(unit: str) -> bool:
    """Contagem por µL é inteira; já 'mil/µL' e 'milhões/µL' têm casas decimais."""
    return unit == "/µL"


def _domain_stem(domain: str) -> str:
    """Rótulo que identifica o domínio: sem o TLD (qualquer um) nem o segundo nível.

    `exemplo.com.br` -> `exemplo`; `exemplo.ca` -> `exemplo` (um TLD fora de uma lista fixa
    não pode virar o nome do laboratório); `www.exemplo` (sem TLD) -> `exemplo`.
    """
    labels = [label for label in domain.lower().split(".") if label]
    if len(labels) > 1:
        labels.pop()
    while len(labels) > 1 and labels[-1] in _SECOND_LEVEL_LABELS:
        labels.pop()
    return labels[-1] if labels else ""


def _title_case_name(raw: str) -> str:
    """`MARCELO DA SILVA` -> `Marcelo da Silva` (acentos preservados, partículas minúsculas)."""
    words = raw.split()
    return " ".join(
        word.lower() if word.lower() in _NAME_PARTICLES else word.title() for word in words
    )


def laboratory_from_footer(text: str) -> str:
    """Nome do rodapé Softl@b `LAB. <NOME> CRM-PJ <nº>` ("" se não há).

    `LAB.` é a abreviatura de "Laboratório": `LAB. JOSE EXEMPLO CRM-PJ 12` ->
    "Laboratório Jose Exemplo".
    """
    for match in _LAB_FOOTER.finditer(text):
        name = " ".join(match.group(1).split())
        if sum(ch.isalpha() for ch in name) >= MIN_DOMAIN_STEM_LETTERS:
            return f"Laboratório {_title_case_name(name)}"
    return ""


def laboratory_from_text(text: str, domain_text: str | None = None) -> str:
    """Nome do laboratório ("" se não há), em melhor esforço e determinístico.

    1. Rodapé `LAB. <NOME> CRM-PJ <nº>` (Softl@b), que traz o nome por extenso.
    2. Senão, o primeiro domínio de e-mail/site em `domain_text` (padrão: `text`):
       `emilioribas.com.br` -> "Emilioribas". Domínios de provedores genéricos (gmail etc.)
       e radicais com menos de 3 letras são ignorados. Quem chama passa só as faixas de topo
       e rodapé das páginas: um endereço no corpo do laudo (nota de rodapé, bibliografia de
       um valor de referência) não diz quem emitiu o laudo.
    """
    if footer := laboratory_from_footer(text):
        return footer
    for pattern in (_WEBSITE, _EMAIL_DOMAIN):
        for match in pattern.finditer(text if domain_text is None else domain_text):
            stem = _domain_stem(match.group(1))
            letters = sum(ch.isalpha() for ch in stem)
            if letters >= MIN_DOMAIN_STEM_LETTERS and stem not in _GENERIC_DOMAINS:
                return stem.replace("-", " ").title()
    return ""


def _first_group(match: re.Match[str] | None) -> str:
    return next((g.strip() for g in (match.groups() if match else ()) if g), "")


def parse_header(raw: dict, header_text: str, laboratory_hint: str = "") -> Header:
    """Cabeçalho do LLM (se houver), completado por regex quando falta campo.

    Com `raw` vazio, é só o regex: usado antes de decidir se vale chamar o LLM.
    """

    def pick(key: str, pattern: re.Pattern[str] | None) -> str:
        value = _text(raw.get(key))
        if not value and pattern:
            value = _first_group(pattern.search(header_text))
        return value

    def pick_name(key: str, pattern: re.Pattern[str]) -> str:
        """Nome de pessoa: ignora capturas sem letras.

        O cabeçalho costuma ter `Cód. Cliente: 100200` ANTES de `Cliente: NOME`; a primeira
        ocorrência do rótulo é o código, não o nome.
        """
        value = _text(raw.get(key))
        if value:
            return value
        for match in pattern.finditer(header_text):
            candidate = _first_group(match)
            if re.search(r"[^\W\d_]{2}", candidate):
                return candidate
        return ""

    collected = _text(raw.get("collected_at")) or _first_group(
        _COLLECTED.search(header_text) or _ATTENDED.search(header_text)
    )
    return Header(
        laboratory_name=_clean_field(_text(raw.get("laboratory_name")) or laboratory_hint),
        accession_number=_clean_field(pick("accession_number", _ACCESSION)),
        patient_name=_clean_field(pick_name("patient_name", _PATIENT)),
        patient_birth_date=parse_br_date(pick("patient_birth_date", _BIRTH)),
        ordering_physician=_clean_field(pick_name("ordering_physician", _PHYSICIAN)),
        issued_at=parse_br_datetime(pick("issued_at", _ISSUED)),
        collected_at=parse_br_datetime(collected),
    )


def header_fields_found(header: Header) -> int:
    """Quantos dos 5 campos de identificação do laudo foram achados."""
    return sum(
        1
        for value in (
            header.accession_number,
            header.patient_name,
            header.patient_birth_date,
            header.issued_at,
            header.ordering_physician,
        )
        if value
    )


def _coerce_row(raw: dict) -> dict:
    """Só as colunas que o modelo transcreve; qualquer outra chave é ignorada."""
    keys = ("name", "value_text", "unit", "reference_text", "printed_flag")
    row = {key: _text(raw.get(key)) for key in keys}
    row["is_calculated"] = raw.get("is_calculated") is True
    return row


# Linhas de âncora/metadado do bloco: nunca são título de seção.
_META_PREFIXES = (
    "material",
    "metodo",
    "data da coleta",
    "valor de referencia",
    "valores de referencia",
    "liberado",
    "anteriores",
)
_SECTION_WORDS = re.compile(r"[^\W\d_]+(?: [^\W\d_]+)*")
_COLUMN_HEADER = re.compile(r"refer[eê]ncia|resultado|unidade|valor", re.IGNORECASE)
SECTION_MAX_CHARS = 40


_PCT_ABS_SUFFIXES = ("_pct", "_abs")


def resolve_source_line(block: blocks_mod.Block, row: dict) -> str:
    """Linha de origem do resultado.

    Aceita a linha que a camada de origem indicou (regras ou número da linha dado pelo
    modelo) só se ela existir no bloco **e** contiver o valor; senão deriva por busca.
    """
    claimed = _text(row.get("source_line"))
    if claimed and claimed in block.lines:
        needle = compact(row["value_text"])
        if not needle or needle in compact(claimed):
            return claimed
    return find_source_line(block, row["name"], row["value_text"])


def _is_section_title(line: str) -> bool:
    folded = fold(line.strip())
    if not line.strip() or folded.startswith(_META_PREFIXES):
        return False
    head, sep, rest = line.partition(" | ")
    # "ERITROGRAMA | Valores de referência": cabeçalho de colunas ao lado do título. Qualquer
    # outra linha em colunas é um resultado (ex.: "ASPECTO | LÍMPIDO"), não uma seção.
    if sep and (any(ch.isdigit() for ch in rest) or not _COLUMN_HEADER.search(rest)):
        return False
    head = head.strip()
    return (
        len(head) <= SECTION_MAX_CHARS
        and _SECTION_WORDS.fullmatch(head) is not None
        and head == head.upper()
    )


def find_section(block: blocks_mod.Block, source_line: str) -> str:
    """Título de seção em CAIXA ALTA mais próximo ANTES da linha de origem ("" se não houver).

    Ignora o título do próprio exame (primeira linha do bloco) e linhas de metadados.
    """
    if not source_line or source_line not in block.lines:
        return ""
    index = block.lines.index(source_line)
    for position in range(index - 1, -1, -1):
        if position == 0 and block.title:
            break
        line = block.lines[position]
        if _is_section_title(line):
            return line.partition(" | ")[0].strip()
    return ""


def _has_competing_normal_bands(selected: list[Band]) -> bool:
    return sum(1 for band in selected if band.normal_like and not band.qualitative) > 1


def band_kind(band: Band | None) -> str:
    if band is None:
        return ReferenceKind.NONE
    if band.qualitative:
        return ReferenceKind.QUALITATIVE
    if band.low is not None and band.high is not None:
        return ReferenceKind.RANGE
    return ReferenceKind.UPPER if band.high is not None else ReferenceKind.LOWER


@dataclass
class PatientContext:
    sex: str | None
    birth_date: object


def build_draft(
    row: dict,
    block: blocks_mod.Block,
    *,
    resolver: AnalyteResolver,
    patient: PatientContext,
    fallback_collected: datetime | None,
    vision: bool,
) -> dict | None:
    """Uma linha do LLM -> campos de ExamResult (ou None se não for um resultado)."""
    name = row["name"]
    value_text = row["value_text"]
    if not name or (not value_text and not row["reference_text"]):
        return None  # título de seção ou ruído

    warnings: list[dict] = []
    if vision:
        warnings.append(warning(WarningCode.PAGE_READ_BY_VISION))

    unit_raw = row["unit"]
    unit = normalize_unit(unit_raw) or infer_unit(row["reference_text"])
    if not unit and row.get("unit_hint"):
        # Duas colunas de resultado sem unidade impressa: só para exames que têm versão
        # percentual/absoluta no catálogo, o 1º valor é "%" e o 2º é contagem por µL.
        family = resolver.resolve(name, "").analyte
        if family is not None and family.slug.endswith(_PCT_ABS_SUFFIXES):
            unit = normalize_unit(row["unit_hint"])
    resolution = resolver.resolve(name, unit)
    analyte: Analyte | None = resolution.analyte
    if analyte is None:
        warnings.append(warning(WarningCode.UNMAPPED_ANALYTE))

    plausible = None
    if analyte and analyte.plausible_min is not None and analyte.plausible_max is not None:
        if not unit or unit == analyte.canonical_unit:
            plausible = (analyte.plausible_min, analyte.plausible_max)
    parsed = parse_number(value_text, plausible, integer_only=_is_integer_count_unit(unit))
    if parsed.ambiguous:
        warnings.append(warning(WarningCode.AMBIGUOUS_NUMBER_FORMAT))

    value = parsed.value
    if not value_text:
        warnings.append(warning(WarningCode.VALUE_MISSING))
    elif not value_is_grounded(value_text, block.text):
        warnings.append(warning(WarningCode.VALUE_NOT_IN_PAGE))

    canonical = None
    if value is not None:
        if analyte and analyte.canonical_unit:
            if not unit:
                warnings.append(warning(WarningCode.UNIT_MISSING))
                canonical = value
            else:
                canonical = convert(value, unit, analyte.canonical_unit, resolver.factors(analyte))
                if canonical is None:
                    warnings.append(warning(WarningCode.UNIT_NOT_CONVERTIBLE, unit=unit))
        elif not unit:
            warnings.append(warning(WarningCode.UNIT_MISSING))
        if is_implausible(
            canonical,
            analyte.plausible_min if analyte else None,
            analyte.plausible_max if analyte else None,
        ):
            warnings.append(warning(WarningCode.IMPLAUSIBLE_VALUE))

    collected = parse_br_datetime(block.collected_at) or fallback_collected
    on_date = _local_date(collected) if collected else timezone.localdate()
    age = age_on(patient.birth_date, on_date)

    # Layout "ficha": o texto impresso é guardado inteiro, mas a faixa só é lida de um trecho que
    # as regras consideram seguro (nunca matriz por idade/sexo nem prosa).
    bands = parse_reference(row.get("parse_reference_text", row["reference_text"]))
    selected, needs_sex = applicable_bands(bands, patient.sex, age)
    if row.get("strict_reference") and _has_competing_normal_bands(selected):
        # Mais de uma faixa "normal" vale para este paciente (ex.: com e sem jejum): escolher
        # uma seria chute. O texto impresso fica e a faixa fica sem leitura.
        bands, selected = [], []
    normal = normal_band(selected)
    if row["reference_text"] and not bands:
        warnings.append(warning(WarningCode.REFERENCE_NOT_PARSED))
    elif bands and normal is None and not any(b.qualitative for b in selected):
        code = (
            WarningCode.SEX_REQUIRED_FOR_REFERENCE
            if needs_sex
            else WarningCode.REFERENCE_NOT_APPLICABLE
        )
        warnings.append(warning(code))

    interpretation = ""
    if value is not None:
        flag = compute_flag(value, parsed.comparator, normal)
        if value is not None and selected:
            interpretation = interpretation_for(selected, value)
    else:
        flag = compute_qualitative_flag(parsed.text or value_text, selected)

    direction = printed_flag_direction(row["printed_flag"])
    if (
        direction
        and flag in (ResultFlag.LOW, ResultFlag.HIGH, ResultFlag.NORMAL)
        and direction != flag
    ):
        warnings.append(warning(WarningCode.FLAG_MISMATCH))

    qualitative_band = next((b for b in selected if b.qualitative), None)
    source_line = resolve_source_line(block, row)
    section = row["section"] if "section" in row else find_section(block, source_line)
    return {
        "origin": ResultOrigin.CURRENT,
        "page": block.page,
        "name_raw": name[:255],
        "section_raw": section[:255],
        "value_raw": value_text[:255],
        "unit_raw": unit_raw[:60],
        "reference_raw": row["reference_text"],
        "flag_printed": row["printed_flag"][:20],
        "comparator": Comparator(parsed.comparator),
        "value_numeric": value,
        "value_text": "" if value is not None else (parsed.text or value_text)[:255],
        "unit": unit[:40],
        "value_canonical": canonical,
        "ref_kind": band_kind(normal or qualitative_band),
        "ref_low": normal.low if normal else None,
        "ref_high": normal.high if normal else None,
        "ref_low_inclusive": normal.low_inclusive if normal else True,
        "ref_high_inclusive": normal.high_inclusive if normal else True,
        "ref_bands": [b.to_json() for b in bands],
        "flag": flag,
        "interpretation": interpretation[:255],
        "collected_at": collected,
        # Metadados do bloco (o modelo não os transcreve).
        "method": block.method[:255],
        "specimen": block.specimen[:100],
        "performing_lab": block.performing_lab[:255],
        "notes": "",
        "review_status": ReviewStatus.NEEDS_REVIEW if needs_review(warnings) else ReviewStatus.AUTO,
        "warnings": warnings,
        "source_line": source_line,
        "raw": row,
        "analyte": analyte,
        "_calculated": row["is_calculated"] or bool(analyte and analyte.is_calculated),
    }


def build_previous_drafts(
    block,
    primary: dict,
    resolver: AnalyteResolver,
    previous: list[tuple[date, str]] | None = None,
) -> list[dict]:
    """Linhas 'Anteriores' do laudo -> resultados históricos do mesmo exame.

    `previous` são os anteriores da própria linha de resultado (layout Softl@b, um painel
    pode trazer vários exames); sem ele valem os do bloco.
    """
    analyte: Analyte | None = primary["analyte"]
    pairs = block.previous if previous is None else previous
    if analyte is None or not pairs:
        return []
    plausible = None
    if analyte.plausible_min is not None and analyte.plausible_max is not None:
        plausible = (analyte.plausible_min, analyte.plausible_max)
    drafts = []
    # O laudo não imprime a unidade nas anteriores: herda a do resultado atual.
    unit = primary["unit"]
    integer_only = _is_integer_count_unit(unit)
    for day, raw_value in pairs:
        parsed = parse_number(raw_value, plausible, integer_only=integer_only)
        if parsed.value is None:
            continue
        canonical = (
            convert(parsed.value, unit, analyte.canonical_unit, resolver.factors(analyte))
            if unit
            else parsed.value
        )
        warnings = [warning(WarningCode.AMBIGUOUS_NUMBER_FORMAT)] if parsed.ambiguous else []
        drafts.append(
            {
                "origin": ResultOrigin.LAB_PREVIOUS,
                "page": block.page,
                "name_raw": primary["name_raw"],
                "section_raw": "",
                "value_raw": raw_value[:255],
                "unit_raw": "",
                "reference_raw": "",
                "flag_printed": "",
                "comparator": Comparator(parsed.comparator),
                "value_numeric": parsed.value,
                "value_text": "",
                "unit": unit,
                "value_canonical": canonical,
                "ref_kind": ReferenceKind.NONE,
                "ref_low": None,
                "ref_high": None,
                "ref_low_inclusive": True,
                "ref_high_inclusive": True,
                "ref_bands": [],
                "flag": ResultFlag.UNKNOWN,
                "interpretation": "",
                "collected_at": timezone.make_aware(datetime.combine(day, time.min)),
                "method": "",
                "specimen": primary["specimen"],
                "performing_lab": primary["performing_lab"],
                "notes": "",
                "review_status": ReviewStatus.NEEDS_REVIEW if warnings else ReviewStatus.AUTO,
                "warnings": warnings,
                "source_line": "",
                "raw": {"previous_of": primary["name_raw"]},
                "analyte": analyte,
            }
        )
    return drafts


def ingest(
    document: ExamDocument,
    llm: LLMClient,
    pdf_path: Path,
    telemetry: LLMTelemetry | None = None,
) -> list[PageText]:
    telemetry = telemetry if telemetry is not None else LLMTelemetry()
    vision_page: list[int | None] = [None]

    def transcribe(png: bytes) -> str:
        return _measured(telemetry, llm, "vision", vision_page[0], llm.transcribe_page, png)

    def on_vision(number: int) -> None:
        vision_page[0] = number

    pages = read_pages(pdf_path, transcribe, settings.EXAMS_MAX_PAGES, on_vision)
    document.page_count = len(pages)
    methods = {p.method for p in pages}
    document.extraction_method = (
        ExtractionMethod.MIXED
        if len(methods) > 1
        else ExtractionMethod.VISION
        if methods == {"vision"}
        else ExtractionMethod.TEXT
    )
    # Texto já sem CPF/RG (ver linearize.redact); fica no banco para reprocessar.
    document.page_text = [{"number": p.number, "method": p.method, "lines": p.lines} for p in pages]
    document.save(update_fields=["page_count", "extraction_method", "page_text", "updated_at"])
    return pages


def pages_from_document(document: ExamDocument) -> list[PageText]:
    return [PageText(p["number"], list(p["lines"]), p["method"]) for p in document.page_text]


ProgressCallback = Callable[[int, int], None]

# Falha de infraestrutura: o próximo bloco falharia igual, então não vale insistir.
# Já `INFERENCE_TIMEOUT` e `INVALID_MODEL_OUTPUT` podem ser só um bloco difícil.
_BLOCK_SKIPPABLE = frozenset({ErrorCode.INFERENCE_TIMEOUT, ErrorCode.INVALID_MODEL_OUTPUT})

# Blocos maiores que isto vão ao LLM em pedaços: ninguém gera 26 linhas de uma vez a 4 tok/s.
MAX_LLM_BLOCK_LINES = 14
MAX_CHUNK_LINES = 12
_ANCHOR_LINE = re.compile(r"^material\s*[:|]")
_METHOD_LINE = re.compile(r"^m[eé]todos?\s*:", re.IGNORECASE)


@dataclass
class LLMChunk:
    """Pedaço de um bloco enviado ao LLM numa chamada."""

    lines: list[str]
    origin: list[int]  # índice, no bloco original, de cada linha do pedaço
    repeated: frozenset[int] = frozenset()  # linhas de prefixo (título/meta) repetidas
    first: bool = True

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def split_for_llm(block: blocks_mod.Block) -> list[LLMChunk]:
    """Bloco -> pedaços de até 12 linhas de tabela, cada um precedido pelo título e pelas
    primeiras linhas `Material:`/`Método:`. Blocos pequenos seguem inteiros."""
    count = len(block.lines)
    if count <= MAX_LLM_BLOCK_LINES:
        return [LLMChunk(list(block.lines), list(range(count)))]
    prefix = [0] if block.title else []
    for pattern in (_ANCHOR_LINE, _METHOD_LINE):
        found = next(
            (
                i
                for i, line in enumerate(block.lines)
                if i not in prefix and pattern.match(fold(line))
            ),
            None,
        )
        if found is not None:
            prefix.append(found)
    prefix.sort()
    body = [i for i in range(count) if i not in prefix]
    # Pedaços de tamanho parecido (26 linhas -> 9+9+8), nunca com mais de MAX_CHUNK_LINES.
    parts = -(-len(body) // MAX_CHUNK_LINES)
    size = -(-len(body) // parts)
    chunks = []
    for start in range(0, len(body), size):
        origin = [*prefix, *body[start : start + size]]
        chunk = LLMChunk([block.lines[i] for i in origin], origin, frozenset(prefix), start == 0)
        chunks.append(chunk)
    return chunks


@dataclass
class _Plan:
    """O que fazer com um bloco: pular, ler por regras ou enviar ao LLM."""

    block: blocks_mod.Block
    kind: str  # "skip" | "rule" | "llm"
    rows: list[dict] = field(default_factory=list)
    chunks: list[LLMChunk] = field(default_factory=list)
    table: bool = False  # lido pelas regras de tabela (e não pelas de linha única)

    @property
    def units(self) -> int:
        return len(self.chunks) if self.kind == "llm" else 1


def _plan(block: blocks_mod.Block) -> _Plan:
    if block.layout == "softlab" and (rows := softlab.extract_softlab_block(block)):
        # Antes do "pular": um resultado qualitativo (`VDRL | NEGATIVO`) é um bloco de uma linha.
        return _Plan(block, "rule", rows=rows, table=len(rows) > 1)
    if len(block.lines) < 2 and not any(ch.isdigit() for ch in block.text):
        return _Plan(block, "skip")
    if block.layout == "ficha":
        rows = layouts.extract_ficha_block(block)
        if rows:
            return _Plan(block, "rule", rows=rows, table=len(rows) > 1)
        return _Plan(block, "llm", chunks=split_for_llm(block))
    if block.layout == "softlab":
        return _Plan(block, "llm", chunks=split_for_llm(block))  # as regras já recusaram o bloco
    rows = layouts.extract_inline_block(block)
    if rows:
        return _Plan(block, "rule", rows=rows)
    rows = layouts.extract_table_rows(block)
    if rows:
        return _Plan(block, "rule", rows=rows, table=True)
    return _Plan(block, "llm", chunks=split_for_llm(block))


UNREADABLE_TITLE_MAX = 80


def unreadable_title(block: blocks_mod.Block) -> str:
    """Nome do exame como impresso, para o aviso `block_unreadable`.

    Só o nome: o que vem depois de `|` ou `:` (valor, unidade, referência) é cortado, e o
    material (`, soro`) também. Sem título, vale a primeira linha não vazia do bloco, com o
    mesmo corte. Nunca devolve outro trecho do bloco; no máximo `UNREADABLE_TITLE_MAX` caracteres.
    """

    def name_of(line: str) -> str:
        head = line.split("|", 1)[0].split(":", 1)[0]
        return blocks_mod.split_title(" ".join(head.split()))[0]

    candidates = [block.title, *block.lines]
    title = next((name for line in candidates if (name := name_of(line))), "")
    return title[:UNREADABLE_TITLE_MAX].rstrip()


@dataclass
class _Calls:
    count: int = 0


def _clock() -> float:
    """Relógio monotônico (isolado para os testes simularem o tempo)."""
    return monotonic()


def _measured(
    telemetry: LLMTelemetry,
    llm: object,
    kind: str,
    page: int | None,
    call: Callable,
    *args,
    on_elapsed: Callable[[float], None] | None = None,
):
    """Executa uma chamada ao LLM e registra duração, tokens e desfecho (também se falhar).

    O tempo de parede vem do mesmo relógio do orçamento (`_clock`); só é usado quando o
    cliente não mede por conta própria (fakes).
    """
    started = _clock()
    outcome = OUTCOME_OK
    try:
        return call(*args)
    except ExtractionError as exc:
        outcome = OUTCOME_TIMEOUT if exc.code == ErrorCode.INFERENCE_TIMEOUT else OUTCOME_ERROR
        raise
    except BaseException:
        outcome = OUTCOME_ERROR
        raise
    finally:
        elapsed = _clock() - started
        if on_elapsed is not None:
            on_elapsed(elapsed)
        telemetry.record_call(llm, kind, page, max(0.0, elapsed), outcome)


class _Budget:
    """Tempo de parede gasto em chamadas ao LLM neste laudo, contra um teto em segundos."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.spent = 0.0

    @property
    def exceeded(self) -> bool:
        return self.spent >= self.seconds


class _TimedLLM:
    """Mede o tempo de cada chamada ao LLM; estourado o teto, nada mais é perguntado.

    `extract_results` é controlado por `analyze` (que pula a unidade e avisa); o
    cabeçalho e a escolha de exame pelo modelo apenas deixam de ser tentados.
    """

    def __init__(self, llm: LLMClient, budget: _Budget, telemetry: LLMTelemetry) -> None:
        self._llm = llm
        self._budget = budget
        self._telemetry = telemetry
        self.model = llm.model
        self.vision_model = llm.vision_model

    def transcribe_page(self, png: bytes) -> str:
        return self._llm.transcribe_page(png)

    def _spend(self, seconds: float) -> None:
        self._budget.spent += seconds

    def _timed(self, kind: str, page: int | None, call: Callable, *args):
        return _measured(
            self._telemetry, self._llm, kind, page, call, *args, on_elapsed=self._spend
        )

    def extract_header(self, text: str) -> dict:
        if self._budget.exceeded:
            return {}
        return self._timed("header", None, self._llm.extract_header, text)

    def extract_results(self, block_text: str, page: int) -> dict:
        return self._timed("results", page, self._llm.extract_results, block_text, page)

    def match_analyte(self, name: str, unit: str, candidates: list[tuple[str, str]]) -> str:
        if self._budget.exceeded:
            return ""
        return self._timed("match", None, self._llm.match_analyte, name, unit, candidates)


def _extract_rows(
    llm: LLMClient, text: str, page: int, calls: _Calls, *, retry_empty: bool = True
) -> list[dict]:
    """Linhas do LLM para um pedaço. Timeout é tentado de novo uma vez."""

    def ask() -> dict:
        calls.count += 1
        return llm.extract_results(text, page)

    def call() -> list[dict]:
        try:
            output = ask()
        except ExtractionError as exc:
            if exc.code != ErrorCode.INFERENCE_TIMEOUT:
                raise
            output = ask()
        rows = []
        for raw in output.get("results") or []:
            if isinstance(raw, dict):
                row = _coerce_row(raw)
                row["source_line"] = _text(raw.get("source_line"))
                rows.append(row)
        return rows

    rows = call()
    if not rows and retry_empty and "material" in text.lower():
        rows = call()  # uma segunda chance
    return rows


def _fill_references(block: blocks_mod.Block, chunk: LLMChunk, rows: list[dict]) -> list[dict]:
    """Linhas do LLM de um pedaço -> linhas do bloco.

    Descarta o eco do prefixo repetido (título/meta) nos pedaços seguintes ao primeiro e
    copia, por código, a referência impressa fora da linha do resultado.
    """
    located: list[tuple[dict, int | None]] = []
    for row in rows:
        source = resolve_source_line(block, row)
        index = next(
            (o for o, line in zip(chunk.origin, chunk.lines, strict=True) if line == source), None
        )
        if index is None and source in block.lines:
            index = block.lines.index(source)
        if index is not None and index in chunk.repeated and not chunk.first:
            continue
        located.append((row, index))
    others = [index for _, index in located if index is not None]
    filled = []
    for row, index in located:
        if not row["reference_text"] and index is not None:
            row = {**row, "reference_text": layouts.reference_for(block, index, others)}
        filled.append(row)
    return filled


def analyze(
    document: ExamDocument,
    pages: list[PageText],
    llm: LLMClient,
    on_progress: ProgressCallback | None = None,
    telemetry: LLMTelemetry | None = None,
) -> Analysis:
    telemetry = telemetry if telemetry is not None else LLMTelemetry()
    use_llm = settings.EXTRACTION_USE_LLM
    # Modelo desligado = orçamento zero: nenhuma unidade vai ao LLM e nada é tentado.
    budget = _Budget(settings.EXTRACTION_LLM_BUDGET_SECONDS if use_llm else 0)
    llm = _TimedLLM(llm, budget, telemetry)
    page_lines, header_lines = blocks_mod.strip_boilerplate(pages)
    preface, blocks = blocks_mod.split_blocks(page_lines)
    header_text = "\n".join([*header_lines, *preface])[:HEADER_MAX_CHARS]
    if not header_text:
        header_text = "\n".join(page_lines[0][:12]) if page_lines else ""
    # Texto inteiro das páginas, sem tirar o boilerplate: o rodapé que traz o nome do laboratório
    # (`LAB. ... CRM-PJ`) repete em toda página e é justamente o que `strip_boilerplate` remove.
    # Endereços web só valem nas faixas de topo/rodapé de cada página (papel timbrado), nunca no
    # corpo: uma nota com a bibliografia de um valor de referência cita o site de outra instituição.
    laboratory_hint = laboratory_from_text(
        "\n".join([header_text, *(line for page in pages for line in page.lines)]),
        "\n".join(
            [
                header_text,
                *(
                    line
                    for page in pages
                    for line in (
                        *page.lines[:LAB_DOMAIN_TOP_LINES],
                        *page.lines[-LAB_DOMAIN_BOTTOM_LINES:],
                    )
                ),
            ]
        ),
    )
    # Regex primeiro: o LLM só entra quando o cabeçalho não foi lido (< 3 campos achados).
    header_needs_llm = header_fields_found(parse_header({}, header_text)) < HEADER_REGEX_MIN_FIELDS
    header_by_llm = header_needs_llm and not budget.exceeded
    raw_header = llm.extract_header(header_text) if header_by_llm else {}
    header = parse_header(raw_header, header_text, laboratory_hint)
    if header.collected_at is None:
        # Sem "Data da coleta" no cabeçalho: vale a data de coleta impressa nos próprios exames.
        header.collected_at = next(
            (c for block in blocks if (c := parse_br_datetime(block.collected_at))), None
        )

    user = document.user
    profile = getattr(user, "exam_profile", None)
    patient = PatientContext(
        sex=(profile.sex or None) if profile else None,
        birth_date=(
            profile.birth_date if profile and profile.birth_date else header.patient_birth_date
        ),
    )
    resolver = AnalyteResolver(llm, user=document.user)
    vision_pages = {p.number for p in pages if p.method == "vision"}

    plans = [_plan(block) for block in blocks]
    total = sum(plan.units for plan in plans)

    def report(done: int) -> None:
        # Também no objeto em memória: `persist` salva o documento inteiro e não pode
        # sobrescrever o progresso gravado pelo callback com zeros.
        document.progress_done, document.progress_total = done, total
        if on_progress is not None:
            on_progress(done, total)

    def drafts_for(block: blocks_mod.Block, rows: list[dict]) -> list[dict]:
        return [
            d
            for row in rows
            if (
                d := build_draft(
                    row,
                    block,
                    resolver=resolver,
                    patient=patient,
                    fallback_collected=header.collected_at,
                    vision=block.page in vision_pages,
                )
            )
        ]

    report(0)
    analysis = Analysis(header=header)
    empty_blocks = 0
    skipped_blocks = 0
    skipped_units = 0
    rule_blocks = 0
    table_blocks = 0
    calls = _Calls()
    unreadable: dict[tuple[int, str], None] = {}  # (página, título) na ordem em que apareceram
    if not use_llm:
        # Sem modelo não há visão: página sem texto (escaneada ou em branco) não é lida. Se
        # NENHUMA página tem texto, o laudo é uma imagem e só o modelo o leria; senão, cada
        # página fica na lista de "não lidos" (dá para digitar ou dispensar).
        blank = [p.number for p in pages if p.method == "vision" and not p.lines]
        if pages and len(blank) == len(pages):
            raise ExtractionError(ErrorCode.LLM_DISABLED)
        for number in blank:
            unreadable.setdefault((number, ""))
    last_error: ExtractionError | None = None
    done = 0
    drafts_by_plan: dict[int, list[dict]] = {}

    # Regras primeiro (rápidas), LLM por último; a ordem original vale dentro de cada grupo
    # e é restaurada ao montar o resultado.
    order = sorted(range(len(plans)), key=lambda i: (plans[i].kind == "llm", i))
    for position in order:
        plan = plans[position]
        block = plan.block
        if plan.kind == "skip":
            done += 1
            report(done)
            continue

        block_drafts: list[dict] = []
        failed = False
        if plan.kind == "rule":
            rule_blocks += 1
            table_blocks += plan.table
            block_drafts = drafts_for(block, plan.rows)
            done += 1
            report(done)
        else:
            for chunk in plan.chunks:
                if budget.exceeded:
                    # Sem tempo de LLM: a parte é pulada (não tentada) e o laudo avisa.
                    skipped_units += 1
                    if not failed:
                        skipped_blocks += 1
                        failed = True
                    unreadable.setdefault((block.page, unreadable_title(block)))
                    done += 1
                    report(done)
                    continue
                try:
                    rows = _extract_rows(
                        llm, chunk.text, block.page, calls, retry_empty=len(plan.chunks) == 1
                    )
                except ExtractionError as exc:
                    if exc.code not in _BLOCK_SKIPPABLE:
                        raise
                    # Um bloco ilegível não derruba o laudo: segue e avisa (sem nomes nem valores).
                    last_error = exc
                    if not failed:
                        skipped_blocks += 1
                        failed = True
                    unreadable.setdefault((block.page, unreadable_title(block)))
                    logger.warning(
                        "Bloco da página %s não pôde ser lido (%s)", block.page, exc.code
                    )
                    done += 1
                    report(done)
                    continue
                block_drafts.extend(drafts_for(block, _fill_references(block, chunk, rows)))
                done += 1
                report(done)

        if block_drafts:
            drafts_by_plan[position] = list(block_drafts)
            row_previous = [d for d in block_drafts if d["raw"].get("previous")]
            primary = next(
                (
                    d
                    for d in block_drafts
                    if not d["_calculated"] and d["value_numeric"] is not None
                ),
                None,
            )
            for draft in row_previous:  # Softl@b: cada resultado traz os próprios anteriores
                stored = draft["raw"]["previous"]
                pairs = [(date.fromisoformat(day), value) for day, value in stored]
                drafts_by_plan[position].extend(
                    build_previous_drafts(block, draft, resolver, pairs)
                )
            if primary is not None and not row_previous:
                drafts_by_plan[position].extend(build_previous_drafts(block, primary, resolver))
        elif not failed:
            empty_blocks += 1

    for position in range(len(plans)):
        analysis.drafts.extend(drafts_by_plan.get(position, []))

    if last_error is not None and not analysis.drafts:
        # Nada foi lido porque os blocos falharam: o motivo real, não "sem resultados".
        raise last_error
    analysis.warnings.extend(
        warning(WarningCode.BLOCK_UNREADABLE, page=page, **({"title": title} if title else {}))
        for page, title in unreadable
    )
    if skipped_units and use_llm:  # desligado de propósito não é "tempo esgotado"
        logger.warning("Tempo do LLM esgotado: %s parte(s) puladas", skipped_units)
        analysis.warnings.append(
            warning(WarningCode.LLM_BUDGET_EXCEEDED, seconds=int(budget.seconds))
        )

    analysis.stats = {
        "blocks": len(blocks),
        "empty_blocks": empty_blocks,
        "skipped_blocks": skipped_blocks,
        "rule_blocks": rule_blocks,
        "table_blocks": table_blocks,
        "llm_calls": calls.count,
        "llm_enabled": use_llm,
        "llm_skipped_units": skipped_units,
        "llm_seconds": round(budget.spent, 1),
        "header_by_llm": header_by_llm,
        "results": sum(1 for d in analysis.drafts if d["origin"] == ResultOrigin.CURRENT),
        "previous": sum(1 for d in analysis.drafts if d["origin"] == ResultOrigin.LAB_PREVIOUS),
        "needs_review": sum(
            1 for d in analysis.drafts if d["review_status"] == ReviewStatus.NEEDS_REVIEW
        ),
        **telemetry.stats(),
    }

    full_name = f"{user.first_name} {user.last_name}".strip()
    if header.patient_name and not names_match(header.patient_name, full_name):
        analysis.warnings.append(warning(WarningCode.PATIENT_MISMATCH))
    return analysis


def _local_date(moment: datetime):
    """Dia no fuso do usuário. O banco devolve UTC; sem isto, 22:30 em São Paulo cairia
    no dia seguinte e não casaria com a data impressa no laudo."""
    return timezone.localtime(moment).date()


def laboratory_for(name: str) -> Laboratory | None:
    """Laboratório pelo nome normalizado (criado se não existe); `None` sem nome."""
    if not name:
        return None
    laboratory, _ = Laboratory.objects.get_or_create(
        normalized_name=normalize_name(name), defaults={"name": name}
    )
    return laboratory


def find_accession_clash(
    user, laboratory: Laboratory | None, accession_number: str, exclude: ExamDocument | None = None
) -> ExamDocument | None:
    """Outro laudo do usuário com o mesmo número de pedido (no mesmo laboratório)."""
    if not accession_number:
        return None
    # `laboratory=None` vira `IS NULL`: a constraint única do Postgres nunca
    # colide com NULL, então a checagem precisa ser explícita.
    lab_filter = {"laboratory": laboratory} if laboratory else {"laboratory__isnull": True}
    clashes = ExamDocument.objects.filter(
        user=user, accession_number=accession_number, **lab_filter
    )
    if exclude is not None:
        clashes = clashes.exclude(pk=exclude.pk)
    return clashes.first()


def history_key(analyte: Analyte | None, collected_at: datetime | None):
    """(exame, dia local): chave que identifica um valor no histórico do usuário."""
    if analyte is None or collected_at is None:
        return None
    return (analyte.pk, _local_date(collected_at))


def known_history_keys(user, exclude: ExamDocument | None = None) -> set:
    """Chaves (exame, dia) que o usuário já tem em OUTROS laudos."""
    results = ExamResult.objects.filter(
        user=user, collected_at__isnull=False, analyte__isnull=False
    )
    if exclude is not None:
        results = results.exclude(document=exclude)
    return {
        (analyte_id, _local_date(collected))
        for analyte_id, collected in results.values_list("analyte_id", "collected_at")
    }


def drop_superseded_previous(user, current_keys: set, exclude: ExamDocument | None = None) -> None:
    """Resultado atual substitui o "anterior" que outro laudo havia semeado."""
    candidates = ExamResult.objects.filter(
        user=user, origin=ResultOrigin.LAB_PREVIOUS, analyte__isnull=False
    )
    if exclude is not None:
        candidates = candidates.exclude(document=exclude)
    stale = [
        r.pk
        for r in candidates
        if r.collected_at and (r.analyte_id, _local_date(r.collected_at)) in current_keys
    ]
    ExamResult.objects.filter(pk__in=stale).delete()


def review_status_of_document(rows, warnings: list[dict]) -> str:
    """`needs_review` se sobra resultado atual a revisar ou aviso do laudo; senão `completed`."""
    pending = any(
        r.review_status == ReviewStatus.NEEDS_REVIEW for r in rows if r.origin in CURRENT_ORIGINS
    )
    return DocumentStatus.NEEDS_REVIEW if pending or warnings else DocumentStatus.COMPLETED


def persist(document: ExamDocument, analysis: Analysis) -> Outcome:
    header = analysis.header
    current = [d for d in analysis.drafts if d["origin"] == ResultOrigin.CURRENT]
    if not current:
        raise ExtractionError(ErrorCode.NO_RESULTS_FOUND)

    with transaction.atomic():
        laboratory = laboratory_for(header.laboratory_name)
        clash = find_accession_clash(
            document.user, laboratory, header.accession_number, exclude=document
        )
        if clash:
            when = clash.collected_on.isoformat() if clash.collected_on else ""
            raise ExtractionError(ErrorCode.DUPLICATE_DOCUMENT, {"date": when})

        document.results.all().delete()
        collected_dates = [_local_date(d["collected_at"]) for d in current if d["collected_at"]]
        document.laboratory = laboratory
        document.accession_number = header.accession_number
        document.ordering_physician = header.ordering_physician[:255]
        document.patient_name_on_doc = header.patient_name[:255]
        document.patient_birth_date = header.patient_birth_date
        document.issued_at = header.issued_at
        document.collected_on = (
            min(collected_dates)
            if collected_dates
            else (_local_date(header.collected_at) if header.collected_at else None)
        )
        document.warnings = analysis.warnings

        user_seen = known_history_keys(document.user, exclude=document)
        current_keys = {
            key
            for d in current
            if (key := history_key(d["analyte"], d["collected_at"])) is not None
        }
        drop_superseded_previous(document.user, current_keys, exclude=document)

        rows = []
        seen_previous: set = set()
        for draft in analysis.drafts:
            if draft["origin"] == ResultOrigin.LAB_PREVIOUS:
                key = (draft["analyte"].pk, _local_date(draft["collected_at"]))
                if key in user_seen or key in current_keys or key in seen_previous:
                    continue
                seen_previous.add(key)
            fields = {k: v for k, v in draft.items() if not k.startswith("_")}
            rows.append(
                ExamResult(user=document.user, document=document, position=len(rows), **fields)
            )
        ExamResult.objects.bulk_create(rows)

        document.status = review_status_of_document(rows, analysis.warnings)
        document.error_code = ""
        document.error_params = {}
        try:
            document.save()
        except IntegrityError as exc:
            # Outro worker gravou o mesmo laudo (hash/pedido) entre a checagem e aqui.
            raise ExtractionError(ErrorCode.DUPLICATE_DOCUMENT, {"date": ""}) from exc
    return Outcome(status=document.status, stats=analysis.stats)


def process_document(
    document: ExamDocument,
    llm: LLMClient,
    pdf_path: Path | None = None,
    on_progress: ProgressCallback | None = None,
    telemetry: LLMTelemetry | None = None,
) -> Outcome:
    """Executa o pipeline. Sem `pdf_path`, reprocessa a partir do texto salvo.

    `telemetry` é de quem chama: se a extração falhar no meio, as chamadas já medidas
    continuam lá (o laudo lento é justamente o que precisa ser visto).
    """
    telemetry = telemetry if telemetry is not None else LLMTelemetry()
    if pdf_path is not None:
        pages = ingest(document, llm, pdf_path, telemetry)
    else:
        pages = pages_from_document(document)
        if not pages:
            raise ExtractionError(ErrorCode.DOCUMENT_NOT_REPROCESSABLE)
    analysis = analyze(document, pages, llm, on_progress, telemetry)
    return persist(document, analysis)
