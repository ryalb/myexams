"""API de contas: administração de usuários (`/api/v1/admin/users/`) e troca da própria senha.

Regras que valem para tudo aqui:
- O administrador do app (`Profile.is_admin`, permissão `IsAppAdmin`) gerencia CONTAS. Nenhuma
  resposta traz contagem ou conteúdo de exames, nem nada derivado deles.
- NÃO existe exclusão de conta: só desativação. Apagar o usuário apagaria os exames de alguém.
- Senhas temporárias saem uma única vez na resposta (`Cache-Control: no-store`), nunca são
  guardadas em claro nem entram em log.
- Trocar/redefinir a senha invalida os JWT anteriores do usuário (SIMPLE_JWT.CHECK_REVOKE_TOKEN).
  Desativar a conta também os recusa já na próxima requisição (JWTAuthentication confere
  `is_active` a cada chamada). Um WebSocket já aberto só é conferido na conexão.
"""

from django.contrib.auth import get_user_model
from django.utils.cache import add_never_cache_headers
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from . import accounts
from .codes import ErrorCode
from .errors import CodedAPIError
from .models import Profile
from .permissions import IsAppAdmin


class AdminUserSerializer(serializers.Serializer):
    """Conta vista pelo administrador. Só dados da conta: nada de exames."""

    id = serializers.IntegerField(read_only=True)
    email = serializers.EmailField(read_only=True)
    name = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    is_admin = serializers.SerializerMethodField()
    must_change_password = serializers.SerializerMethodField()
    date_joined = serializers.DateTimeField(read_only=True)
    last_login = serializers.DateTimeField(read_only=True)

    def get_name(self, user) -> str:
        return accounts.display_name(user)

    def get_is_admin(self, user) -> bool:
        profile = accounts.profile_of(user)
        return bool(profile and profile.is_admin)

    def get_must_change_password(self, user) -> bool:
        profile = accounts.profile_of(user)
        return bool(profile and profile.must_change_password)


class AdminUserCreateSerializer(serializers.Serializer):
    # 150 = limite do `username` do Django (o e-mail é usado como username).
    email = serializers.EmailField(max_length=150)
    name = serializers.CharField(
        required=False, allow_blank=True, max_length=accounts.NAME_MAX_LENGTH, default=""
    )
    is_admin = serializers.BooleanField(required=False, default=False)
    password = serializers.CharField(
        required=False, allow_blank=True, write_only=True, trim_whitespace=False, max_length=128
    )

    def validate_email(self, value):
        if accounts.email_is_taken(value):
            raise serializers.ValidationError(ErrorCode.USER_EMAIL_TAKEN.value)
        return value

    def validate(self, attrs):
        # Vazio = "gere uma senha temporária".
        password = attrs.pop("password", "") or None
        if password is not None:
            candidate = get_user_model()(username=attrs["email"], email=attrs["email"])
            candidate.first_name, candidate.last_name = accounts.split_name(attrs["name"])
            codes = accounts.password_error_codes(password, user=candidate)
            if codes:
                raise serializers.ValidationError({"password": codes})
        attrs["password"] = password
        return attrs


class AdminUserUpdateSerializer(serializers.Serializer):
    """Só estes três campos são graváveis; qualquer outro (e-mail, senha...) é ignorado."""

    name = serializers.CharField(
        required=False, allow_blank=True, max_length=accounts.NAME_MAX_LENGTH
    )
    is_active = serializers.BooleanField(required=False)
    is_admin = serializers.BooleanField(required=False)


def _users_with_profiles():
    return get_user_model().objects.select_related("exam_profile")


def _secret_response(user, temporary_password: str | None, *, created: bool) -> Response:
    """Conta + senha temporária (mostrada uma vez). Nunca vai para o cache."""
    response = Response(
        {**AdminUserSerializer(user).data, "temporary_password": temporary_password},
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
    add_never_cache_headers(response)
    return response


class AdminUserListCreateAPIView(APIView):
    permission_classes = [IsAppAdmin]

    def get(self, request):
        users = _users_with_profiles().order_by("date_joined", "id")
        return Response(AdminUserSerializer(users, many=True).data)

    def post(self, request):
        serializer = AdminUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        created = accounts.create_user(
            request.user,
            email=data["email"],
            name=data["name"],
            is_admin=data["is_admin"],
            password=data["password"],
        )
        user = _users_with_profiles().get(pk=created.user.pk)
        return _secret_response(user, created.temporary_password, created=True)


class AdminUserDetailAPIView(APIView):
    """Só PATCH: não há exclusão (desativar é o caminho) nem troca de e-mail/senha aqui."""

    permission_classes = [IsAppAdmin]

    def patch(self, request, pk: int):
        serializer = AdminUserUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        accounts.update_user(request.user, pk, dict(serializer.validated_data))
        return Response(AdminUserSerializer(_users_with_profiles().get(pk=pk)).data)


class AdminUserResetPasswordAPIView(APIView):
    permission_classes = [IsAppAdmin]

    def post(self, request, pk: int):
        _, secret = accounts.reset_password(request.user, pk)
        return _secret_response(_users_with_profiles().get(pk=pk), secret, created=False)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


class ChangePasswordAPIView(APIView):
    """Troca da própria senha. Devolve um par de tokens novo: os antigos deixam de valer."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"  # adivinhar a senha atual é força bruta como no login

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        current = serializer.validated_data["current_password"]
        new = serializer.validated_data["new_password"]

        if not user.check_password(current):
            raise CodedAPIError(ErrorCode.CURRENT_PASSWORD_INCORRECT)
        if new == current:
            raise serializers.ValidationError(
                {"new_password": [ErrorCode.PASSWORD_UNCHANGED.value]}
            )
        codes = accounts.password_error_codes(new, user=user)
        if codes:
            raise serializers.ValidationError({"new_password": codes})

        user.set_password(new)
        user.save(update_fields=["password"])
        profile, _ = Profile.objects.get_or_create(user=user)
        if profile.must_change_password:
            profile.must_change_password = False
            profile.save(update_fields=["must_change_password", "updated_at"])
        accounts.logger.info("senha trocada pelo próprio usuário: usuário=%s", user.pk)

        refresh = RefreshToken.for_user(user)
        response = Response({"access": str(refresh.access_token), "refresh": str(refresh)})
        add_never_cache_headers(response)
        return response
