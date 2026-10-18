"""Administração de usuários: /api/v1/admin/users/. Dados inventados."""

import logging

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.exams import accounts
from apps.exams.codes import ErrorCode
from apps.exams.errors import CodedAPIError
from apps.exams.models import Profile

from .conftest import make_user

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/admin/users/"
User = get_user_model()


@pytest.fixture(autouse=True)
def _fresh_throttle_counters():
    """O LocMemCache vive no processo todo: sem limpar, os logins de outros testes esgotam o
    limite `login` (10/hora por IP) e este recebe 429."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def make_admin(email="admin@example.com", first="Ada", last="Admin"):
    user = make_user(email, first, last)
    Profile.objects.filter(user=user).update(is_admin=True)
    return user


def client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def admin(db):
    return make_admin()


@pytest.fixture
def admin_api(admin):
    return client_for(admin)


def detail(user) -> str:
    return f"{LIST_URL}{user.pk}/"


def reset_url(user) -> str:
    return f"{LIST_URL}{user.pk}/reset-password/"


# --- permissão ------------------------------------------------------------------------------


def test_anonymous_gets_401():
    assert APIClient().get(LIST_URL).status_code == 401
    assert APIClient().post(LIST_URL, {}, format="json").status_code == 401


def test_a_plain_member_gets_403_on_every_admin_endpoint(user, other_user):
    api = client_for(user)

    assert api.get(LIST_URL).status_code == 403
    assert api.post(LIST_URL, {"email": "x@example.com"}, format="json").status_code == 403
    assert api.patch(detail(other_user), {"is_admin": True}, format="json").status_code == 403
    assert api.post(reset_url(other_user), format="json").status_code == 403
    other_user.refresh_from_db()
    assert not other_user.exam_profile.is_admin


def test_a_user_without_a_profile_row_is_not_an_admin(db):
    bare = User.objects.create_user(username="semperfil@example.com", password="x-y-z-12345")
    assert not Profile.objects.filter(user=bare).exists()

    assert client_for(bare).get(LIST_URL).status_code == 403
    assert accounts.is_app_admin(bare) is False


def test_an_inactive_admin_is_refused_even_with_a_forced_session(admin):
    User.objects.filter(pk=admin.pk).update(is_active=False)
    admin.refresh_from_db()

    assert client_for(admin).get(LIST_URL).status_code in (401, 403)
    assert accounts.is_app_admin(admin) is False


def test_django_staff_flags_do_not_make_an_app_admin(user):
    User.objects.filter(pk=user.pk).update(is_staff=True, is_superuser=True)
    user.refresh_from_db()

    assert client_for(user).get(LIST_URL).status_code == 403


def test_the_admin_role_is_read_per_request_so_revoking_is_immediate(admin, admin_api):
    assert admin_api.get(LIST_URL).status_code == 200
    Profile.objects.filter(user=admin).update(is_admin=False)

    assert admin_api.get(LIST_URL).status_code == 403


# --- listagem -------------------------------------------------------------------------------


def test_list_has_account_data_only_ordered_by_date_joined(admin, admin_api, user):
    response = admin_api.get(LIST_URL)

    assert response.status_code == 200
    rows = response.json()
    assert [r["id"] for r in rows] == [admin.pk, user.pk]
    assert set(rows[0]) == {
        "id",
        "email",
        "name",
        "is_active",
        "is_admin",
        "must_change_password",
        "date_joined",
        "last_login",
    }
    assert rows[0]["name"] == "Ada Admin" and rows[0]["is_admin"] is True
    assert rows[1]["is_admin"] is False and rows[1]["is_active"] is True
    assert rows[0]["last_login"] is None


def test_list_never_exposes_exam_derived_data(admin_api, user):
    from apps.exams.models import ExamDocument

    ExamDocument.objects.create(user=user, sha256="a" * 64, original_filename="laudo-privado.pdf")

    body = admin_api.get(LIST_URL).content.decode()

    for forbidden in ("laudo-privado", "document", "result", "count", "sha256"):
        assert forbidden not in body


def test_a_user_without_a_profile_shows_as_member(admin_api):
    User.objects.create_user(username="nunca@example.com", email="nunca@example.com", password="x")

    row = [r for r in admin_api.get(LIST_URL).json() if r["email"] == "nunca@example.com"][0]

    assert row["is_admin"] is False and row["must_change_password"] is False


def test_list_uses_a_constant_number_of_queries(admin_api, django_assert_max_num_queries):
    for index in range(8):
        make_user(f"pessoa{index}@example.com", f"P{index}", "Teste")

    with django_assert_max_num_queries(6):
        assert admin_api.get(LIST_URL).status_code == 200


# --- criação --------------------------------------------------------------------------------


def create(api, **extra):
    payload = {"email": "nova@example.com", "name": "Nora Nova", **extra}
    return api.post(LIST_URL, payload, format="json")


def test_create_without_password_generates_a_temporary_one_shown_once(admin_api):
    response = create(admin_api)

    assert response.status_code == 201
    body = response.json()
    secret = body["temporary_password"]
    assert len(secret) >= 14
    assert not set(secret) & set("0O1lIo")  # alfabeto sem ambíguos
    assert body["email"] == "nova@example.com" and body["name"] == "Nora Nova"
    assert body["is_admin"] is False and body["is_active"] is True
    assert body["must_change_password"] is True
    assert "no-store" in response["Cache-Control"]

    created = User.objects.get(email="nova@example.com")
    assert created.check_password(secret)
    assert created.password != secret  # nunca em claro
    assert created.username == "nova@example.com"
    assert (created.first_name, created.last_name) == ("Nora", "Nova")
    assert created.exam_profile.must_change_password is True
    # Uma segunda leitura (lista) não repete a senha.
    assert secret not in admin_api.get(LIST_URL).content.decode()


def test_temporary_passwords_are_random_and_readable():
    passwords = {accounts.generate_temporary_password() for _ in range(200)}

    assert len(passwords) == 200
    for secret in passwords:
        assert len(secret) >= 14
        assert set(secret) <= set(accounts.TEMP_PASSWORD_ALPHABET)
        assert any(c.islower() for c in secret)
        assert any(c.isupper() for c in secret)
        assert any(c.isdigit() for c in secret)


def test_create_with_a_typed_password_returns_no_secret_and_still_forces_a_change(admin_api):
    response = create(admin_api, password="uma-senha-bem-longa-93", is_admin=True)

    assert response.status_code == 201
    body = response.json()
    assert body["temporary_password"] is None
    assert "uma-senha-bem-longa-93" not in response.content.decode()
    assert body["is_admin"] is True and body["must_change_password"] is True
    created = User.objects.get(email="nova@example.com")
    assert created.check_password("uma-senha-bem-longa-93")
    assert created.exam_profile.is_admin is True


def test_blank_password_means_generate(admin_api):
    assert create(admin_api, password="").json()["temporary_password"]


def test_create_rejects_a_taken_email_ignoring_case(admin_api, user):
    response = create(admin_api, email=user.email.upper())

    assert response.status_code == 400
    assert response.json() == {"email": [str(ErrorCode.USER_EMAIL_TAKEN)]}
    assert User.objects.filter(email__iexact=user.email).count() == 1


def test_create_rejects_an_email_that_is_an_existing_username(admin_api, db):
    User.objects.create_user(username="login@example.com", email="", password="x-y-z-12345")

    response = create(admin_api, email="Login@Example.com")

    assert response.json() == {"email": [str(ErrorCode.USER_EMAIL_TAKEN)]}


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("curta1", ErrorCode.PASSWORD_TOO_SHORT),
        ("password", ErrorCode.PASSWORD_TOO_COMMON),
        ("93847561029384", ErrorCode.PASSWORD_ENTIRELY_NUMERIC),
        ("nova@example.com", ErrorCode.PASSWORD_TOO_SIMILAR),
    ],
)
def test_a_typed_password_goes_through_the_django_validators_with_codes(admin_api, password, code):
    response = create(admin_api, password=password)

    assert response.status_code == 400
    assert str(code) in response.json()["password"]
    assert not User.objects.filter(email="nova@example.com").exists()


def test_create_requires_a_valid_email(admin_api):
    assert admin_api.post(LIST_URL, {"name": "Sem e-mail"}, format="json").status_code == 400
    assert create(admin_api, email="isto-nao-e-email").status_code == 400


def test_create_cannot_set_django_staff_flags(admin_api):
    create(admin_api, is_staff=True, is_superuser=True)

    created = User.objects.get(email="nova@example.com")
    assert not created.is_staff and not created.is_superuser


def test_the_new_user_can_log_in_with_the_temporary_password(admin_api):
    secret = create(admin_api).json()["temporary_password"]

    login = APIClient().post(
        "/api/v1/auth/token/", {"username": "nova@example.com", "password": secret}, format="json"
    )

    assert login.status_code == 200


def test_create_without_a_name_is_allowed(admin_api):
    response = create(admin_api, name="")

    assert response.status_code == 201 and response.json()["name"] == ""


def test_multiword_names_round_trip(admin_api):
    body = create(admin_api, name="Maria da Silva Souza").json()

    assert body["name"] == "Maria da Silva Souza"


# --- edição ---------------------------------------------------------------------------------


def test_patch_updates_name_active_and_admin(admin_api, user):
    response = admin_api.patch(
        detail(user),
        {"name": "Novo Nome Completo", "is_active": False, "is_admin": True},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Novo Nome Completo"
    assert body["is_active"] is False and body["is_admin"] is True
    user.refresh_from_db()
    assert user.is_active is False and user.exam_profile.is_admin is True
    assert (user.first_name, user.last_name) == ("Novo", "Nome Completo")


def test_patch_creates_the_profile_row_lazily_when_promoting(admin_api, db):
    bare = User.objects.create_user(username="b@example.com", email="b@example.com", password="x")

    assert admin_api.patch(detail(bare), {"is_admin": True}, format="json").json()["is_admin"]
    assert Profile.objects.get(user=bare).is_admin is True


def test_patch_ignores_everything_else(admin_api, user):
    old_email, old_hash = user.email, user.password

    response = admin_api.patch(
        detail(user),
        {"email": "outro@example.com", "password": "novissima-senha-77", "is_staff": True},
        format="json",
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert (user.email, user.password, user.is_staff) == (old_email, old_hash, False)


def test_patch_unknown_user_is_404(admin_api):
    assert (
        admin_api.patch(f"{LIST_URL}999999/", {"is_active": False}, format="json").status_code
        == 404
    )


def test_there_is_no_delete_or_put_endpoint(admin_api, user):
    assert admin_api.delete(detail(user)).status_code == 405
    assert admin_api.put(detail(user), {"name": "x"}, format="json").status_code == 405
    assert User.objects.filter(pk=user.pk).exists()


def test_an_admin_cannot_deactivate_or_demote_themselves(admin, admin_api):
    for payload in ({"is_active": False}, {"is_admin": False}):
        response = admin_api.patch(detail(admin), payload, format="json")

        assert response.status_code == 409
        assert response.json() == {"code": str(ErrorCode.ADMIN_SELF_ACTION), "params": {}}
    admin.refresh_from_db()
    assert admin.is_active and admin.exam_profile.is_admin


def test_an_admin_can_still_rename_themselves_and_resend_unchanged_flags(admin, admin_api):
    response = admin_api.patch(
        detail(admin), {"name": "Ada Lovelace", "is_active": True, "is_admin": True}, format="json"
    )

    assert response.status_code == 200 and response.json()["name"] == "Ada Lovelace"


def test_one_admin_can_demote_another_when_a_third_remains(admin, admin_api):
    second = make_admin("segundo@example.com", "Sê", "Gundo")

    response = admin_api.patch(detail(second), {"is_admin": False}, format="json")

    assert response.status_code == 200 and response.json()["is_admin"] is False


def test_the_last_active_admin_can_never_be_deactivated_or_demoted(admin, db):
    # O ator que chega aqui pela API já é outro administrador ativo; a trava cobre a corrida
    # (ele perdeu o papel no instante anterior), então é exercitada direto no serviço.
    stranger = make_user("fora@example.com")

    for changes in ({"is_active": False}, {"is_admin": False}):
        with pytest.raises(CodedAPIError) as caught:
            accounts.update_user(stranger, admin.pk, changes)
        assert caught.value.error_code == str(ErrorCode.LAST_ADMIN)
        assert caught.value.status_code == 409
    admin.refresh_from_db()
    assert admin.is_active and admin.exam_profile.is_admin


def test_inactive_admins_do_not_count_as_a_remaining_admin(admin, db):
    ghost = make_admin("fantasma@example.com")
    User.objects.filter(pk=ghost.pk).update(is_active=False)

    with pytest.raises(CodedAPIError) as caught:
        accounts.update_user(ghost, admin.pk, {"is_admin": False})

    assert caught.value.error_code == str(ErrorCode.LAST_ADMIN)


def test_demoting_a_deactivated_admin_is_allowed_even_if_it_is_the_only_other(admin, admin_api):
    ghost = make_admin("fantasma@example.com")
    User.objects.filter(pk=ghost.pk).update(is_active=False)

    assert admin_api.patch(detail(ghost), {"is_admin": False}, format="json").status_code == 200


# --- redefinição de senha -------------------------------------------------------------------


def test_reset_password_returns_a_new_temporary_password_once(admin_api, user):
    old_hash = user.password

    response = admin_api.post(reset_url(user), format="json")

    assert response.status_code == 200
    secret = response.json()["temporary_password"]
    assert len(secret) >= 14 and "no-store" in response["Cache-Control"]
    user.refresh_from_db()
    assert user.password != old_hash and user.check_password(secret)
    assert user.exam_profile.must_change_password is True
    assert secret not in admin_api.get(LIST_URL).content.decode()


def test_an_admin_cannot_reset_their_own_password_here(admin, admin_api):
    response = admin_api.post(reset_url(admin), format="json")

    assert response.status_code == 409
    assert response.json()["code"] == str(ErrorCode.ADMIN_SELF_ACTION)


def test_reset_password_for_unknown_user_is_404(admin_api):
    assert admin_api.post(f"{LIST_URL}999999/reset-password/", format="json").status_code == 404


def test_reset_password_kills_the_users_previous_tokens(admin_api, user):
    from rest_framework_simplejwt.tokens import AccessToken

    old_access = str(AccessToken.for_user(user))
    probe = APIClient()
    probe.credentials(HTTP_AUTHORIZATION=f"Bearer {old_access}")
    assert probe.get("/api/v1/auth/me/").status_code == 200

    admin_api.post(reset_url(user), format="json")

    assert probe.get("/api/v1/auth/me/").status_code == 401


# --- log ------------------------------------------------------------------------------------


def test_actions_are_logged_without_passwords_or_exam_data(admin, admin_api, user, caplog):
    caplog.set_level(logging.INFO, logger="apps.exams.admin_actions")

    secret_created = create(admin_api).json()["temporary_password"]
    target = User.objects.get(email="nova@example.com")
    admin_api.patch(detail(target), {"is_active": False}, format="json")
    secret_reset = admin_api.post(reset_url(user), format="json").json()["temporary_password"]

    text = caplog.text
    assert f"ator={admin.pk} alvo={target.pk} ação=create_user" in text
    assert f"alvo={target.pk} ação=update_user:is_active" in text
    assert f"alvo={user.pk} ação=reset_password" in text
    for forbidden in (secret_created, secret_reset, "nova@example.com", user.email):
        assert forbidden not in text


def test_login_does_not_log_passwords_either(caplog, user):
    caplog.set_level(logging.DEBUG)
    APIClient().post(
        "/api/v1/auth/token/",
        {"username": user.email, "password": "senha-forte-123"},
        format="json",
    )

    assert "senha-forte-123" not in caplog.text
