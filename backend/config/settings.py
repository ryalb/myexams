import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

load_dotenv()

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

INSTALLED_APPS = [
    "django.contrib.admin",
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
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "myexams"),
        "USER": os.getenv("POSTGRES_USER", "myexams"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "myexams"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5433"),
        "OPTIONS": {"sslmode": os.getenv("POSTGRES_SSLMODE", "prefer")},
    }
}

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
    # DRF_NUM_PROXIES=N (a documentar no .env.example).
    "NUM_PROXIES": int(os.getenv("DRF_NUM_PROXIES", "0")),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
}

GRAPHENE = {"SCHEMA": "config.schema.schema"}

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3100",
]

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
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

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    },
}

# --- Laudos -----------------------------------------------------------------
# O PDF fica em diretório privado (fora de STATIC/MEDIA, nunca servido) só
# enquanto a extração roda; é apagado ao final, com sucesso ou falha.
EXAMS_UPLOAD_DIR = Path(os.getenv("EXAMS_UPLOAD_DIR", "tmp_uploads"))
if not EXAMS_UPLOAD_DIR.is_absolute():
    EXAMS_UPLOAD_DIR = BASE_DIR / EXAMS_UPLOAD_DIR
EXAMS_MAX_UPLOAD_BYTES = int(os.getenv("EXAMS_MAX_UPLOAD_MB", "20")) * 1024 * 1024
EXAMS_MAX_PAGES = int(os.getenv("EXAMS_MAX_PAGES", "60"))
# Tempo de parede máximo gasto em chamadas ao LLM por laudo. Estourado o teto, o que sobra
# do LLM é pulado (aviso `llm_budget_exceeded`); o que as regras leem não depende dele.
EXTRACTION_LLM_BUDGET_SECONDS = int(os.getenv("EXTRACTION_LLM_BUDGET_SECONDS", "240"))
# Uploads maiores que 2,5 MB seriam espelhados no /tmp do sistema (fora do diretório
# privado e sem apagar ao fim); o Django os guarda aqui. O diretório é criado 0700
# em `storage.upload_dir()`.
FILE_UPLOAD_TEMP_DIR = EXAMS_UPLOAD_DIR
if not _TESTING:
    # O Django não cria FILE_UPLOAD_TEMP_DIR; sem ele o primeiro upload grande falha.
    EXAMS_UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    EXAMS_UPLOAD_DIR.chmod(0o700)
# Arquivos e laudos presos (worker morto, Redis fora) mais velhos que isto são limpos
# por `tasks.sweep_orphans` (ao subir o worker e por `manage.py sweep_uploads`).
EXAMS_ORPHAN_HOURS = float(os.getenv("EXAMS_ORPHAN_HOURS", "3"))

# Dado de saúde: os logs nunca devem carregar valores extraídos. Mantenha em INFO.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"rich": {"format": "%(message)s"}},
    "handlers": {
        "console": {"class": "config.settings.FullWidthRichHandler", "formatter": "rich"},
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "py.warnings": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}

logging.captureWarnings(True)
