"""Configuração lida do ambiente, em funções puras (recebem o `env`, não tocam em banco nem rede).

Fica separada do `settings.py` para ser testada sem subir o Django. Tudo aqui é opt-in: sem as
variáveis novas, o resultado é o mesmo de antes (Postgres local `POSTGRES_*`, Redis, Celery e
Channels como no desenvolvimento). Nenhuma mensagem de erro repete a URL do banco (tem senha).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
DEFAULT_REDIS_URL = "redis://localhost:6380/0"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})

POOLERS = ("direct", "transaction")
# Padrões do perfil "nuvem" (URL com host que não é da própria máquina). O primeiro acesso depois de
# o compute do Neon ser suspenso (5 minutos ociosos) leva de centenas de milissegundos a alguns
# segundos: tempo para conectar e uma nova tentativa só na abertura da conexão (config/db_backend).
CLOUD_CONNECT_TIMEOUT = 10
CLOUD_CONNECT_RETRIES = 1
STOCK_ENGINE = "django.db.backends.postgresql"
RETRYING_ENGINE = "config.db_backend"
# Parâmetros de query da URL que viram opções do libpq (o Neon manda `sslmode` e `channel_binding`).
# O resto é ignorado de propósito.
URL_OPTION_KEYS = frozenset(
    {
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "connect_timeout",
        "application_name",
        "options",
        "target_session_attrs",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
        "channel_binding",
        "sslnegotiation",
    }
)
# O nome entra em SQL (CREATE SCHEMA, search_path): só identificador simples em minúsculas.
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def env_bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ImproperlyConfigured(f"{name} deve ser true ou false.")


def env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} deve ser um número inteiro.") from exc


def env_count(env: Mapping[str, str], name: str, default: int) -> int:
    """Inteiro maior ou igual a zero (segundos, tentativas)."""
    value = env_int(env, name, default)
    if value < 0:
        raise ImproperlyConfigured(f"{name} deve ser um número inteiro maior ou igual a zero.")
    return value


def env_list(env: Mapping[str, str], name: str, default: list[str]) -> list[str]:
    raw = env.get(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Banco ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedDatabaseURL:
    name: str
    user: str
    password: str
    host: str
    port: str
    params: dict[str, str]


def parse_database_url(url: str) -> ParsedDatabaseURL:
    """Lê `postgresql://usuário:senha@host:porta/banco?sslmode=require` só com a stdlib.

    Usuário e senha são decodificados (`%40` -> `@`). Caracteres reservados na senha precisam vir
    codificados (`/`, `?`, `#`); o `@` solto também é tolerado, porque o host nunca tem `@`.
    Sem porta vale 5432; usuário, senha e host podem faltar (soquete Unix, `.pgpass`); sem nome
    do banco a URL é recusada. Erros nunca incluem a URL.
    """
    text = url.strip()
    if text.split("://", 1)[0].lower() not in {"postgres", "postgresql"} or "://" not in text:
        raise ImproperlyConfigured("DATABASE_URL deve começar com postgresql://")
    parts = urlsplit(text)
    userinfo, _, hostport = parts.netloc.rpartition("@")
    user, _, password = userinfo.partition(":")
    try:
        authority = urlsplit("//" + hostport)
        host = authority.hostname or ""
        port = authority.port
    except ValueError as exc:
        raise ImproperlyConfigured("DATABASE_URL tem host ou porta inválidos.") from exc
    name = unquote(parts.path.lstrip("/"))
    if not name:
        raise ImproperlyConfigured("DATABASE_URL precisa do nome do banco (/banco).")
    params = {
        key: value
        for key, value in parse_qsl(parts.query)
        if key in URL_OPTION_KEYS and value != ""
    }
    return ParsedDatabaseURL(
        name=name,
        user=unquote(user),
        password=unquote(password),
        host=host,
        port=str(port) if port is not None else "5432",
        params=params,
    )


def detect_pooler(host: str) -> str:
    """Modo do pooler, quando dá para saber só pelo endereço.

    No Neon o endpoint com pooler (PgBouncer em modo transação) é o mesmo da conexão direta com
    `-pooler` no fim do primeiro trecho do host: `ep-xxx-pooler.<região>.aws.neon.tech`. Qualquer
    outro endereço volta vazio (conexão direta ou desconhecida): `DATABASE_POOLER` decide.
    """
    first_label = host.lower().split(".", 1)[0]
    return "transaction" if first_label.endswith("-pooler") else ""


def validate_schema(raw: str) -> str:
    schema = raw.strip()
    if schema in {"", "public"}:
        return ""
    if not _SCHEMA_RE.fullmatch(schema):
        raise ImproperlyConfigured(
            "DATABASE_SCHEMA deve ser um identificador simples em minúsculas (a-z, 0-9, _)."
        )
    return schema


def search_path_for(schema: str) -> str:
    return f"{schema},public"


@dataclass(frozen=True)
class DatabaseConfig:
    default: dict
    schema: str
    pooler: str
    migrate_mode: bool


def resolve_database(env: Mapping[str, str]) -> DatabaseConfig:
    """Monta o `DATABASES["default"]`.

    `DATABASE_URL` vale quando existe; senão, as `POSTGRES_*` de sempre. Com
    `EXAMS_CLOUD_MIGRATE=1` (só as tarefas `cloud-*` do mise) a URL é `DATABASE_MIGRATE_URL`,
    a connection string DIRETA (sem `-pooler`): migrar por pooler de transação não é suportado.
    """
    migrate_mode = env_bool(env, "EXAMS_CLOUD_MIGRATE")
    url = env.get("DATABASE_MIGRATE_URL" if migrate_mode else "DATABASE_URL", "").strip()
    if migrate_mode and not url:
        raise ImproperlyConfigured("Defina DATABASE_MIGRATE_URL (a conexão direta, sem -pooler).")

    options: dict[str, object] = {}
    if url:
        parsed = parse_database_url(url)
        name, user, password = parsed.name, parsed.user, parsed.password
        host, port = parsed.host, parsed.port
        options.update(parsed.params)
        default_sslmode = env.get("POSTGRES_SSLMODE") or (
            "prefer" if host.lower() in LOCAL_HOSTS else "require"
        )
    else:
        name = env.get("POSTGRES_DB", "myexams")
        user = env.get("POSTGRES_USER", "myexams")
        password = env.get("POSTGRES_PASSWORD", "myexams")
        host = env.get("POSTGRES_HOST", "localhost")
        port = env.get("POSTGRES_PORT", "5433")
        default_sslmode = env.get("POSTGRES_SSLMODE", "prefer")
    options.setdefault("sslmode", default_sslmode)

    pooler_name = "DATABASE_MIGRATE_POOLER" if migrate_mode else "DATABASE_POOLER"
    pooler = env.get(pooler_name, "").strip().lower() or detect_pooler(host)
    if pooler and pooler not in POOLERS:
        raise ImproperlyConfigured(f"{pooler_name} deve ser direct ou transaction.")
    if migrate_mode and pooler == "transaction":
        raise ImproperlyConfigured(
            "DATABASE_MIGRATE_URL não pode ser o endpoint com pooler em modo transação "
            "(use a connection string direta, sem -pooler no host)."
        )

    schema = validate_schema(env.get("DATABASE_SCHEMA", ""))
    remote = bool(url) and host.lower() not in LOCAL_HOSTS
    # Pooler de transação (PgBouncer do Neon): sem cursores no servidor, sem prepared statements
    # no libpq e uma conexão por requisição. Os dois primeiros evitam erros de "prepared statement
    # does not exist"; o último, segurar conexão que o pooler ou o compute suspenso encerrou.
    config: dict[str, object] = {
        "ENGINE": STOCK_ENGINE,
        "NAME": name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": port,
        "OPTIONS": options,
        "CONN_MAX_AGE": env_int(env, "DATABASE_CONN_MAX_AGE", 0),
    }
    if pooler == "transaction":
        config["DISABLE_SERVER_SIDE_CURSORS"] = True
        config["CONN_MAX_AGE"] = 0
        options["prepare_threshold"] = None
    elif config["CONN_MAX_AGE"]:
        # Conexão reaproveitada pode ter sido encerrada com o compute suspenso (Neon).
        config["CONN_HEALTH_CHECKS"] = True
    # O PgBouncer do Neon recusa parâmetros de inicialização como `options=-c search_path=...`
    # ("unsupported startup parameter"): em modo transação o `search_path` vem do papel
    # (`ALTER ROLE ... SET search_path`, ver docs/DEPLOY.md).
    if schema and pooler != "transaction":
        existing = str(options.get("options", "")).strip()
        pg_option = f"-c search_path={search_path_for(schema)}"
        options["options"] = f"{existing} {pg_option}".strip()
    connect_timeout = env_count(
        env, "DATABASE_CONNECT_TIMEOUT", CLOUD_CONNECT_TIMEOUT if remote else 0
    )
    if connect_timeout:
        options.setdefault("connect_timeout", str(connect_timeout))
    retries = env_count(env, "DATABASE_CONNECT_RETRIES", CLOUD_CONNECT_RETRIES if remote else 0)
    if retries:
        config["ENGINE"] = RETRYING_ENGINE
        config["CONNECT_RETRIES"] = retries
    return DatabaseConfig(default=config, schema=schema, pooler=pooler, migrate_mode=migrate_mode)


# --- Celery, Channels e cache --------------------------------------------------------------

CHANNEL_MODES = ("redis", "memory", "none")
CACHE_MODES = ("redis", "db", "locmem")


@dataclass(frozen=True)
class InfraConfig:
    eager: bool
    redis_url: str
    broker_url: str
    result_backend: str | None
    channel_layers: dict
    caches: dict
    channel_mode: str
    cache_mode: str


def resolve_infra(env: Mapping[str, str]) -> InfraConfig:
    """Celery, camada de canais (WebSocket) e cache/throttling.

    `CELERY_TASK_ALWAYS_EAGER=true` é o modo "inline" da nuvem: a extração roda na própria
    requisição, sem broker. Nesse modo o broker é `memory://` (nunca abre conexão com Redis) e,
    se `REDIS_URL` não foi definido, a camada de canais é em memória. O cache continua Redis
    por padrão: na nuvem, defina `DJANGO_CACHE=db`.
    """
    eager = env_bool(env, "CELERY_TASK_ALWAYS_EAGER")
    redis_explicit = bool(env.get("REDIS_URL", "").strip())
    redis_url = env.get("REDIS_URL", "").strip() or DEFAULT_REDIS_URL

    channel_mode = env.get("DJANGO_CHANNEL_LAYER", "").strip().lower()
    if not channel_mode:
        channel_mode = "memory" if eager and not redis_explicit else "redis"
    if channel_mode not in CHANNEL_MODES:
        raise ImproperlyConfigured("DJANGO_CHANNEL_LAYER deve ser redis, memory ou none.")
    channel_layers: dict
    if channel_mode == "redis":
        channel_layers = {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [redis_url]},
            }
        }
    elif channel_mode == "memory":
        channel_layers = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    else:
        channel_layers = {}

    cache_mode = env.get("DJANGO_CACHE", "").strip().lower() or "redis"
    if cache_mode not in CACHE_MODES:
        raise ImproperlyConfigured("DJANGO_CACHE deve ser redis, db ou locmem.")
    if cache_mode == "db":
        caches = {
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": "django_cache",
                "OPTIONS": {"MAX_ENTRIES": 1000},
            }
        }
    elif cache_mode == "locmem":
        caches = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    else:
        caches = {
            "default": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": redis_url,
            }
        }

    return InfraConfig(
        eager=eager,
        redis_url=redis_url,
        broker_url="memory://localhost/" if eager else redis_url,
        result_backend=None if eager else redis_url,
        channel_layers=channel_layers,
        caches=caches,
        channel_mode=channel_mode,
        cache_mode=cache_mode,
    )
