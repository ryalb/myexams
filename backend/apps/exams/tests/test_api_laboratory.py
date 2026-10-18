"""PATCH /exam-documents/<id>/ (laboratório do laudo) e GET /laboratories/. Dados inventados."""

import pytest
from rest_framework.test import APIClient

from apps.exams.codes import ErrorCode
from apps.exams.extraction.pipeline import laboratory_for
from apps.exams.models import DocumentStatus, ExamDocument, Laboratory

pytestmark = pytest.mark.django_db

MAX_NAME = 255


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def other_api(other_user):
    client = APIClient()
    client.force_authenticate(other_user)
    return client


def lab(name: str) -> Laboratory:
    laboratory = laboratory_for(name)
    assert laboratory is not None
    return laboratory


_counter = iter(range(1, 10_000))


def doc(
    user,
    *,
    laboratory=None,
    accession="",
    status=DocumentStatus.COMPLETED,
) -> ExamDocument:
    return ExamDocument.objects.create(
        user=user,
        sha256=f"{next(_counter):064d}",
        original_filename="laudo.pdf",
        status=status,
        laboratory=laboratory,
        accession_number=accession,
    )


def url(document: ExamDocument) -> str:
    return f"/api/v1/exam-documents/{document.pk}/"


def patch(api, document, name):
    return api.patch(url(document), {"laboratory_name": name}, format="json")


# --- contrato básico ---------------------------------------------------------------------


def test_patch_sets_the_laboratory_and_returns_the_detail_representation(api, user):
    document = doc(user)

    response = patch(api, document, "Laboratório Exemplo")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document.pk
    assert body["laboratory_name"] == "Laboratório Exemplo"
    assert body["laboratory"] == Laboratory.objects.get().pk
    assert "page_text" in body  # representação de detalhe
    assert {"result_count", "review_count", "out_of_range_count"} <= set(body)
    document.refresh_from_db()
    assert document.laboratory.name == "Laboratório Exemplo"


def test_patch_requires_authentication(user):
    document = doc(user)

    response = APIClient().patch(url(document), {"laboratory_name": "X Lab"}, format="json")

    assert response.status_code == 401


def test_other_users_document_is_404_and_untouched(other_api, user):
    original = lab("Laboratório Original")
    document = doc(user, laboratory=original)

    response = patch(other_api, document, "Laboratório Invasor")

    assert response.status_code == 404
    document.refresh_from_db()
    assert document.laboratory == original
    assert not Laboratory.objects.filter(name="Laboratório Invasor").exists()


def test_put_is_not_allowed(api, user):
    document = doc(user)

    response = api.put(url(document), {"laboratory_name": "Laboratório Exemplo"}, format="json")

    assert response.status_code == 405
    document.refresh_from_db()
    assert document.laboratory is None


def test_only_laboratory_name_is_writable(api, user):
    document = doc(user, accession="123", status=DocumentStatus.NEEDS_REVIEW)

    response = api.patch(
        url(document),
        {
            "laboratory_name": "Laboratório Exemplo",
            "status": "failed",
            "accession_number": "999",
            "original_filename": "outro.pdf",
            "ordering_physician": "Fulano",
            "laboratory": 12345,
            "page_text": ["x"],
        },
        format="json",
    )

    assert response.status_code == 200
    document.refresh_from_db()
    assert document.status == DocumentStatus.NEEDS_REVIEW
    assert document.accession_number == "123"
    assert document.original_filename == "laudo.pdf"
    assert document.ordering_physician == ""
    assert document.page_text == []
    assert document.laboratory.name == "Laboratório Exemplo"


def test_missing_laboratory_name_is_a_validation_error(api, user):
    document = doc(user, laboratory=lab("Laboratório Exemplo"))

    response = api.patch(url(document), {}, format="json")

    assert response.status_code == 400
    assert "laboratory_name" in response.json()
    document.refresh_from_db()
    assert document.laboratory is not None


def test_null_laboratory_name_is_a_validation_error(api, user):
    document = doc(user, laboratory=lab("Laboratório Exemplo"))

    response = api.patch(url(document), {"laboratory_name": None}, format="json")

    assert response.status_code == 400
    document.refresh_from_db()
    assert document.laboratory is not None


# --- normalização do nome ---------------------------------------------------------------


def test_name_is_trimmed_and_inner_whitespace_collapsed(api, user):
    document = doc(user)

    response = patch(api, document, "  Laboratório \t  Exemplo \n Central  ")

    assert response.status_code == 200
    assert response.json()["laboratory_name"] == "Laboratório Exemplo Central"


