"""`manage.py make_admin`. Dados inventados."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.exams.models import Profile

pytestmark = pytest.mark.django_db

User = get_user_model()


def run(*args) -> str:
    out = StringIO()
    call_command("make_admin", *args, stdout=out)
    return out.getvalue()


def test_grants_the_role_and_prints_only_the_email_and_the_role(user):
    output = run(user.email)

    assert output == f"{user.email}: administrador\n"
    assert Profile.objects.get(user=user).is_admin is True


def test_is_idempotent(user):
    first = run(user.email)
    second = run(user.email)

    assert first == second
    assert Profile.objects.filter(user=user, is_admin=True).count() == 1


def test_revoke_returns_the_account_to_member_and_is_idempotent(user):
    run(user.email)

    assert run(user.email, "--revoke") == f"{user.email}: membro\n"
    assert run(user.email, "--revoke") == f"{user.email}: membro\n"
    assert Profile.objects.get(user=user).is_admin is False


def test_lookup_ignores_case_and_creates_a_missing_profile(db):
    bare = User.objects.create_user(
        username="Sem@Perfil.example", email="sem@perfil.example", password="x"
    )

    output = run("SEM@PERFIL.EXAMPLE")

    assert output == "sem@perfil.example: administrador\n"
    assert Profile.objects.get(user=bare).is_admin is True


def test_does_not_touch_the_django_staff_flags(user):
    run(user.email)

    user.refresh_from_db()
    assert not user.is_staff and not user.is_superuser


def test_works_on_an_inactive_account_so_it_can_be_revoked(user):
    run(user.email)
    User.objects.filter(pk=user.pk).update(is_active=False)

    assert run(user.email, "--revoke") == f"{user.email}: membro\n"


def test_unknown_user_is_a_command_error(db):
    with pytest.raises(CommandError, match="Usuário não encontrado"):
        run("ninguem@example.com")
