"""Esquema próprio do Postgres (`DATABASE_SCHEMA`), opcional.

Por padrão as tabelas ficam no `public`, que é o recomendado no Neon (o Data API do Neon vem
desligado). Com `DATABASE_SCHEMA=myexams` elas vão para um esquema próprio: é o plano B caso o
Data API seja ligado um dia, porque o "Grant public schema access" dele só concede acesso ao
`public`. Detalhes em docs/DEPLOY.md.

Nada aqui lê ou imprime dado de exame.
"""

from django.conf import settings
from django.db import connections


def ensure_schema(connection, schema: str) -> bool:
    """Cria o esquema se ele não existir. Devolve `True` se criou.

    Confere no catálogo antes de criar: `CREATE SCHEMA IF NOT EXISTS` exige o privilégio CREATE no
    banco mesmo quando o esquema já existe, e o papel do app pode não tê-lo (o esquema foi criado
    à mão para ele, ver docs/DEPLOY.md).
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", [schema])
        if cursor.fetchone():
            return False
        cursor.execute(f"CREATE SCHEMA {connection.ops.quote_name(schema)}")
    return True


def ensure_schema_before_migrate(sender, using, **kwargs) -> None:
    """`pre_migrate`: as tabelas vão para o primeiro esquema do search_path, que precisa existir."""
    schema = settings.DATABASE_SCHEMA
    connection = connections[using]
    if schema and connection.vendor == "postgresql":
        ensure_schema(connection, schema)
