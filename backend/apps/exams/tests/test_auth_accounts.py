"""Login, tokens, troca de senha e isolamento do administrador. Dados inventados."""

import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.exams.codes import ErrorCode
from apps.exams.models import Analyte, ExamDocument, ExamResult, Profile

from .conftest import make_user
from .test_api_admin_users import make_admin

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "senha-forte-123"
TOKEN_URL = "/api/v1/auth/token/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
ME_URL = "/api/v1/auth/me/"
CHANGE_URL = "/api/v1/auth/change-password/"


@pytest.fixture(autouse=True)
def _fresh_throttle_counters():
    """O LocMemCache vive no processo todo: sem limpar, os logins de outros testes esgotam o
    limite `login` (10/hora por IP) e este recebe 429."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def bearer(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def login(email: str, password: str = PASSWORD):
    return APIClient().post(TOKEN_URL, {"username": email, "password": password}, format="json")


# --- usuário inativo ------------------------------------------------------------------------


def deactivate(user):
    User.objects.filter(pk=user.pk).update(is_active=False)


def test_an_inactive_user_cannot_log_in(user):
    assert login(user.email).status_code == 200
    deactivate(user)

    assert login(user.email).status_code == 401


def test_an_inactive_user_cannot_refresh_their_token(user):
    refresh = str(RefreshToken.for_user(user))
    assert APIClient().post(REFRESH_URL, {"refresh": refresh}, format="json").status_code == 200
    deactivate(user)

    assert APIClient().post(REFRESH_URL, {"refresh": refresh}, format="json").status_code == 401


def test_an_inactive_user_cannot_use_an_existing_access_token_on_rest_or_graphql(user):
    access = str(AccessToken.for_user(user))
    assert bearer(access).get(ME_URL).status_code == 200
    deactivate(user)

    assert bearer(access).get(ME_URL).status_code == 401
    graphql = APIClient().post(
        "/graphql/",
        data=json.dumps({"query": "{ examDocuments { id } }"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )
    assert graphql.status_code == 401


def test_reactivating_restores_access(user):
    access = str(AccessToken.for_user(user))
    deactivate(user)
    User.objects.filter(pk=user.pk).update(is_active=True)

    assert bearer(access).get(ME_URL).status_code == 200


def test_login_updates_last_login(user):
    assert user.last_login is None

    assert login(user.email).status_code == 200

    user.refresh_from_db()
    assert user.last_login is not None


# --- /auth/me/ ------------------------------------------------------------------------------


def test_me_exposes_the_role_and_the_pending_password_change(user):
    api = APIClient()
    api.force_authenticate(user)
    body = api.get(ME_URL).json()
    assert body["is_admin"] is False and body["must_change_password"] is False

    Profile.objects.filter(user=user).update(is_admin=True, must_change_password=True)
    body = api.get(ME_URL).json()
    assert body["is_admin"] is True and body["must_change_password"] is True


def test_me_creates_the_profile_lazily_as_a_plain_member(db):
    bare = User.objects.create_user(username="x@example.com", email="x@example.com", password="x")
    api = APIClient()
    api.force_authenticate(bare)

    body = api.get(ME_URL).json()

    assert body["is_admin"] is False and Profile.objects.filter(user=bare).exists()


def test_a_user_cannot_promote_themselves_through_the_profile_endpoints(user):
    api = APIClient()
    api.force_authenticate(user)

    response = api.put(
        "/api/v1/profile/",
        {
            "sex": "male",
            "birth_date": "1990-01-02",
            "is_admin": True,
            "must_change_password": False,
        },
        format="json",
    )

    assert response.status_code == 200
    assert "is_admin" not in response.json()
    assert Profile.objects.get(user=user).is_admin is False
    assert api.post("/api/v1/admin/users/", {"email": "a@b.com"}, format="json").status_code == 403


# --- troca da própria senha -----------------------------------------------------------------


def change(api, current=PASSWORD, new="outra-senha-segura-81"):
    return api.post(CHANGE_URL, {"current_password": current, "new_password": new}, format="json")


def authed(user) -> APIClient:
    api = APIClient()
    api.force_authenticate(user)
    return api


def test_change_password_requires_authentication():
    assert change(APIClient()).status_code == 401


def test_change_password_sets_the_new_password_and_clears_the_flag(user):
    Profile.objects.filter(user=user).update(must_change_password=True)

    response = change(authed(user))

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password("outra-senha-segura-81")
    assert user.exam_profile.must_change_password is False
    assert login(user.email, "outra-senha-segura-81").status_code == 200
    assert login(user.email, PASSWORD).status_code == 401
    assert "no-store" in response["Cache-Control"]


def test_change_password_returns_working_new_tokens_and_revokes_the_old_ones(user):
    old_access = str(AccessToken.for_user(user))
    old_refresh = str(RefreshToken.for_user(user))

    body = change(bearer(old_access)).json()

    assert bearer(body["access"]).get(ME_URL).status_code == 200
    refreshed = APIClient().post(REFRESH_URL, {"refresh": body["refresh"]}, format="json").json()
    assert bearer(refreshed["access"]).get(ME_URL).status_code == 200
    assert bearer(old_access).get(ME_URL).status_code == 401
    stale = APIClient().post(REFRESH_URL, {"refresh": old_refresh}, format="json")
    # Mesmo que a renovação responda, o access que sai dela carrega o hash antigo e é recusado.
    if stale.status_code == 200:
        assert bearer(stale.json()["access"]).get(ME_URL).status_code == 401


def test_change_password_rejects_a_wrong_current_password(user):
    response = change(authed(user), current="errada-errada-1")

    assert response.status_code == 400
    assert response.json() == {"code": str(ErrorCode.CURRENT_PASSWORD_INCORRECT), "params": {}}
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.parametrize(
    ("new", "code"),
    [
        ("curta1", ErrorCode.PASSWORD_TOO_SHORT),
        ("password", ErrorCode.PASSWORD_TOO_COMMON),
        ("93847561029384", ErrorCode.PASSWORD_ENTIRELY_NUMERIC),
        (PASSWORD, ErrorCode.PASSWORD_UNCHANGED),
    ],
)
def test_change_password_validates_the_new_one_with_codes(user, new, code):
    response = change(authed(user), new=new)

    assert response.status_code == 400
    assert str(code) in response.json()["new_password"]
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_change_password_is_throttled(user, settings):
    from django.core.cache import cache

    cache.clear()
    api = authed(user)
    statuses = [change(api, current="errada-errada-1").status_code for _ in range(12)]

    assert statuses[:10] == [400] * 10
    assert statuses[10] == 429


# --- o administrador não ganha acesso a exames ----------------------------------------------


@pytest.fixture
def private_data(db):
    """Um laudo com um resultado e o histórico de uma pessoa comum."""
    owner = make_user("dono@example.com", "Dona", "Da Silva")
    document = ExamDocument.objects.create(
        user=owner, sha256="b" * 64, original_filename="laudo.pdf", status="completed"
    )
    analyte = Analyte.objects.get(slug="glicose")
    result = ExamResult.objects.create(
        user=owner,
        document=document,
        analyte=analyte,
        name_raw="GLICOSE",
        value_raw="99",
        value_numeric=99,
        value_canonical=99,
        unit="mg/dL",
    )
    return owner, document, result


def test_an_admin_gets_404_for_another_users_documents_and_results(private_data):
    _, document, result = private_data
    api = authed(make_admin())

    assert api.get("/api/v1/exam-documents/").json()["count"] == 0
    assert api.get(f"/api/v1/exam-documents/{document.pk}/").status_code == 404
    assert api.get(f"/api/v1/exam-documents/{document.pk}/results/").status_code == 404
    assert (
        api.patch(
            f"/api/v1/exam-documents/{document.pk}/", {"laboratory_name": "X"}, format="json"
        ).status_code
        == 404
    )
    assert api.delete(f"/api/v1/exam-documents/{document.pk}/").status_code == 404
    assert api.get("/api/v1/exam-results/").json()["count"] == 0
    assert api.get(f"/api/v1/exam-results/{result.pk}/").status_code == 404
    assert (
        api.patch(
            f"/api/v1/exam-results/{result.pk}/", {"value_raw": "1"}, format="json"
        ).status_code
        == 404
    )
    assert api.delete(f"/api/v1/exam-results/{result.pk}/").status_code == 404
    assert ExamDocument.objects.filter(pk=document.pk).exists()


def test_an_admin_sees_no_history_overview_or_laboratories_of_others(private_data):
    owner, document, _ = private_data
    api = authed(make_admin())

    history = api.get("/api/v1/analytes/glicose/history/").json()
    assert history["points"] == []
    assert api.get("/api/v1/overview/").json() == [] or all(
        row.get("latest_value") is None for row in api.get("/api/v1/overview/").json()
    )
    assert api.get("/api/v1/laboratories/").json() == []
    # O dono continua vendo os próprios dados.
    assert authed(owner).get(f"/api/v1/exam-documents/{document.pk}/").status_code == 200


def test_an_admin_sees_no_documents_through_graphql(private_data):
    owner, document, _ = private_data
    query = (
        "{ examDocuments { id } examDocument(id: %d) { id } "
        'analyteHistory(slug: "glicose") { points { value } } }' % document.pk
    )

    def run(user):
        return (
            APIClient()
            .post(
                "/graphql/",
                data=json.dumps({"query": query}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}",
            )
            .json()["data"]
        )

    admin_data = run(make_admin())
    assert admin_data["examDocuments"] == []
    assert admin_data["examDocument"] is None
    assert admin_data["analyteHistory"]["points"] == []
    assert len(run(owner)["examDocuments"]) == 1
