"""Códigos estáveis trocados com o frontend.

O backend não produz texto de interface customizado: devolve código + params e o
frontend (@nuxtjs/i18n, locales/pt-BR.json) traduz. `tests/test_i18n_parity.py`
garante que todo código aqui tem tradução em `errors.*` / `warnings.*`.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    # Upload / validação do arquivo
    FILE_MISSING = "file_missing"
    FILE_NOT_PDF = "file_not_pdf"
    FILE_TOO_LARGE = "file_too_large"
    FILE_TOO_MANY_PAGES = "file_too_many_pages"
    PDF_ENCRYPTED = "pdf_encrypted"
    PDF_UNREADABLE = "pdf_unreadable"
    DUPLICATE_DOCUMENT = "duplicate_document"
    # Pipeline
    INFERENCE_UNAVAILABLE = "inference_unavailable"
    INFERENCE_TIMEOUT = "inference_timeout"
    INFERENCE_NOT_LOCAL = "inference_not_local"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    NO_RESULTS_FOUND = "no_results_found"
    EXTRACTION_FAILED = "extraction_failed"
    # API
    REGISTRATION_DISABLED = "registration_disabled"
    EMAIL_ALREADY_REGISTERED = "email_already_registered"
    DOCUMENT_NOT_REPROCESSABLE = "document_not_reprocessable"
    DOCUMENT_NOT_READY = "document_not_ready"
    ANALYTE_NOT_FOUND = "analyte_not_found"
    # Edição do laboratório do laudo
    LABORATORY_NAME_TOO_LONG = "laboratory_name_too_long"  # params: max
    DOCUMENT_BUSY = "document_busy"  # a extração ainda grava no laudo
    LABORATORY_ACCESSION_CONFLICT = "laboratory_accession_conflict"  # params: document_id


class WarningCode(StrEnum):
    """Avisos de revisão, por resultado (ou do laudo inteiro)."""

    VALUE_NOT_IN_PAGE = "value_not_in_page"
    VALUE_MISSING = "value_missing"
    AMBIGUOUS_NUMBER_FORMAT = "ambiguous_number_format"
    IMPLAUSIBLE_VALUE = "implausible_value"
    FLAG_MISMATCH = "flag_mismatch"
    UNMAPPED_ANALYTE = "unmapped_analyte"
    UNIT_MISSING = "unit_missing"
    UNIT_NOT_CONVERTIBLE = "unit_not_convertible"
    REFERENCE_NOT_PARSED = "reference_not_parsed"
    REFERENCE_NOT_APPLICABLE = "reference_not_applicable"
    PATIENT_MISMATCH = "patient_mismatch"
    SEX_REQUIRED_FOR_REFERENCE = "sex_required_for_reference"
    PAGE_READ_BY_VISION = "page_read_by_vision"
    # Do laudo inteiro: um bloco não foi lido (params: page). O resto do laudo foi salvo.
    BLOCK_UNREADABLE = "block_unreadable"
    # Do laudo inteiro: o tempo do LLM acabou e o resto foi pulado (params: seconds).
    LLM_BUDGET_EXCEEDED = "llm_budget_exceeded"
