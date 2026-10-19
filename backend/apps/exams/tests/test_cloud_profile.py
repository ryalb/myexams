"""Perfil da nuvem (Vercel + Neon) e padrões de hoje, conferidos num processo à parte.

Cada caso importa `config.settings` num Python novo com um ambiente controlado (sem `.env`,
`PYTHON_DOTENV_DISABLED=1`) e nunca abre conexão com banco. Valores inventados.
"""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]

NEON_POOLED_HOST = "ep-example-123456-pooler.sa-east-1.aws.neon.tech"
NEON_POOLED_URL = (
    f"postgresql://myexams_app:senha-inventada-123@{NEON_POOLED_HOST}/neondb"
    "?sslmode=require&channel_binding=require"
)

PROBE = """
import json
import django
from django.conf import settings
from django.urls import Resolver404, resolve

django.setup()
try:
    resolve("/admin/")
    admin_route = True
except Resolver404:
    admin_route = False
print(json.dumps({
    "asgi": getattr(settings, "ASGI_APPLICATION", None),
    "auto_migrate": settings.AUTO_MIGRATE,
    "admin_installed": "django.contrib.admin" in settings.INSTALLED_APPS,
    "admin_route": admin_route,
    "cache": settings.CACHES["default"]["BACKEND"],
    "channels": settings.CHANNEL_LAYERS.get("default", {}).get("BACKEND"),
    "broker": settings.CELERY_BROKER_URL,
    "eager": settings.CELERY_TASK_ALWAYS_EAGER,
    "log_handler": settings.LOGGING["handlers"]["console"]["class"],
    "cors": settings.CORS_ALLOWED_ORIGINS,
    "renderers": settings.REST_FRAMEWORK.get("DEFAULT_RENDERER_CLASSES"),
    "proxy_header": getattr(settings, "SECURE_PROXY_SSL_HEADER", None),
    "db": {
        k: settings.DATABASES["default"].get(k)
        for k in ("ENGINE", "HOST", "PORT", "NAME", "CONN_MAX_AGE", "CONNECT_RETRIES")
    },
    "db_options": settings.DATABASES["default"]["OPTIONS"],
    "schema": settings.DATABASE_SCHEMA,
}))
"""


CHECK = (
    "import django; django.setup(); "
    "from django.core.management import call_command as c; c('check')"
)
CHECK_DEPLOY = CHECK.replace("c('check')", "c('check', deploy=True)")


def run_python(code: str, env: dict[str, str]):
    full_env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(BACKEND),
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PYTHON_DOTENV_DISABLED": "1",
        **env,
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        env=full_env,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


def probe(env: dict[str, str]) -> dict:
    result = run_python(PROBE, env)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def secret():
    return secrets.token_urlsafe(64)


def test_no_new_variables_means_todays_local_behaviour():
    state = probe({"DJANGO_DEBUG": "true"})

    assert state["asgi"] == "config.asgi.application"
    assert state["auto_migrate"] is True
    assert state["admin_installed"] is True and state["admin_route"] is True
    assert state["cache"].endswith("RedisCache")
    assert state["channels"].endswith("RedisChannelLayer")
    assert state["broker"] == "redis://localhost:6380/0" and state["eager"] is False
    assert state["log_handler"] == "config.settings.FullWidthRichHandler"
    assert state["cors"] == ["http://localhost:3100"]
    assert state["renderers"] is None and state["proxy_header"] is None
    assert state["db"] == {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "localhost",
        "PORT": "5433",
        "NAME": "myexams",
        "CONN_MAX_AGE": 0,
        "CONNECT_RETRIES": None,
    }
    assert state["db_options"] == {"sslmode": "prefer"} and state["schema"] == ""


