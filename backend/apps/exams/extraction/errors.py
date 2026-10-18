from ..codes import ErrorCode


class ExtractionError(Exception):
    """Falha do pipeline com código + params (o frontend traduz)."""

    def __init__(self, code: ErrorCode, params: dict | None = None):
        super().__init__(str(code))
        self.code = code
        self.params = params or {}
