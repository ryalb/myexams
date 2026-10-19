import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

from config.envconf import (
    env_bool,
    env_int,
    env_list,
    resolve_database,
    resolve_infra,
)

# Tarefas `cloud-*` (EXAMS_CLOUD_MIGRATE=1) leem primeiro o `.env.cloud`, que NÃO é o `.env` do
# desenvolvimento: assim `DATABASE_SCHEMA` & cia. da nuvem nunca vazam para o servidor local.
# `load_dotenv` não sobrescreve variáveis já definidas, então o `.env.cloud` tem prioridade.
if os.environ.get("EXAMS_CLOUD_MIGRATE", "").strip().lower() in {"1", "true", "yes", "on"}:
    load_dotenv(
        os.environ.get("EXAMS_CLOUD_ENV_FILE")
        or Path(__file__).resolve().parent.parent / ".env.cloud"
    )
load_dotenv()

# `os.environ` já com o `.env` carregado. As funções de `config.envconf` recebem este mapa.
ENV = os.environ
# A Vercel define `VERCEL` no build e na execução. Só serve para trocar o padrão de poucos
# interruptores que, na plataforma, não podem ficar no valor do desenvolvimento.
ON_VERCEL = bool(ENV.get("VERCEL"))

BASE_DIR = Path(__file__).resolve().parent.parent


class FullWidthRichHandler(RichHandler):
    def __init__(self, *args, **kwargs):
        console = Console(width=200, force_terminal=True, stderr=True)
        super().__init__(*args, console=console, **kwargs)


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-dev-key")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"

# Fora do DEBUG, recusa subir com a chave de exemplo: ela assina os JWTs, então
# qualquer um forjaria um token. Exceção só para testes (pytest ou `manage.py test`):
# o CI roda com DJANGO_DEBUG=False e a chave do .env.example, e não serve tráfego.
_TESTING = "pytest" in sys.modules or sys.argv[1:2] == ["test"]
if not DEBUG and not _TESTING and SECRET_KEY in {"unsafe-dev-key", "change-me", ""}:
    raise ImproperlyConfigured(
        "Defina DJANGO_SECRET_KEY com um valor secreto e único (ou DJANGO_DEBUG=true em dev)."
    )

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

# O /admin/ do Django é opcional. Na nuvem, `DJANGO_ADMIN_ENABLED=false` tira a rota e o app
# (menos superfície, nenhum arquivo estático): o app tem a própria tela de usuários.
ADMIN_ENABLED = env_bool(ENV, "DJANGO_ADMIN_ENABLED", True)

INSTALLED_APPS = [
    *(["django.contrib.admin"] if ADMIN_ENABLED else []),
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.postgres",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "graphene_django",
    "channels",
    "apps.exams.apps.ExamsConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Multiusuário: o registro público nasce desligado. Ligado, qualquer pessoa com
# acesso à API cria uma conta.
ALLOW_PUBLIC_REGISTRATION = os.getenv("DJANGO_ALLOW_PUBLIC_REGISTRATION", "False").lower() == "true"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
# A Vercel usa o ASGI quando os dois estão definidos; a nuvem roda em WSGI (sem WebSocket).
if not ON_VERCEL:
    ASGI_APPLICATION = "config.asgi.application"

# Aplica migrations pendentes ao subir (config/boot.py). Padrão: ligado no desenvolvimento e
# desligado na Vercel, onde migrar é um passo manual (`mise run cloud-migrate`).
AUTO_MIGRATE = env_bool(ENV, "DJANGO_AUTO_MIGRATE", default=not ON_VERCEL)

# --- Banco ------------------------------------------------------------------------------------
# `DATABASE_URL` (postgresql://...) ou, sem ela, as `POSTGRES_*`. Detalhes em config/envconf.py.
_DATABASE = resolve_database(ENV)
DATABASES = {"default": _DATABASE.default}
# Esquema próprio (padrão vazio = `public`, o recomendado no Neon). Com valor, as tabelas, o
# `django_migrations` e o cache vão para ele; o Data API do Neon (desligado por padrão) só concede
# acesso ao `public`, então o esquema próprio é o plano B caso ele seja ligado um dia.
DATABASE_SCHEMA = _DATABASE.schema
# Só as tarefas `cloud-*` ligam isto: aponta o Django para `DATABASE_MIGRATE_URL`.
CLOUD_MIGRATE_MODE = _DATABASE.migrate_mode

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Mensagens nativas do Django/DRF (campo obrigatório, token inválido...) saem em
# pt-BR por causa disto. Texto customizado NÃO mora no backend: o backend devolve
# códigos (apps.exams.codes) e o frontend traduz via @nuxtjs/i18n.
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    **(
        {}
        if env_bool(ENV, "DRF_BROWSABLE_API", True)
        else {"DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",)}
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/hour",
        "user": "2000/hour",
        "login": "10/hour",
        # Cada upload dispara inferência local pesada.
        "upload": "30/hour",
    },
    "EXCEPTION_HANDLER": "apps.exams.errors.exception_handler",
    # Throttle por IP usa REMOTE_ADDR (0 proxies confiáveis), não o X-Forwarded-For,
    # que o cliente controla. Atrás de N proxies reversos confiáveis, defina
    # DRF_NUM_PROXIES=N (na Vercel: 1; ver docs/DEPLOY.md).
    "NUM_PROXIES": int(os.getenv("DRF_NUM_PROXIES", "0")),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    # `last_login` aparece na lista de usuários do administrador.
    "UPDATE_LAST_LOGIN": True,
    # Cada token leva um hash da senha: trocar ou redefinir a senha invalida, na hora, os
    # tokens já emitidos (REST, GraphQL e WebSocket). Não usa o app de blacklist. Usuário
    # inativo já é recusado por padrão (CHECK_USER_IS_ACTIVE) ao autenticar e ao renovar.
    "CHECK_REVOKE_TOKEN": True,
}

