"""Resolução do nome impresso para o exame canônico.

Ordem: apelido exato (também com tokens ordenados e sem parênteses), similaridade
por trigramas (pg_trgm), e por último o LLM restrito ao catálogo. Sem match, o
resultado é salvo sem exame e vai para revisão: nunca se descarta um valor.

Apelidos globais (`user` nulo) vêm só do catálogo. Os do usuário sobrepõem os globais
apenas para ele. O que o LLM escolhe (derivado de texto de PDF não confiável) vale só
na memória deste resolver e nunca é gravado no banco.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import F, Q

from ..models import AliasSource, Analyte, AnalyteAlias, UnitConversion
from .text import normalize_name, sorted_tokens_key
from .units import is_count_unit

TRIGRAM_THRESHOLD = 0.6
LLM_CANDIDATES = 400


@dataclass(frozen=True)
class Resolution:
    analyte: Analyte | None
    source: str  # alias | fuzzy | llm | none


def _name_keys(name: str) -> list[str]:
    keys = [normalize_name(name)]
    without_parens = normalize_name(re.sub(r"\(.*?\)", " ", name))
    keys.append(without_parens)
    keys.extend(normalize_name(inner) for inner in re.findall(r"\((.*?)\)", name))
    keys = [k for k in keys if k]
    keys.extend(sorted_tokens_key(k) for k in list(keys))
    return list(dict.fromkeys(keys))


class AnalyteResolver:
    def __init__(self, llm=None, user=None) -> None:
        self.llm = llm
        self.user = user
        self._analytes = {a.pk: a for a in Analyte.objects.all()}
        self._by_slug = {a.slug: a for a in self._analytes.values()}
        self._aliases = dict(
            AnalyteAlias.objects.filter(user__isnull=True).values_list(
                "alias_normalized", "analyte_id"
            )
        )
        if user is not None:
            # Apelidos do próprio usuário vencem os globais.
            self._aliases.update(
                AnalyteAlias.objects.filter(user=user).values_list("alias_normalized", "analyte_id")
            )
        self._llm_matches: dict[str, Analyte] = {}
        self._factors: dict[int, dict[tuple[str, str], Decimal]] = {}
        for conv in UnitConversion.objects.all():
            self._factors.setdefault(conv.analyte_id, {})[(conv.from_unit, conv.to_unit)] = (
                conv.factor
            )
        self._misses: set[str] = set()

    def factors(self, analyte: Analyte | None) -> dict[tuple[str, str], Decimal]:
        return self._factors.get(analyte.pk, {}) if analyte else {}

    def resolve(self, name: str, unit: str = "") -> Resolution:
        analyte, source = self._lookup(name, unit)
        return Resolution(self._swap_pct_abs(analyte, unit), source)

    def _lookup(self, name: str, unit: str) -> tuple[Analyte | None, str]:
        keys = _name_keys(name)
        for key in keys:
            if key in self._aliases:
                return self._analytes[self._aliases[key]], "alias"

        primary = keys[0] if keys else ""
        if not primary or primary in self._misses:
            return None, "none"

        if primary in self._llm_matches:
            return self._llm_matches[primary], "llm"

        scope = Q(user__isnull=True)
        if self.user is not None:
            scope |= Q(user=self.user)
        fuzzy = (
            AnalyteAlias.objects.filter(scope)
            .annotate(sim=TrigramSimilarity("alias_normalized", primary))
            .filter(sim__gte=TRIGRAM_THRESHOLD)
            # Em empate de similaridade, o apelido do próprio usuário vence o global.
            .order_by("-sim", F("user").asc(nulls_last=True))
            .first()
        )
        if fuzzy is not None:
            return self._analytes[fuzzy.analyte_id], "fuzzy"

        if self.llm is not None:
            candidates = [
                (a.slug, a.name_pt) for a in list(self._analytes.values())[:LLM_CANDIDATES]
            ]
            slug = self.llm.match_analyte(name, unit, candidates)
            analyte = self._by_slug.get(slug)
            if analyte is not None:
                self._llm_matches[primary] = analyte
                return analyte, "llm"

        self._misses.add(primary)
        return None, "none"

    def _swap_pct_abs(self, analyte: Analyte | None, unit: str) -> Analyte | None:
        """Percentual e absoluto são exames distintos; a unidade decide qual."""
        if analyte is None:
            return None
        if analyte.slug.endswith("_pct") and is_count_unit(unit):
            return self._by_slug.get(analyte.slug[:-4] + "_abs", analyte)
        if analyte.slug.endswith("_abs") and unit == "%":
            return self._by_slug.get(analyte.slug[:-4] + "_pct", analyte)
        return analyte


def learn_alias(user, name_raw: str, analyte: Analyte) -> None:
    """Correção do usuário vira apelido permanente, só para ele (laço de feedback)."""
    if user is None:
        raise ValueError("learn_alias exige um usuário: apelidos globais vêm só do catálogo")
    key = normalize_name(name_raw)
    if key:
        AnalyteAlias.objects.update_or_create(
            user=user,
            alias_normalized=key,
            defaults={"analyte": analyte, "source": AliasSource.USER},
        )
