import pytest
from reportlab.pdfgen import canvas

from apps.exams.codes import ErrorCode
from apps.exams.extraction import linearize
from apps.exams.extraction.errors import ExtractionError
from apps.exams.extraction.linearize import clean_lines, read_pages, redact


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CPF: 123.456.789-09", "CPF: [CPF]"),
        ("Paciente 123.456.789-09 Cliente", "Paciente [CPF] Cliente"),
        ("CPF 123 456 789 09", "CPF [CPF]"),
        ("CPF: 12345678909", "CPF: [CPF]"),
        ("RG: | 91002384861", "RG: | [RG]"),
        ("RG: 99999999999", "RG: [RG]"),
        ("R.G.: MG-12.345.678", "R.G.: [RG]"),
        ("CNS: 898001234567890", "CNS: [ID]"),
        ("Cartão SUS 123456789", "Cartão SUS [ID]"),
        ("Documento: 987654321", "Documento: [ID]"),
        ("Identidade | 1234567 SSP", "Identidade | [ID] SSP"),
        ("898001234567890", "[ID]"),
        ("898 0012 3456 7890", "[ID]"),
        ("Contato: maria.silva@exemplo.com.br", "Contato: [EMAIL]"),
        ("Tel: (11) 98765-4321", "Tel: [TEL]"),
        ("Telefone | 11987654321", "Telefone | [TEL]"),
        ("Fone 3333-4444", "Fone [TEL]"),
        ("WhatsApp: 85 99999-0000", "WhatsApp: [TEL]"),
        ("Ligar (85) 3222-1111 hoje", "Ligar [TEL] hoje"),
    ],
)
def test_identifiers_are_redacted(raw, expected):
    assert redact(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "O.S: 02111111222",  # número do pedido
        "Atendimento: 02111111222",
        "Resultado: 1234567890",
        "Contagem 1234567890123",
        "Protocolo 99999999999",
        "Glicose | 103 | mg/dL | 70 a 99",
        "Leucócitos 4.000 /uL",
        "TSH 1.251 uIU/mL",
        "Documento assinado digitalmente em 25/08/2026",
        "Telefone do laboratório ver rodapé",
        "1234-5678",  # sem rótulo e sem parênteses: pode ser um resultado
    ],
)
def test_legitimate_numbers_are_preserved(raw):
    assert redact(raw) == raw


def test_clean_lines_redacts_each_line():
    lines = clean_lines(["CPF:   123.456.789-09", "RG: | 91002384861", "O.S: 02111111222"])
    assert lines == ["CPF: [CPF]", "RG: | [RG]", "O.S: 02111111222"]


def _pdf(path, size, text="Hemoglobina 13,5 g/dL referência 13,0 a 17,0"):
    pdf = canvas.Canvas(str(path), pagesize=size)
    pdf.drawString(40, 100, text)
    pdf.showPage()
    pdf.save()
    return path


def _must_not_transcribe(png):
    raise AssertionError("não deveria renderizar a página")


def test_oversized_mediabox_is_rejected_before_rendering(tmp_path):
    path = _pdf(tmp_path / "gigante.pdf", (3000, 800), text="")
    with pytest.raises(ExtractionError) as info:
        read_pages(path, _must_not_transcribe, max_pages=5)
    assert info.value.code == ErrorCode.PDF_UNREADABLE


def test_too_many_words_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(linearize, "MAX_WORDS_PER_PAGE", 3)
    path = _pdf(tmp_path / "palavras.pdf", (595, 842))
    with pytest.raises(ExtractionError) as info:
        read_pages(path, _must_not_transcribe, max_pages=5)
    assert info.value.code == ErrorCode.PDF_UNREADABLE


def test_normal_page_is_read(tmp_path):
    path = _pdf(tmp_path / "normal.pdf", (595, 842))
    pages = read_pages(path, _must_not_transcribe, max_pages=5)
    assert pages[0].method == "text"
