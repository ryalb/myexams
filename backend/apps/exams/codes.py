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
    LLM_DISABLED = (
        "llm_disabled"  # EXTRACTION_USE_LLM=false e a página exigiria o modelo (escaneada)
    )
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
    # Resultado digitado pelo usuário
    RESULT_NAME_REQUIRED = "result_name_required"  # erro do campo name_raw
    RESULT_VALUE_REQUIRED = "result_value_required"  # erro do campo value_raw
    RESULT_VALUE_INVALID = "result_value_invalid"  # erro do campo value_raw
    RESULT_REFERENCE_INVALID = "result_reference_invalid"  # erro do campo reference_raw
    RESULT_DUPLICATE = "result_duplicate"  # 409; params: result_id
    RESULT_NOT_MANUAL = "result_not_manual"  # 403: só resultado manual pode ser apagado
    WARNING_NOT_DISMISSIBLE = "warning_not_dismissible"  # 400: só block_unreadable se dispensa
    # Contas e administração de usuários
    USER_EMAIL_TAKEN = "user_email_taken"  # erro do campo email
    PASSWORD_TOO_SHORT = "password_too_short"  # erros do campo de senha (validadores do Django)
    PASSWORD_TOO_COMMON = "password_too_common"
    PASSWORD_ENTIRELY_NUMERIC = "password_entirely_numeric"
    PASSWORD_TOO_SIMILAR = "password_too_similar"
    PASSWORD_INVALID = "password_invalid"  # validador desconhecido
    PASSWORD_UNCHANGED = "password_unchanged"  # erro do campo new_password: igual à atual
    CURRENT_PASSWORD_INCORRECT = "current_password_incorrect"  # 400
    ADMIN_SELF_ACTION = "admin_self_action"  # 409: administrador agindo sobre a própria conta
    LAST_ADMIN = "last_admin"  # 409: o último administrador ativo não pode sair do papel


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
    # Do laudo inteiro: um bloco não foi lido (params: page, title = nome do exame como impresso;
    # `title` falta nos laudos antigos). O resto do laudo foi salvo.
    BLOCK_UNREADABLE = "block_unreadable"
    # Do laudo inteiro: o tempo do LLM acabou e o resto foi pulado (params: seconds).
    LLM_BUDGET_EXCEEDED = "llm_budget_exceeded"
