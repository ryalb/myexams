from rest_framework import permissions

from .accounts import is_app_admin


class IsAppAdmin(permissions.BasePermission):
    """Administrador do app: autenticado, ativo e com `Profile.is_admin`.

    Anônimo recebe 401 (o DRF converte quando há autenticador com cabeçalho `WWW-Authenticate`);
    autenticado sem o papel recebe 403. O papel é lido do banco a cada requisição, então revogar
    `is_admin` vale já na próxima chamada.
    """

    def has_permission(self, request, view) -> bool:
        return is_app_admin(request.user)
