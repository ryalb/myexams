"""Detecção do nome do laboratório (determinística, sem LLM). Textos inventados."""

import pytest

from apps.exams import tasks
from apps.exams.extraction import blocks as blocks_mod
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.extraction.pipeline import laboratory_from_footer, laboratory_from_text
from apps.exams.models import ExamDocument

from . import ficha_fixtures as ficha
from . import softlab_fixtures as soft


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("www.exemplo.com.br", "Exemplo"),
        ("contato@exemplo-lab.org.br", "Exemplo Lab"),
        ("contato@exemplo.med.br", "Exemplo"),
        ("www.exemplo.com", "Exemplo"),
        ("www.exemplo.io", "Exemplo"),
        ("contato@gmail.com", ""),
        ("www.exemplo", "Exemplo"),
    ],
)
def test_domain_stem_is_the_name_without_tld_and_second_level(text, expected):
    assert laboratory_from_text(text) == expected


@pytest.mark.parametrize("text", ["www.exemplo.ca", "contato@exemplo.ca", "www.exemplo.pt"])
def test_unknown_country_tld_is_never_taken_as_the_laboratory_name(text):
    """Causa raiz do "Ca" lixo: o TLD (`.ca`) fora da lista fixa virava o radical."""
    assert laboratory_from_text(text) == "Exemplo"


def test_second_level_label_before_an_unknown_tld_is_dropped_too():
    assert laboratory_from_text("www.exemplo.com.ca") == "Exemplo"


@pytest.mark.parametrize("text", ["www.ab.com.br", "contato@xy.org", "www.a.br", "www.ca"])
def test_stems_shorter_than_three_letters_are_ignored(text):
    assert laboratory_from_text(text) == ""


def test_short_stem_does_not_hide_a_later_valid_domain():
    text = "contato@ab.com.br\nwww.exemplo.com.br"
    assert laboratory_from_text(text) == "Exemplo"


def test_stem_without_letters_is_ignored():
    assert laboratory_from_text("www.123.com.br") == ""


def test_text_without_any_hint_gives_empty_name():
    assert laboratory_from_text("Cliente: JOAO TESTE\nEndereço: Rua Exemplo, 100, Ca") == ""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("LAB. JOSE EXEMPLO CRM-PJ 12", "Laboratório Jose Exemplo"),
        ("LAB. MARIA DA SILVA E SOUZA CRM-PJ 7", "Laboratório Maria da Silva e Souza"),
        ("LAB. JOÃO DOS SANTOS MAGALHÃES CRM-PJ 31", "Laboratório João dos Santos Magalhães"),
        ("LAB. ANA DE EXEMPLO CRM PJ 5", "Laboratório Ana de Exemplo"),
        ("  lab. jose exemplo crm-pj 12", "Laboratório Jose Exemplo"),
        ("LAB. D'AVILA EXEMPLO CRM-PJ 9", "Laboratório D'Avila Exemplo"),
    ],
)
def test_softlab_footer_gives_the_laboratory_name(line, expected):
    assert (
        laboratory_from_footer(f"Texto antes\n{line}\nDIREÇÃO: Dr. Fulano Exemplo CRM 000")
        == expected
    )


def test_footer_wins_over_domain_in_the_same_text():
    text = "www.outro.com.br\nLAB. JOSE EXEMPLO CRM-PJ 12"
    assert laboratory_from_text(text) == "Laboratório Jose Exemplo"


@pytest.mark.parametrize(
    "text",
    [
        "LAB. CRM-PJ 12",
        "LAB. 123 CRM-PJ 12",
        "LAB. AB CRM-PJ 12",
        "LABORATÓRIO JOSE EXEMPLO CRM-PJ 12",
        "O LAB. JOSE EXEMPLO CRM-PJ 12",
        "LAB. JOSE EXEMPLO",
    ],
)
def test_footer_shape_is_strict(text):
    assert laboratory_from_footer(text) == ""


# --- ponta a ponta no layout Softl@b ------------------------------------------------------


@pytest.fixture
def llm(monkeypatch):
    client = FakeLLMClient()
    monkeypatch.setattr(tasks, "build_llm_client", lambda: client)
    return client


def _run(user, page_list):
    document = ExamDocument.objects.create(
        user=user,
        sha256="d" * 64,
        original_filename="laudo.pdf",
        page_count=len(page_list),
        page_text=[{"number": p.number, "method": p.method, "lines": p.lines} for p in page_list],
    )
    tasks.process_exam_document(document.pk, True)
    document.refresh_from_db()
    return document


@pytest.mark.django_db
def test_repeated_footer_is_stripped_as_boilerplate_but_still_names_the_laboratory(
    user, llm, monkeypatch
):
    # Rodapé que termina em linha comum (sem "assinado digitalmente"): é removido por inteiro.
    monkeypatch.setattr(soft, "FOOTER", [*soft.FOOTER[:3], "RODAPÉ FIXO EXEMPLO"])
    page_list = soft.pages(soft.ALBUMIN, soft.TSH)
    cleaned, _ = blocks_mod.strip_boilerplate(page_list)
    assert not any("CRM-PJ" in line for lines in cleaned for line in lines)

    document = _run(user, page_list)

    assert document.laboratory is not None
    assert document.laboratory.name == "Laboratório Exemplo"


@pytest.mark.django_db
def test_single_page_softlab_footer_names_the_laboratory(user, llm):
    document = _run(user, soft.pages(soft.ALBUMIN))

    assert document.laboratory.name == "Laboratório Exemplo"


@pytest.mark.django_db
def test_footer_with_a_synthetic_full_name_and_accents(user, llm, monkeypatch):
    monkeypatch.setattr(soft, "FOOTER", ["LAB. JOSÉ DA EXEMPLO CRM-PJ 12", *soft.FOOTER[1:]])
    document = _run(user, soft.pages(soft.ALBUMIN, soft.TSH))

    assert document.laboratory.name == "Laboratório José da Exemplo"


# --- endereço web no corpo do laudo não é o laboratório ------------------------------------


def test_domain_outside_the_given_band_is_not_the_laboratory():
    body_note = "Referência: www.hospital-exemplo.org/projeto-de-referencia"

    assert laboratory_from_text(body_note) == "Hospital Exemplo"  # sem faixa: comportamento antigo
    assert laboratory_from_text(body_note, domain_text="") == ""
    assert laboratory_from_text(body_note, domain_text="www.timbrado-exemplo.com.br") == (
        "Timbrado Exemplo"
    )


@pytest.mark.django_db
def test_bibliography_url_in_a_result_note_is_not_taken_as_the_laboratory(user, llm):
    filler = [f"linha de corpo {n}" for n in range(1, 21)]  # empurra a nota para o meio da página
    note_body = [
        *ficha.single("UREIA, soro", "27 mg/dL | 10 a 50 mg/dL", method="Cinético, UV"),
        *filler,
        "NOTA: valores de referência pediátricos em www.hospital-exemplo.org/projeto",
        "Hospital Exemplo de Crianças.",
        *filler,
    ]

    document = _run(user, ficha.pages(note_body))

    assert document.laboratory is None
