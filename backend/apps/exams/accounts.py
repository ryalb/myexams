"""Contas de usuário: papel de administrador do app, senhas e as regras que protegem o acesso.

O administrador do app só gerencia CONTAS (criar, ativar/desativar, promover, redefinir senha).
Ele nunca enxerga exames de ninguém: nenhuma função daqui lê ou conta laudos/resultados.

Cada ação grava uma linha de log (quem, em quem, qual ação). Nunca senha nem dado de exame.
"""

import logging
import secrets
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import NotFound

from .codes import ErrorCode
from .errors import CodedAPIError
from .models import Profile

logger = logging.getLogger("apps.exams.admin_actions")

# Sem caracteres ambíguos (0/O, 1/l/I, o) para ser ditada ou copiada de uma tela sem erro.
_LOWER = "abcdefghijkmnpqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
TEMP_PASSWORD_ALPHABET = _LOWER + _UPPER + _DIGITS
TEMP_PASSWORD_LENGTH = 16  # ~92 bits de entropia

NAME_MAX_LENGTH = 150  # first_name e last_name do Django têm 150 cada

# `ValidationError.code` dos validadores nativos do Django -> código que o frontend traduz.
_PASSWORD_CODES = {
    "password_too_short": ErrorCode.PASSWORD_TOO_SHORT,
    "password_too_common": ErrorCode.PASSWORD_TOO_COMMON,
    "password_entirely_numeric": ErrorCode.PASSWORD_ENTIRELY_NUMERIC,
    "password_too_similar": ErrorCode.PASSWORD_TOO_SIMILAR,
}


def profile_of(user) -> Profile | None:
    """Perfil do usuário, sem criar. `None` se ele nunca abriu o app."""
    try:
        return user.exam_profile
    except ObjectDoesNotExist:
        return None


def is_app_admin(user) -> bool:
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return Profile.objects.filter(user=user, is_admin=True).exists()


def split_name(name: str) -> tuple[str, str]:
    """Nome de exibição -> (first_name, last_name): primeira palavra e o resto."""
    first, _, rest = name.strip().partition(" ")
    return first, rest.strip()


def display_name(user) -> str:
    return " ".join(part for part in (user.first_name, user.last_name) if part)


def generate_temporary_password() -> str:
    """Senha aleatória (`secrets`), com ao menos uma minúscula, uma maiúscula e um dígito."""
    while True:
        candidate = "".join(
            secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH)
        )
        if (
            any(c in _LOWER for c in candidate)
            and any(c in _UPPER for c in candidate)
            and any(c in _DIGITS for c in candidate)
        ):
            return candidate


def password_error_codes(password: str, user=None) -> list[str]:
    """Roda os validadores de senha do Django e devolve CÓDIGOS (sem repetir, em ordem)."""
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        codes: list[str] = []
        for error in exc.error_list:
            code = str(_PASSWORD_CODES.get(error.code or "", ErrorCode.PASSWORD_INVALID))
            if code not in codes:
                codes.append(code)
        return codes
    return []


def find_user_by_login(login: str):
    """Usuário por e-mail ou nome de usuário, sem diferenciar maiúsculas (ativo ou não)."""
    return (
        get_user_model()
        .objects.filter(Q(email__iexact=login) | Q(username__iexact=login))
        .order_by("pk")
        .first()
    )


def email_is_taken(email: str) -> bool:
    return (
        get_user_model().objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists()
    )


def _log(actor, target, action: str) -> None:
    logger.info("ação de administrador: ator=%s alvo=%s ação=%s", actor.pk, target.pk, action)


@dataclass(frozen=True)
class CreatedUser:
    user: object
    temporary_password: str | None  # só quando o servidor gerou a senha; mostrada uma única vez


