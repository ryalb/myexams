"""Checagens que decidem se um resultado precisa de revisão humana."""

from decimal import Decimal

from ..codes import WarningCode
from .text import compact, normalize_name

# Avisos só informativos: o valor em si está bom, falta apenas a faixa.
INFORMATIONAL = frozenset(
    {
        WarningCode.REFERENCE_NOT_PARSED,
        WarningCode.REFERENCE_NOT_APPLICABLE,
        WarningCode.SEX_REQUIRED_FOR_REFERENCE,
        WarningCode.PAGE_READ_BY_VISION,
    }
)


def warning(code: WarningCode, **params) -> dict:
    return {"code": str(code), "params": params}


def needs_review(warnings: list[dict]) -> bool:
    return any(w["code"] not in {str(c) for c in INFORMATIONAL} for w in warnings)


def value_is_grounded(value_text: str, block_text: str) -> bool:
    """O valor transcrito precisa aparecer no texto da página/bloco.

    Principal defesa contra número alucinado: o LLM só transcreve, então o que
    ele devolve tem de existir literalmente no texto de origem.
    """
    needle = compact(value_text)
    return not needle or needle in compact(block_text)


def is_implausible(value: Decimal | None, minimum: Decimal | None, maximum: Decimal | None) -> bool:
    if value is None:
        return False
    if minimum is not None and value < minimum:
        return True
    return maximum is not None and value > maximum


def names_match(document_name: str, user_full_name: str) -> bool:
    """Todo token do nome cadastrado precisa aparecer no nome do laudo.

    Compara sem acento, caixa nem pontuação: o laudo costuma imprimir "NOME SOBRENOME."
    (com ponto final) e isso não pode virar um falso aviso de paciente diferente.
    """
    user_tokens = set(normalize_name(user_full_name).split())
    if not user_tokens or not document_name.strip():
        return True
    return user_tokens <= set(normalize_name(document_name).split())
