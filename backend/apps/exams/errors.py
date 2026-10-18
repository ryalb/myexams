"""Erros de API com código + params, sem texto customizado.

Resposta: `{"code": "duplicate_document", "params": {"date": "2026-08-25"}}`.
O frontend resolve `errors.<code>` no pt-BR.json. Mensagens nativas do DRF
(campo obrigatório etc.) continuam saindo em pt-BR via LANGUAGE_CODE.
"""

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .codes import ErrorCode


class CodedAPIError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: ErrorCode, params: dict | None = None, status_code: int | None = None):
        super().__init__(detail=str(code), code=str(code))
        self.error_code = str(code)
        self.params = params or {}
        if status_code is not None:
            self.status_code = status_code


def exception_handler(exc, context):
    if isinstance(exc, CodedAPIError):
        return Response({"code": exc.error_code, "params": exc.params}, status=exc.status_code)
    return drf_exception_handler(exc, context)
