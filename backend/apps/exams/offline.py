"""Configuração para rodar a extração fora do app web: sem Redis e, por padrão, sem modelo."""

from django.test.utils import override_settings


def offline_settings(*, use_llm: bool = False):
    """Eventos em memória (sem Redis) e, por padrão, sem modelo (`EXTRACTION_USE_LLM=False`).

    Compartilhado por `export_reports`, `backfill_warnings` e `import_reports`. Com
    `use_llm=True` o modelo continua valendo o que o ambiente diz (`EXTRACTION_USE_LLM`).
    É um `override_settings`: nada vaza para fora do bloco `with`.
    """
    overrides = {"CHANNEL_LAYERS": {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}}
    if not use_llm:
        overrides["EXTRACTION_USE_LLM"] = False
    return override_settings(**overrides)
