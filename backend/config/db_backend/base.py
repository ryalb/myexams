"""Backend PostgreSQL do Django com uma nova tentativa ao ABRIR a conexão.

O plano gratuito do Neon suspende o compute depois de 5 minutos ociosos; a primeira conexão
depois disso o acorda (centenas de milissegundos, às vezes alguns segundos) e, de vez em quando,
a tentativa que o acordou falha com `OperationalError`. Aqui essa tentativa é repetida
`DATABASES["default"]["CONNECT_RETRIES"]` vezes, depois de uma pausa curta.

Seguro por construção: `get_new_connection` só é chamado quando NÃO há conexão aberta (o Django
abre uma nova em `connect()`, que `ensure_connection()` só chama sem conexão). Uma transação em
andamento tem conexão, então nunca é refeita. Um erro no meio de uma consulta continua subindo.

O log traz só o número da tentativa: o texto do erro pode ter usuário e host.
"""

import logging
import time

import psycopg
from django.db.backends.postgresql.base import DatabaseWrapper as PostgresDatabaseWrapper

logger = logging.getLogger("config.db_backend")

RETRY_DELAY_SECONDS = 1.0


class DatabaseWrapper(PostgresDatabaseWrapper):
    def get_new_connection(self, conn_params):
        retries = int(self.settings_dict.get("CONNECT_RETRIES") or 0)
        attempt = 0
        while True:
            try:
                return super().get_new_connection(conn_params)
            except psycopg.OperationalError:
                if attempt >= retries:
                    raise
                attempt += 1
                logger.warning(
                    "Conexão com o banco falhou (o compute pode estar acordando); "
                    "nova tentativa %d de %d.",
                    attempt,
                    retries,
                )
                time.sleep(RETRY_DELAY_SECONDS)
