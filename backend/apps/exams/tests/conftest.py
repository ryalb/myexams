"""Fixtures compartilhadas. Sem mocking de biblioteca: o LLM é um fake em memória
e os PDFs são gerados com reportlab (dados sintéticos, nunca laudos reais)."""

from datetime import date
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from apps.exams.models import ExamDocument, Profile
from apps.exams.storage import save_upload


@pytest.fixture(autouse=True)
def _isolated_infra(settings, tmp_path):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.EXAMS_UPLOAD_DIR = tmp_path / "uploads"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    # Hermético: o `.env` de quem roda os testes pode ter EXTRACTION_USE_LLM=false (modo só
    # regras); os testes com LLM falso precisam do padrão ligado. Quem testa o desligado
    # (test_llm_toggle.py) muda explicitamente.
    settings.EXTRACTION_USE_LLM = True


def make_user(email="joao@example.com", first="Joao", last="Teste da Silva", sex="male"):
    user = get_user_model().objects.create_user(
        username=email, email=email, password="senha-forte-123", first_name=first, last_name=last
    )
    Profile.objects.create(user=user, sex=sex, birth_date=date(1977, 3, 5))
    return user


@pytest.fixture
def user(db):
    return make_user()


@pytest.fixture
def other_user(db):
    return make_user("outra@example.com", "Outra", "Pessoa", "female")


SITE_LINE = [(40, "www.exemplo.com.br")]
HEADER = [
    [(40, "LABORATÓRIO EXEMPLO"), (300, "Medicina Diagnóstica")],
    SITE_LINE,
    [(40, "Cliente: JOAO TESTE DA SILVA"), (330, "O.S: 021-11111-222")],
    [(40, "Data Nasc.: 05/03/1977"), (330, "Atendimento: 25/08/2026 - 06:44:05")],
    [(40, "CPF: 123.456.789-09"), (330, "Solicitante: DR EXEMPLO")],
    [(40, "RG:"), (110, "99999999999"), (330, "Convênio: TESTE")],
]


HEADER_WITHOUT_SITE = [line for line in HEADER if line is not SITE_LINE]


def header_with_accession(accession: str) -> list[list[tuple[int, str]]]:
    """Mesmo cabeçalho, com outro número de pedido (o cabeçalho agora é lido do texto do PDF)."""
    return [
        [(x, text.replace("021-11111-222", accession)) for x, text in cells] for cells in HEADER
    ]


# Cabeçalho sem os campos que o regex lê (cliente, O.S, nascimento, solicitante): força o
# pipeline a perguntar ao LLM. Mantém CPF/RG para provar que não chegam ao modelo.
SPARSE_HEADER = [
    [(40, "LABORATÓRIO EXEMPLO"), (300, "Medicina Diagnóstica")],
    [(40, "CPF: 123.456.789-09"), (330, "RG: 99999999999")],
]


def _page_lines(
    page_number: int,
    total: int,
    body: list[list[tuple[int, str]]],
    identity: list[list[tuple[int, str]]] | None = None,
):
    header = [
        *(HEADER if identity is None else identity),
        [(40, "Emissão: 31/08/2026 - 13:57:40"), (330, f"Página: {page_number}/{total}")],
    ]
    footer = [
        [(40, "ASSINATURA DIGITAL")],
        [(40, "A interpretação dos resultados é de responsabilidade médica.")],
    ]
    return [*header, *body, *footer]


def build_pdf(
    path: Path,
    pages_body: list[list[list[tuple[int, str]]]],
    identity: list[list[tuple[int, str]]] | None = None,
) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    total = len(pages_body)
    for number, body in enumerate(pages_body, start=1):
        y = 800
        for cells in _page_lines(number, total, body, identity) if body is not None else []:
            for x, text in cells:
                pdf.drawString(x, y, text)
            y -= 16
        pdf.showPage()
    pdf.save()
    return path


def row(*cells):
    """row('Hemácias', '4,85', 'milhões/uL', '4,50 a 6,50') -> células espaçadas."""
    xs = (40, 190, 270, 360, 470)
    return [(xs[i], text) for i, text in enumerate(cells) if text]


PAGE_1 = [
    [(40, "HEMOGRAMA")],
    [(40, "Material: sangue")],
    [(40, "Método: Automatizado")],
    [(40, "Data da coleta: 25/08/2026 - 07:01:57")],
    row("ERITROGRAMA", "", "", "Valores de referência"),
    row("Hemácias", "4,85", "milhões/uL", "4,50 a 6,50 milhões/uL"),
    row("Hemoglobina", "", "g/dL", "13,50 a 18,00 g/dL"),
    row("LEUCOGRAMA"),
    row("Leucócitos", "4.000", "/uL", "4.000 a 10.000 /uL"),
    row("Neutrófilos", "60,0 %", "2.400 /uL", "40,0 a 75,0 | 2.000 a 7.000"),
    [(40, "Liberado por: Dr. Teste CRM-CE 1")],
    row("GLICOSE", "", "", "103 mg/dL"),
    [(40, "Material: soro")],
    [(40, "Método: Hexoquinase")],
    [(40, "Data da coleta: 25/08/2026 - 07:01:51")],
    [(40, "Valor de Referência: Normal: Glicemia de jejum entre 70 a 99 mg/dL")],
    [(40, "Glicemia de jejum alterada: Glicemia de jejum entre 100 a 125 mg/dL")],
    [(40, "Anteriores: 25/04/2022 - 92 | 17/02/2018 - 84")],
    [(40, "Liberado por: Dr. Teste CRM-CE 1")],
]