def test_name_of_255_chars_is_accepted(api, user):
    document = doc(user)

    response = patch(api, document, "a" * MAX_NAME)

    assert response.status_code == 200
    assert len(response.json()["laboratory_name"]) == MAX_NAME


def test_name_over_255_chars_gives_a_coded_error_and_changes_nothing(api, user):
    original = lab("Laboratório Original")
    document = doc(user, laboratory=original)

    response = patch(api, document, "a" * (MAX_NAME + 1))

    assert response.status_code == 400
    assert response.json() == {
        "code": ErrorCode.LABORATORY_NAME_TOO_LONG.value,
        "params": {"max": MAX_NAME},
    }
    document.refresh_from_db()
    assert document.laboratory == original


def test_length_is_measured_after_collapsing_whitespace(api, user):
    document = doc(user)

    response = patch(api, document, "   " + "a" * MAX_NAME + "      ")

    assert response.status_code == 200


# --- limpar ------------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", " \t\n "])
def test_blank_name_clears_the_laboratory_without_deleting_the_shared_row(api, user, blank):
    original = lab("Laboratório Exemplo")
    document = doc(user, laboratory=original)

    response = patch(api, document, blank)

    assert response.status_code == 200
    body = response.json()
    assert body["laboratory"] is None
    assert body["laboratory_name"] == ""
    document.refresh_from_db()
    assert document.laboratory is None
    assert Laboratory.objects.filter(pk=original.pk).exists()


# --- laboratório global compartilhado ----------------------------------------------------


def test_same_name_reuses_the_shared_row_even_with_different_case_and_accents(api, user):
    existing = lab("Laboratório Exemplo")
    document = doc(user)

    response = patch(api, document, "  LABORATORIO   exemplo ")

    assert response.status_code == 200
    assert Laboratory.objects.count() == 1
    document.refresh_from_db()
    assert document.laboratory == existing
    assert response.json()["laboratory_name"] == "Laboratório Exemplo"


def test_changing_the_lab_never_renames_a_shared_row_used_by_another_user(api, user, other_user):
    shared = lab("Laboratório Compartilhado")
    mine = doc(user, laboratory=shared)
    theirs = doc(other_user, laboratory=shared)

    response = patch(api, mine, "Laboratório Só Meu")

    assert response.status_code == 200
    shared.refresh_from_db()
    assert shared.name == "Laboratório Compartilhado"
    theirs.refresh_from_db()
    assert theirs.laboratory == shared
    mine.refresh_from_db()
    assert mine.laboratory.name == "Laboratório Só Meu"
    assert mine.laboratory != shared


def test_previous_laboratory_row_is_kept_when_left_unused(api, user):
    old = lab("Laboratório Antigo")
    document = doc(user, laboratory=old)

    patch(api, document, "Laboratório Novo")

    assert Laboratory.objects.filter(pk=old.pk).exists()
    assert not ExamDocument.objects.filter(laboratory=old).exists()


# --- status do laudo ---------------------------------------------------------------------


@pytest.mark.parametrize("busy", [DocumentStatus.UPLOADED, DocumentStatus.EXTRACTING])
def test_refused_while_the_pipeline_is_still_writing(api, user, busy):
    original = lab("Laboratório Original")
    document = doc(user, laboratory=original, status=busy)

    response = patch(api, document, "Laboratório Novo")

    assert response.status_code == 409
    assert response.json() == {"code": ErrorCode.DOCUMENT_BUSY.value, "params": {}}
    document.refresh_from_db()
    assert document.laboratory == original
    assert not Laboratory.objects.filter(name="Laboratório Novo").exists()


@pytest.mark.parametrize(
    "ready", [DocumentStatus.NEEDS_REVIEW, DocumentStatus.COMPLETED, DocumentStatus.FAILED]
)
def test_allowed_once_the_pipeline_is_done(api, user, ready):
    document = doc(user, status=ready)

    response = patch(api, document, "Laboratório Exemplo")

    assert response.status_code == 200


# --- conflito de número de pedido --------------------------------------------------------


def test_accession_clash_in_the_target_lab_returns_a_coded_conflict_and_changes_nothing(api, user):
    target = lab("Laboratório Alvo")
    source = lab("Laboratório Origem")
    existing = doc(user, laboratory=target, accession="777-1")
    document = doc(user, laboratory=source, accession="777-1")

    response = patch(api, document, "Laboratório Alvo")

    assert response.status_code == 409
    assert response.json() == {
        "code": ErrorCode.LABORATORY_ACCESSION_CONFLICT.value,
        "params": {"document_id": existing.pk},
    }
    document.refresh_from_db()
    assert document.laboratory == source