GRAPHENE = {"SCHEMA": "config.schema.schema"}

# Origens do frontend (lista separada por vírgulas, com esquema, sem barra final).
CORS_ALLOWED_ORIGINS = env_list(ENV, "DJANGO_CORS_ALLOWED_ORIGINS", ["http://localhost:3100"])

# --- Redis, Celery, canais e cache -----------------------------------------------------------
# Padrão: Redis para tudo, como no desenvolvimento. Modo "inline" da nuvem
# (`CELERY_TASK_ALWAYS_EAGER=true`): extração dentro da requisição, broker em memória e nenhuma
# conexão com Redis. Ver `resolve_infra` para as regras e `DJANGO_CACHE`/`DJANGO_CHANNEL_LAYER`.
_INFRA = resolve_infra(ENV)
REDIS_URL = _INFRA.redis_url
CACHES = _INFRA.caches
CHANNEL_LAYERS = _INFRA.channel_layers
CELERY_TASK_ALWAYS_EAGER = _INFRA.eager
CELERY_BROKER_URL = _INFRA.broker_url
CELERY_RESULT_BACKEND = _INFRA.result_backend
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
# A extração roda inferência local e pode demorar minutos por laudo.
CELERY_TASK_TIME_LIMIT = 60 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 55 * 60
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# O Redis reentrega uma tarefa não confirmada após `visibility_timeout` (default 1h).
# Tem de ser maior que o limite da tarefa, senão uma extração longa rodaria duas vezes.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 2 * 60 * 60}

# --- Laudos -----------------------------------------------------------------
# O PDF fica em diretório privado (fora de STATIC/MEDIA, nunca servido) só
# enquanto a extração roda; é apagado ao final, com sucesso ou falha.
EXAMS_UPLOAD_DIR = Path(os.getenv("EXAMS_UPLOAD_DIR", "tmp_uploads"))
if not EXAMS_UPLOAD_DIR.is_absolute():
    EXAMS_UPLOAD_DIR = BASE_DIR / EXAMS_UPLOAD_DIR
EXAMS_MAX_UPLOAD_BYTES = int(os.getenv("EXAMS_MAX_UPLOAD_MB", "20")) * 1024 * 1024
EXAMS_MAX_PAGES = int(os.getenv("EXAMS_MAX_PAGES", "60"))
# Liga/desliga o modelo (LLM) na extração. Desligado, só as regras determinísticas leem o laudo:
# o worker nem tenta falar com o Ollama; o que as regras não leem vira aviso `block_unreadable`
# (dá para digitar o resultado) e páginas sem texto (PDF escaneado) falham com `llm_disabled`.
EXTRACTION_USE_LLM = os.getenv("EXTRACTION_USE_LLM", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
# Tempo de parede máximo gasto em chamadas ao LLM por laudo. Estourado o teto, o que sobra
# do LLM é pulado (aviso `llm_budget_exceeded`); o que as regras leem não depende dele.
EXTRACTION_LLM_BUDGET_SECONDS = int(os.getenv("EXTRACTION_LLM_BUDGET_SECONDS", "240"))
# Uploads maiores que 2,5 MB seriam espelhados no /tmp do sistema (fora do diretório
# privado e sem apagar ao fim); o Django os guarda aqui. O diretório é criado 0700
# em `storage.upload_dir()`.
FILE_UPLOAD_TEMP_DIR = EXAMS_UPLOAD_DIR
if not _TESTING:
    # O Django não cria FILE_UPLOAD_TEMP_DIR; sem ele o primeiro upload grande falha. Em disco
    # somente leitura (Vercel: só o /tmp é gravável) não derruba a importação das settings:
    # `storage.upload_dir()` tenta de novo a cada upload e falha ali, com a causa clara.
    try:
        EXAMS_UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        EXAMS_UPLOAD_DIR.chmod(0o700)
    except OSError:
        pass
# Arquivos e laudos presos (worker morto, Redis fora) mais velhos que isto são limpos
# por `tasks.sweep_orphans` (ao subir o worker e por `manage.py sweep_uploads`).
EXAMS_ORPHAN_HOURS = float(os.getenv("EXAMS_ORPHAN_HOURS", "3"))

# --- Produção (tudo desligado por padrão; ver docs/DEPLOY.md) ---------------------------------
# Atrás de um proxy que reescreve X-Forwarded-Proto (Vercel), o Django precisa confiar nele
# para saber que a requisição era HTTPS. Ligue só se o proxy for confiável.
if env_bool(ENV, "DJANGO_SECURE_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool(ENV, "DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = env_int(ENV, "DJANGO_SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(ENV, "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool(ENV, "DJANGO_SECURE_HSTS_PRELOAD", False)
SESSION_COOKIE_SECURE = env_bool(ENV, "DJANGO_SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = env_bool(ENV, "DJANGO_CSRF_COOKIE_SECURE", False)
CSRF_TRUSTED_ORIGINS = env_list(ENV, "DJANGO_CSRF_TRUSTED_ORIGINS", [])

# Dado de saúde: os logs nunca devem carregar valores extraídos. Mantenha em INFO.
# Na Vercel o log é texto puro (o handler `rich` força cores ANSI e largura 200).
LOG_PLAIN = env_bool(ENV, "DJANGO_LOG_PLAIN", default=ON_VERCEL)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "rich": {"format": "%(message)s"},
        "plain": {"format": "%(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": (
            {"class": "logging.StreamHandler", "formatter": "plain"}
            if LOG_PLAIN
            else {"class": "config.settings.FullWidthRichHandler", "formatter": "rich"}
        ),
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "py.warnings": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}

logging.captureWarnings(True)