def create_user(
    actor, *, email: str, name: str, is_admin: bool, password: str | None
) -> CreatedUser:
    """Cria a conta com `must_change_password=True`. Sem `password`, gera uma temporária."""
    generated = password is None
    secret = generate_temporary_password() if generated else password
    first_name, last_name = split_name(name)
    try:
        with transaction.atomic():
            user = get_user_model().objects.create_user(
                username=email,
                email=email,
                password=secret,
                first_name=first_name,
                last_name=last_name,
            )
            Profile.objects.create(user=user, is_admin=is_admin, must_change_password=True)
    except IntegrityError as exc:  # corrida: o mesmo e-mail criado ao mesmo tempo
        raise CodedAPIError(
            ErrorCode.USER_EMAIL_TAKEN, status_code=status.HTTP_409_CONFLICT
        ) from exc
    _log(actor, user, "create_user" + (":admin" if is_admin else ""))
    return CreatedUser(user=user, temporary_password=secret if generated else None)


def _lock_admin_profiles() -> list[Profile]:
    """Trava (até o fim da transação) os perfis de administrador, em ordem fixa.

    Duas ações simultâneas que tirariam o papel uma da outra se serializam aqui, e a segunda
    enxerga o resultado da primeira.
    """
    return list(
        Profile.objects.select_for_update(of=("self",))
        .select_related("user")
        .filter(is_admin=True)
        .order_by("pk")
    )


def _locked_target(pk: int):
    try:
        return get_user_model().objects.select_for_update(of=("self",)).get(pk=pk)
    except ObjectDoesNotExist as exc:
        raise NotFound() from exc


def update_user(actor, target_pk: int, changes: dict):
    """Aplica `name`/`is_active`/`is_admin` (só as chaves presentes em `changes`).

    409 `admin_self_action`: o administrador não desativa nem rebaixa a si mesmo.
    409 `last_admin`: o último administrador ativo nunca sai do papel nem é desativado.
    Esta segunda trava é a rede de segurança contra corrida (dois administradores se
    rebaixando ao mesmo tempo): o ator que passa pela permissão e pela regra anterior já é,
    por si só, outro administrador ativo.
    """
    with transaction.atomic():
        admins = _lock_admin_profiles()
        target = _locked_target(target_pk)
        profile, _ = Profile.objects.get_or_create(user=target)

        will_be_active = changes.get("is_active", target.is_active)
        will_be_admin = changes.get("is_admin", profile.is_admin)
        loses_access = (target.is_active and not will_be_active) or (
            profile.is_admin and not will_be_admin
        )
        if loses_access and actor.pk == target.pk:
            raise CodedAPIError(ErrorCode.ADMIN_SELF_ACTION, status_code=status.HTTP_409_CONFLICT)
        if loses_access and profile.is_admin and target.is_active:
            others = [p for p in admins if p.user_id != target.pk and p.user.is_active]
            if not others:
                raise CodedAPIError(ErrorCode.LAST_ADMIN, status_code=status.HTTP_409_CONFLICT)

        changed: list[str] = []
        if "name" in changes:
            target.first_name, target.last_name = split_name(changes["name"])
            changed.append("name")
        if "is_active" in changes and changes["is_active"] != target.is_active:
            target.is_active = changes["is_active"]
            changed.append("is_active")
        if "is_admin" in changes and changes["is_admin"] != profile.is_admin:
            profile.is_admin = changes["is_admin"]
            profile.save(update_fields=["is_admin", "updated_at"])
            changed.append("is_admin")
        target.save(update_fields=["first_name", "last_name", "is_active"])

    _log(actor, target, "update_user:" + (",".join(changed) or "nothing"))
    return target


def reset_password(actor, target_pk: int) -> tuple[object, str]:
    """Nova senha temporária (devolvida uma vez). O administrador não a usa em si mesmo.

    Trocar o hash da senha invalida os tokens JWT anteriores do usuário (CHECK_REVOKE_TOKEN).
    """
    with transaction.atomic():
        target = _locked_target(target_pk)
        if actor.pk == target.pk:
            raise CodedAPIError(ErrorCode.ADMIN_SELF_ACTION, status_code=status.HTTP_409_CONFLICT)
        profile, _ = Profile.objects.get_or_create(user=target)
        secret = generate_temporary_password()
        target.set_password(secret)
        target.save(update_fields=["password"])
        profile.must_change_password = True
        profile.save(update_fields=["must_change_password", "updated_at"])
    _log(actor, target, "reset_password")
    return target, secret
