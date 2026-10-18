"""Páginas sintéticas no formato "ficha" (um exame por trecho, sem `Material:`).

Dados inventados (nunca laudos reais): só imitam a forma impressa. As linhas já estão no
formato que `linearize` produz (colunas separadas por ` | `).
"""

import random

from apps.exams.extraction.linearize import PageText

PATIENT = "JOAO TESTE DA SILVA"
ACCESSION = "9990000001"
_RANDOM = random.Random(20250210)


def signature() -> str:
    """Assinatura digital única por exame (hex aleatório): no laudo real ela nunca se repete."""
    return "Assinatura digital avançada: " + "".join(_RANDOM.choices("0123456789ABCDEF", k=64))


SIGNATURE = signature()
FOOTER = [
    "CRBM­2: 0000/0000­J ­ RESPONSÁVEL TÉCNICO: FULANA DE TAL CRBM: 000PE",
    "A interpretação do resultado deste(s) exame(s) e a conclusão diagnóstica são atos "
    "médicos e dependem da análise conjunta dos dados clínicos e demais exames do(a) paciente.",
]


def received(day: str = "10/02/2025") -> list[str]:
    return [
        f"RECEBIDO/COLETADO EM: {day} - LIBERADO EM: {day} 18:03 - CNES: 0000000",
        "Exame liberado de acordo com regras definidas e validadas pela assessoria médica.",
        signature(),
    ]


PHYSICIAN = "DRA. FULANA DE TAL CRM 000PE"


def header_lines(physician: str = PHYSICIAN) -> list[str]:
    doctor = f"{physician} | " if physician else ""
    return [
        f"Cliente: | {PATIENT}",
        f"Data de Nascimento: 05/03/1977 | Ficha: | {ACCESSION}",
        f"Médico: | {doctor}Data da Ficha: | 10/02/2025",
    ]


def page(number: int, total: int, body: list[str], physician: str = PHYSICIAN) -> PageText:
    lines = [
        *header_lines(physician),
        *body,
        *FOOTER,
        "Praça Exemplo, 1 ­ Centro ­ Cidade­PE | Impresso em: 18/10/2026 08:12 ­ Página: "
        f"{number}/{total}",
    ]
    return PageText(number, lines, "text")


def single(
    title: str, value_line: str, *reference: str, method: str | None = "Ensaio enzimático"
) -> list[str]:
    """Um exame de resultado único, como impresso."""
    lines = [title]
    if method:
        lines.append(f"Método: {method}")
    lines += ["RESULTADO | VALORES DE REFERÊNCIA", value_line, *reference]
    return lines + received()


CHOLESTEROL = single(
    "COLESTEROL TOTAL, soro",
    "171 mg/dL | Maior ou igual a 20 anos:",
    "Com ou sem jejum de 12 horas:",
    "Desejável: menor que 190 mg/dL.",
    "NOTA: Referências:",
    "Atualização da Diretriz Brasileira de Dislipidemias e Prevenção da",
    "Aterosclerose - 2017 da Sociedade Brasileira de Cardiologia.",
)
UREA = single("UREIA, soro", "27 mg/dL | 10 a 50 mg/dL", method="Cinético, UV")
HDL = single(
    "HDL-COLESTEROL, soro",
    "58 mg/dL | Maior ou igual a 20 anos:",
    "Com ou sem jejum de 12 horas:",
    "Desejável: maior que 40 mg/dL.",
    "NOTA: Referências:",
    "Atualização da Diretriz Brasileira de Dislipidemias.",
)

HEMOGRAM = [
    "HEMOGRAMA, sangue total",
    "VALORES DE REFERÊNCIA",
    "RESULTADO | Masc: Maior ou igual a 18 anos",
    "------------------------------------------------------------------------",
    "ERITRÓCITOS | : | 4,95 milhões/mm3 | 4,32 a 5,67",
    "HEMOGLOBINA | : 14,8 g/dL | 13,3 a 16,5",
    "HEMATÓCRITO | : 43,1 % | 39,2 a 49,0",
    "CONCENTRAÇÃO DE HEMOGLOBINA",
    "CORPUSCULAR MÉDIA: 33,9 g/dL | 32,4 a 36,0",
    "COEFICIENTE DE VARIAÇÃO DO",
    "VOLUME ERITROCITÁRIO (RDW): 13,4 % | 11,8 a 14,1",
    "------------------------------------------------------ ----------------",
    "CARACTERES MORFOLÓGICOS:",
    "normais",
    "SÉRIE BRANCA | ========================================================",
    "VALORES DE REFERÊNCIA",
    "RESULTADO | Masc: Maior ou igual a 18 anos",
    "% | /mm3 | /mm3",
    "-------------------------------------------- ---------------------",
    "LEUCÓCITOS | 6.210 | 3.650 a 8.120",
    "Neutrófilos | : 58,3 | 3.620 | 1.590 a 4.770",
    "Eosinófilos | : | 3,1 | 190 | 34 a 420",
    "Linfócitos | : 30,4 | 1.890 | 1.120 a 2.950",
    "-------------------------------------------- ---------------------",
    "CARACTERES MORFOLÓGICOS:",
    "não foram observados caracteres tóxico­degenerativos nos",
    "neutrófilos; não foram observadas atipias linfocitárias",
    "PLAQUETAS | ===========================================================",
    "VALORES DE REFERÊNCIA",
    "RESULTADO | Masc: Maior ou igual a 18 anos",
    "TOTAL DE PLAQUETAS: 231.000/mm3 | 151.000 a 304.000/mm3",
    "VOLUME PLAQUETÁRIO MÉDIO: 10,7 fL | 9,2 a 12,6 fL",
    "NOTA(1): Os valores de referência para adultos são baseados em um",
    "estudo com 95 mil amostras de brasileiros saudáveis.",
    *received(),
]

HISTORY_PAGE_BODY = [
    "LAUDO EVOLUTIVO",
    "RESULTADOS",
    "ANALITOS | VALORES DE REFERÊNCIA",
    "9990000001 | 9990000002 | 9990000003",
    "10/02/2025 | 19/08/2023 | 03/04/2022",
    "HDL-Colesterol | 58 | 55(*) | 52(*) | ---- | ---- | ---- maior que 40 mg/dL",
    "Colesterol total | 171 | 205(*)(1) | 198(*)(1) | ---- | ---- | ---- menor que 190 mg/dL",
    "Triglicérides | 92 | 84(*) | 101(*) | ---- | ---- | ----",
    "(*) Os intervalos de referência deste exame dependem da faixa etária do(a) paciente.",
]


def pages(*bodies: list[str], physician: str = PHYSICIAN) -> list[PageText]:
    total = len(bodies)
    return [page(number, total, body, physician) for number, body in enumerate(bodies, start=1)]
