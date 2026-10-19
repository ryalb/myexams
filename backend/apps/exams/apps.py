from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import pre_migrate


class ExamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.exams"
    verbose_name = "Exames"

    def ready(self):
        from . import checks  # noqa: F401  (registra as checagens)
        from .dbschema import ensure_schema_before_migrate

        # Esquema próprio: criado antes de `migrate` escrever o `django_migrations`.
        if settings.DATABASE_SCHEMA:
            pre_migrate.connect(ensure_schema_before_migrate, dispatch_uid="exams.ensure_schema")
