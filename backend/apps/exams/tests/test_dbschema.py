"""Esquema próprio (`DATABASE_SCHEMA`) e `pg_trgm` com e sem ele, no banco de teste.

Tudo dentro da transação do teste (esquemas e extensões são transacionais no Postgres).
"""

import pytest
from django.contrib.postgres.operations import TrigramExtension
from django.db import connection

from apps.exams import dbschema

pytestmark = pytest.mark.django_db


class Editor:
    """O mínimo do schema editor que `TrigramExtension` usa."""

    def __init__(self):
        self.connection = connection

    def quote_name(self, name):
        return connection.ops.quote_name(name)

    def execute(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)


def trgm_schema():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
            "WHERE e.extname = 'pg_trgm'"
        )
        row = cursor.fetchone()
        return row[0] if row else None


def reinstall_trgm(search_path: str):
    """Roda a operação da migration 0002 com o `search_path` dado, numa instalação limpa."""
    with connection.cursor() as cursor:
        cursor.execute("DROP EXTENSION IF EXISTS pg_trgm")
        cursor.execute(f"SET LOCAL search_path TO {search_path}")
    TrigramExtension().database_forwards("exams", Editor(), None, None)


def similarity():
    with connection.cursor() as cursor:
        cursor.execute("SELECT similarity('hemoglobina', 'hemoglobin')")
        return cursor.fetchone()[0]


def test_pg_trgm_installs_in_public_and_resolves_on_a_stock_postgres():
    reinstall_trgm("public")

    assert trgm_schema() == "public"
    assert similarity() > 0.5


def test_pg_trgm_installs_in_the_dedicated_schema_and_resolves_through_the_search_path():
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA scratch_myexams")

    reinstall_trgm("scratch_myexams, public")

    assert trgm_schema() == "scratch_myexams"
    assert similarity() > 0.5


def test_pg_trgm_created_beforehand_in_public_still_resolves_with_a_dedicated_schema():
    # Caminho recomendado no Neon: o dono cria a extensão uma vez; o papel do app só a usa.
    reinstall_trgm("public")
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA scratch_myexams")
        cursor.execute("SET LOCAL search_path TO scratch_myexams, public")

    TrigramExtension().database_forwards("exams", Editor(), None, None)  # já existe: não mexe

    assert trgm_schema() == "public"
    assert similarity() > 0.5


def test_ensure_schema_creates_once_and_reports_it():
    assert dbschema.ensure_schema(connection, "scratch_ensure") is True
    assert dbschema.ensure_schema(connection, "scratch_ensure") is False
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'scratch_ensure'")
        assert cursor.fetchone()


def test_pre_migrate_hook_creates_the_configured_schema(settings):
    settings.DATABASE_SCHEMA = "scratch_premigrate"

    dbschema.ensure_schema_before_migrate(sender=None, using="default")

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'scratch_premigrate'")
        assert cursor.fetchone()


def test_pre_migrate_hook_does_nothing_without_a_dedicated_schema(settings):
    settings.DATABASE_SCHEMA = ""
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_namespace")
        before = cursor.fetchone()[0]

    dbschema.ensure_schema_before_migrate(sender=None, using="default")

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_namespace")
        assert cursor.fetchone()[0] == before
