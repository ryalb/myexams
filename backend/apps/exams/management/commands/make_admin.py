"""Concede (ou retira) o papel de administrador do app a uma conta existente.

    manage.py make_admin pessoa@example.com            # concede
    manage.py make_admin pessoa@example.com --revoke   # retira

É o jeito de criar o PRIMEIRO administrador (depois disso, ele cria os outros pela tela
"Usuários"). Idempotente. Mexe só em `Profile.is_admin`: `is_staff`/`is_superuser` do Django
ficam como estão. Imprime apenas o e-mail e o papel resultante.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.exams.accounts import find_user_by_login
from apps.exams.models import Profile


class Command(BaseCommand):
    help = "Concede ou retira (--revoke) o papel de administrador do app."

    def add_arguments(self, parser):
        parser.add_argument("email", help="E-mail (ou nome de usuário) da conta.")
        parser.add_argument(
            "--revoke", action="store_true", help="Retira o papel de administrador."
        )

    def handle(self, *args, **options):
        user = find_user_by_login(options["email"])
        if user is None:
            raise CommandError(f"Usuário não encontrado: {options['email']}")
        profile, _ = Profile.objects.get_or_create(user=user)
        wanted = not options["revoke"]
        if profile.is_admin != wanted:
            profile.is_admin = wanted
            profile.save(update_fields=["is_admin", "updated_at"])
        self.stdout.write(
            f"{user.email or user.get_username()}: {'administrador' if wanted else 'membro'}"
        )