def test_accession_clash_when_clearing_against_another_unlabelled_document(api, user):
    existing = doc(user, laboratory=None, accession="555-9")
    document = doc(user, laboratory=lab("Laboratório Origem"), accession="555-9")

    response = patch(api, document, "")

    assert response.status_code == 409
    assert response.json()["params"] == {"document_id": existing.pk}
    document.refresh_from_db()
    assert document.laboratory is not None


def test_clash_creates_no_new_laboratory_row(api, user):
    other = lab("Laboratório Origem")
    doc(user, laboratory=other, accession="42")
    document = doc(user, laboratory=None, accession="42")
    before = Laboratory.objects.count()

    response = patch(api, document, "Laboratório Origem")

    assert response.status_code == 409
    assert Laboratory.objects.count() == before


def test_same_accession_in_another_users_document_is_not_a_clash(api, user, other_user):
    shared = lab("Laboratório Exemplo")
    doc(other_user, laboratory=shared, accession="900-1")
    document = doc(user, laboratory=None, accession="900-1")

    response = patch(api, document, "Laboratório Exemplo")

    assert response.status_code == 200


def test_same_accession_in_a_different_lab_is_not_a_clash(api, user):
    doc(user, laboratory=lab("Laboratório A"), accession="321")
    document = doc(user, laboratory=lab("Laboratório B"), accession="321")

    response = patch(api, document, "Laboratório C")

    assert response.status_code == 200


def test_reassigning_the_same_laboratory_is_not_a_clash_with_itself(api, user):
    current = lab("Laboratório Exemplo")
    document = doc(user, laboratory=current, accession="111")

    response = patch(api, document, "Laboratório Exemplo")

    assert response.status_code == 200


def test_empty_accession_never_clashes(api, user):
    target = lab("Laboratório Alvo")
    doc(user, laboratory=target, accession="")
    document = doc(user, laboratory=None, accession="")

    response = patch(api, document, "Laboratório Alvo")

    assert response.status_code == 200


# --- GET /laboratories/ ------------------------------------------------------------------


def test_laboratories_requires_authentication():
    assert APIClient().get("/api/v1/laboratories/").status_code == 401


def test_laboratories_lists_only_the_users_own_distinct_labs_ordered_by_name(api, user, other_user):
    zeta = lab("Zeta Diagnósticos")
    alpha = lab("alfa Exemplo")
    beta = lab("Beta Exemplo")
    only_theirs = lab("Laboratório Só do Outro")
    doc(user, laboratory=zeta)
    doc(user, laboratory=zeta)
    doc(user, laboratory=alpha)
    doc(user, laboratory=beta)
    doc(user, laboratory=None)
    doc(other_user, laboratory=beta)
    doc(other_user, laboratory=only_theirs)
    lab("Laboratório Sem Laudo")

    response = api.get("/api/v1/laboratories/")

    assert response.status_code == 200
    assert response.json() == [
        {"id": alpha.pk, "name": "alfa Exemplo"},
        {"id": beta.pk, "name": "Beta Exemplo"},
        {"id": zeta.pk, "name": "Zeta Diagnósticos"},
    ]


def test_laboratories_is_empty_for_a_user_without_labs(other_api, user):
    doc(user, laboratory=lab("Laboratório Exemplo"))

    assert other_api.get("/api/v1/laboratories/").json() == []


def test_laboratories_follow_a_laboratory_change_made_by_patch(api, user):
    document = doc(user, laboratory=lab("Laboratório Antigo"))

    patch(api, document, "Laboratório Novo")

    assert [row["name"] for row in api.get("/api/v1/laboratories/").json()] == ["Laboratório Novo"]


def test_laboratories_is_read_only(api):
    assert api.post("/api/v1/laboratories/", {"name": "X Lab"}, format="json").status_code == 405
    assert api.delete("/api/v1/laboratories/").status_code == 405


def test_laboratories_endpoint_is_throttled_by_the_user_rate(api, settings):
    from rest_framework.throttling import UserRateThrottle

    from apps.exams.views import LaboratoryListAPIView

    classes = [t.__name__ for t in LaboratoryListAPIView.throttle_classes]
    assert UserRateThrottle.__name__ in classes
