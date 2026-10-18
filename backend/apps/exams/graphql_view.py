"""View do GraphQL.

`graphene_django.GraphQLView` e uma view Django comum, e isso quebra de duas formas
quando o cliente e o frontend com JWT:

1. **CSRF.** Sem `csrf_exempt`, todo POST do navegador leva `403 CSRF cookie not
   set` -- a API nao usa cookie de sessao, entao nao ha token de CSRF para enviar.
2. **Autenticacao.** `request.user` vem do `AuthenticationMiddleware`, que le sessao
   e **ignora o header `Authorization`**. Sem tratar o Bearer aqui, todo resolver ve
   usuario anonimo e devolve vazio -- de forma silenciosa, com status 200.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.views.decorators.csrf import csrf_exempt
from graphene_django.views import GraphQLView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken

logger = logging.getLogger(__name__)


class JWTGraphQLView(GraphQLView):
    """GraphQL autenticado pelo mesmo Bearer token que a API REST usa."""

    @classmethod
    def as_view(cls, *args, **kwargs):
        # A API e autenticada por token no header, nao por cookie de sessao: nao
        # existe token de CSRF a enviar, e exigir um so bloqueia o cliente legitimo.
        return csrf_exempt(super().as_view(*args, **kwargs))

    def dispatch(self, request, *args, **kwargs):
        authenticator = JWTAuthentication()
        header = authenticator.get_header(request)

        # `get_raw_token` devolve None sem header ou com outro esquema (`Basic`, por
        # exemplo): nao e token invalido, e ausencia de Bearer. So em DEBUG se mantem o
        # usuario de sessao (GraphiQL logado no /admin); fora dele a requisicao e anonima.
        raw_token = authenticator.get_raw_token(header) if header is not None else None
        if raw_token is None:
            if not settings.DEBUG:
                request.user = AnonymousUser()
        else:
            try:
                request.user = authenticator.get_user(authenticator.get_validated_token(raw_token))
            except (InvalidToken, AuthenticationFailed) as exc:
                # 401 explicito. Cair para anonimo devolveria 200 com dados vazios, e o
                # cliente trataria token expirado como "usuario sem dados".
                logger.info("GraphQL: token invalido (%s)", exc)
                return self._unauthorized()

        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _unauthorized():
        from django.http import JsonResponse

        return JsonResponse({"errors": [{"message": "invalid_token"}]}, status=401)