def test_cloud_profile(secret):
    state = probe(
        {
            "VERCEL": "1",
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": secret,
            "DATABASE_URL": NEON_POOLED_URL,
            "DATABASE_SCHEMA": "myexams",
            "CELERY_TASK_ALWAYS_EAGER": "true",
            "DJANGO_CACHE": "db",
            "DJANGO_ADMIN_ENABLED": "false",
            "DRF_BROWSABLE_API": "false",
            "DJANGO_SECURE_PROXY_SSL_HEADER": "true",
            "DJANGO_CORS_ALLOWED_ORIGINS": "https://app.exemplo.test, https://x.exemplo.test",
            # disco somente leitura: não pode derrubar a importação das settings
            "EXAMS_UPLOAD_DIR": "/nonexistent-readonly/uploads",
        }
    )

    assert state["asgi"] is None  # a Vercel escolhe WSGI
    assert state["auto_migrate"] is False
    assert state["admin_installed"] is False and state["admin_route"] is False
    assert state["cache"].endswith("DatabaseCache")
    assert state["channels"].endswith("InMemoryChannelLayer")
    assert state["broker"] == "memory://localhost/" and state["eager"] is True
    assert state["log_handler"] == "logging.StreamHandler"
    assert state["cors"] == ["https://app.exemplo.test", "https://x.exemplo.test"]
    assert state["renderers"] == ["rest_framework.renderers.JSONRenderer"]
    assert state["proxy_header"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert state["db"]["CONN_MAX_AGE"] == 0 and state["db"]["HOST"] == NEON_POOLED_HOST
    assert state["db"]["ENGINE"] == "config.db_backend" and state["db"]["CONNECT_RETRIES"] == 1
    assert state["db_options"] == {
        "sslmode": "require",
        "channel_binding": "require",
        "connect_timeout": "10",
        "prepare_threshold": None,
    }
    assert state["schema"] == "myexams"


def test_auto_migrate_can_be_forced_either_way(secret):
    base = {"DJANGO_DEBUG": "true"}

    assert probe({**base, "VERCEL": "1", "DJANGO_AUTO_MIGRATE": "true"})["auto_migrate"] is True
    assert probe({**base, "DJANGO_AUTO_MIGRATE": "false"})["auto_migrate"] is False


@pytest.mark.parametrize("key", ["unsafe-dev-key", "change-me", ""])
def test_secret_key_guard_still_refuses_example_keys_outside_debug(key):
    result = run_python(PROBE, {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": key})

    assert result.returncode != 0 and "DJANGO_SECRET_KEY" in result.stderr


def test_check_deploy_has_nothing_left_with_the_cloud_knobs_on(secret):
    env = {
        "VERCEL": "1",
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": secret,
        "DJANGO_ALLOWED_HOSTS": "api.exemplo.test",
        "DATABASE_URL": NEON_POOLED_URL,
        "CELERY_TASK_ALWAYS_EAGER": "true",
        "DJANGO_CACHE": "db",
        "DJANGO_ADMIN_ENABLED": "false",
        "EXAMS_UPLOAD_DIR": "/tmp/myexams-check-uploads",
        "DJANGO_SECURE_PROXY_SSL_HEADER": "true",
        "DJANGO_SECURE_SSL_REDIRECT": "true",
        "DJANGO_SECURE_HSTS_SECONDS": "31536000",
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS": "true",
        "DJANGO_SECURE_HSTS_PRELOAD": "true",
        "DJANGO_SESSION_COOKIE_SECURE": "true",
        "DJANGO_CSRF_COOKIE_SECURE": "true",
    }
    code = CHECK_DEPLOY

    result = run_python(code, env)

    assert result.returncode == 0, result.stderr[-2000:]
    assert "no issues" in result.stdout


def test_check_deploy_flags_what_is_off_by_default(secret):
    env = {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": secret, "DJANGO_ALLOWED_HOSTS": "x.test"}
    code = CHECK_DEPLOY

    result = run_python(code, env)

    for check_id in ("security.W004", "security.W008", "security.W012", "security.W016"):
        assert check_id in result.stderr


def test_inline_mode_with_redis_defaults_warns_about_it():
    code = CHECK

    result = run_python(code, {"DJANGO_DEBUG": "true", "CELERY_TASK_ALWAYS_EAGER": "true"})

    assert "exams.W001" in result.stderr  # cache ainda em Redis
    assert "exams.W002" not in result.stderr  # canais em memória por padrão


def test_cloud_tasks_read_the_cloud_env_file_but_the_local_server_never_does(tmp_path):
    cloud_file = tmp_path / "cloud.env"
    cloud_file.write_text(
        "DATABASE_MIGRATE_URL=postgresql://u:p@db.exemplo.test:5432/nuvem\n"
        "DATABASE_SCHEMA=myexams\n"
    )
    base = {
        "DJANGO_DEBUG": "true",
        "EXAMS_CLOUD_ENV_FILE": str(cloud_file),
        "PYTHON_DOTENV_DISABLED": "0",
    }

    cloud = probe({**base, "EXAMS_CLOUD_MIGRATE": "1"})
    local = probe(base)

    assert cloud["db"]["NAME"] == "nuvem" and cloud["schema"] == "myexams"
    assert local["db"]["NAME"] != "nuvem" and local["schema"] == ""