PAGE_2 = [
    [(40, "CREATININA")],
    [(40, "Material: soro")],
    [(40, "Data da coleta: 25/08/2026 - 07:01:51")],
    row("CREATININA:", "1,20 mg/dL"),
    [(40, "Valores de Referência: Homens: 0,70 a 1,30 mg/dL. Mulheres: 0,50 a 1,10 mg/dL.")],
    [(40, "Anteriores: 25/04/2022 - 0,97 | 18/07/2017 - 1,02")],
    row("eRFG:", "74 mL/min/1,73 m²"),
    [(40, "Valores de Referência: eRFG normal: > 90 mL/min/1,73 m²")],
    [(40, "Liberado por: Dr. Teste CRM-CE 1")],
    row("TSH ULTRA SENSIVEL", "", "", "1,942 uIU/mL"),
    [(40, "Material: soro")],
    [(40, "Data da coleta: 25/08/2026 - 07:01:51")],
    [(40, "Valor de Referência: 2 anos a menor que 12 anos: 0,640 a 6,270 uIU/mL")],
    [(40, "Igual ou maior que 18 anos : 0,550 a 4,780 uIU/mL")],
    [(40, "Anteriores: 17/02/2018 - 1,308 | 11/11/2016 - 1.251")],
    [(40, "Liberado por: Dr. Teste CRM-CE 1")],
]

PAGE_3 = [
    row("MAGNÉSIO", "", "", "2,1 mg/dL"),
    [(40, "Material: soro")],
    [(40, "Data da coleta: 25/08/2026 - 07:01:56")],
    [(40, "Valor de Referência: DE 1,6 A 2,6 mg/dL")],
    [(40, "Exame realizado em laboratório de apoio HERMES PARDINI MEDICINA DIAGNOSTICA.")],
    [(40, "Liberado por: Dr. Teste CRM-CE 1")],
    row("TESTOSTERONA LIVRE CALCULADA", "", "", "11,52 ng/dL"),
    [(40, "Material: soro")],
    [(40, "Data da coleta: 25/08/2026 - 07:01:54")],
    [(40, "Valor de Referência: HOMEM DE 21 A 49 ANOS : DE 3,03 A 14,80 ng/dL")],
    [(40, "HOMEM DE 50 A 89 ANOS : DE 1,81 A 10,20 ng/dL")],
    [(40, "MULHER DE 21 A 60 ANOS (PRÉ-MENOPAUSA): DE 0,08 A 1,11 ng/dL")],
    [(40, "Liberado por: Dr. Teste CRM-CE 1")],
]


def sample_row(name, value, unit="", reference="", **extra):
    """Linha no formato do schema enxuto. `extra` simula chaves a mais que o modelo
    possa devolver (devem ser ignoradas pelo pipeline)."""
    base = {
        "name": name,
        "value_text": value,
        "unit": unit,
        "reference_text": reference,
        "printed_flag": "",
        "is_calculated": False,
    }
    base.update(extra)
    return base


def sample_llm_results(block_text: str) -> dict:
    """O que um bom LLM devolveria para o único bloco do PDF sintético que as regras
    (`layouts`) não leem: a tabela do hemograma. Os demais blocos são exames de um
    resultado só e nunca chegam ao LLM."""
    if block_text.upper().startswith("HEMOGRAMA"):
        return {
            "results": [
                sample_row("Hemácias", "4,85", "milhões/uL", "4,50 a 6,50 milhões/uL"),
                sample_row("Hemoglobina", "", "g/dL", "13,50 a 18,00 g/dL"),
                sample_row("Leucócitos", "4.000", "/uL", "4.000 a 10.000 /uL"),
                sample_row("Neutrófilos", "60,0", "%", "40,0 a 75,0"),
                sample_row("Neutrófilos (absoluto)", "2.400", "/uL", "2.000 a 7.000"),
            ],
        }
    return {"results": []}


def qualitative_block(title: str, value: str) -> list[list[tuple[int, str]]]:
    """Exame de resultado qualitativo (`TÍTULO | Negativo`): as regras o deixam para o LLM."""
    return [
        row(title, "", "", value),
        [(40, "Material: urina")],
        [(40, "Data da coleta: 25/08/2026 - 07:01:51")],
        [(40, "Liberado por: Dr. Teste CRM-CE 1")],
    ]


def qualitative_llm_results(block_text: str) -> dict:
    """O que um bom LLM devolveria para os blocos de `qualitative_block`."""
    title, _, value = block_text.splitlines()[0].partition(" | ")
    if not value:
        return {"results": []}
    return {"results": [sample_row(title, value, "", value)]}


SAMPLE_HEADER = {
    "laboratory_name": "Laboratório Exemplo",
    "accession_number": "021-11111-222",
    "patient_name": "JOAO TESTE DA SILVA",
    "patient_birth_date": "05/03/1977",
    "ordering_physician": "DR EXEMPLO",
    "issued_at": "31/08/2026 - 13:57:40",
    "collected_at": "25/08/2026 - 07:01:51",
}


@pytest.fixture
def sample_pdf(tmp_path) -> Path:
    return build_pdf(tmp_path / "laudo.pdf", [PAGE_1, PAGE_2, PAGE_3])


def create_document(user, pdf_path: Path) -> ExamDocument:
    stored = save_upload([pdf_path.read_bytes()])
    return ExamDocument.objects.create(
        user=user,
        sha256=stored.sha256,
        original_filename="laudo.pdf",
        upload_path=str(stored.path),
    )
