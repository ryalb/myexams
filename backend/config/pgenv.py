"""Da connection string DIRETA do `.env.cloud` para variáveis `PG*` do libpq (`cloud-backup`).

    python -m config.pgenv ARQUIVO.env env SAIDA   # grava o --env-file do docker; imprime só o alvo
    python -m config.pgenv ARQUIVO.env major       # imprime a versão do cliente (CLOUD_PG_MAJOR)

O `pg_dump` roda dentro do Docker e recebe as credenciais por `--env-file`, nunca na linha de
comando (que aparece no `ps`). Usa as mesmas regras do app (`resolve_database` no modo de migração):
a URL tem de ser a direta, sem `-pooler`, porque o `pg_dump` usa `SET`, que o pooler em modo
transação não guarda. Nenhuma mensagem repete a URL ou a senha.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import dotenv_values

from config.envconf import resolve_database

KEYS = (
    "DATABASE_MIGRATE_URL",
    "DATABASE_MIGRATE_POOLER",
    "CLOUD_PG_MAJOR",
    "DATABASE_CONNECT_TIMEOUT",
)
DEFAULT_PG_MAJOR = 18
SUPPORTED_PG_MAJORS = range(14, 19)

# Parâmetro da URL -> variável do libpq. Fora daqui (sslcert, sslkey: caminhos da sua máquina, que
# não existem no contêiner; keepalives: sem variável própria) fica de fora.
OPTION_TO_ENV = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslnegotiation": "PGSSLNEGOTIATION",
    "channel_binding": "PGCHANNELBINDING",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
    "options": "PGOPTIONS",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def read_values(env_file: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Só as chaves que importam: o ambiente do processo vence o arquivo (como no `load_dotenv`)."""
    values: dict[str, str] = {}
    if env_file.is_file():
        values.update(
            {k: v for k, v in dotenv_values(env_file).items() if k in KEYS and v is not None}
        )
    values.update({k: environ[k] for k in KEYS if k in environ})
    return values


def libpq_env(values: Mapping[str, str]) -> dict[str, str]:
    config = resolve_database(
        {
            "EXAMS_CLOUD_MIGRATE": "1",
            **{k: v for k, v in values.items() if k != "CLOUD_PG_MAJOR"},
        }
    ).default
    env = {
        "PGHOST": str(config["HOST"]),
        "PGPORT": str(config["PORT"]),
        "PGUSER": str(config["USER"]),
        "PGPASSWORD": str(config["PASSWORD"]),
        "PGDATABASE": str(config["NAME"]),
    }
    options = config["OPTIONS"]
    assert isinstance(options, dict)
    for key, name in OPTION_TO_ENV.items():
        if options.get(key):
            env[name] = str(options[key])
    for name, value in env.items():
        if not value and name in ("PGHOST", "PGUSER", "PGDATABASE"):
            raise ImproperlyConfigured(f"DATABASE_MIGRATE_URL precisa ter {name[2:].lower()}.")
        if "\n" in value or "\r" in value or "\0" in value:
            raise ImproperlyConfigured(f"{name} tem um caractere de controle e foi recusado.")
    return env


def pg_major(values: Mapping[str, str]) -> int:
    raw = values.get("CLOUD_PG_MAJOR", "").strip() or str(DEFAULT_PG_MAJOR)
    if not raw.isdigit() or int(raw) not in SUPPORTED_PG_MAJORS:
        raise ImproperlyConfigured(
            "CLOUD_PG_MAJOR deve ser a versão principal do Postgres do projeto Neon "
            f"({SUPPORTED_PG_MAJORS.start} a {SUPPORTED_PG_MAJORS.stop - 1})."
        )
    return int(raw)


def write_env_file(path: Path, env: Mapping[str, str]) -> None:
    """`--env-file` do docker: uma atribuição por linha, sem aspas; arquivo só do dono."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            fd = -1
            handle.write("".join(f"{k}={v}\n" for k, v in env.items()))
    finally:
        if fd != -1:
            os.close(fd)


def main(argv: list[str]) -> int:
    usage = "uso: python -m config.pgenv ARQUIVO.env (major | env SAIDA)"
    if len(argv) < 2 or argv[1] not in ("major", "env") or (argv[1] == "env") != (len(argv) == 3):
        print(usage, file=sys.stderr)
        return 2
    values = read_values(Path(argv[0]), os.environ)
    try:
        if argv[1] == "major":
            print(pg_major(values))
            return 0
        env = libpq_env(values)
        write_env_file(Path(argv[2]), env)
    except ImproperlyConfigured as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 2
    print(f"Alvo: host={env['PGHOST']} porta={env['PGPORT']} banco={env['PGDATABASE']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
