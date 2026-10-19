"""`cloud_migrate`, a trava do `reset_db` para bancos remotos e o `boot` sem migração automática."""

from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.exams.management.commands import cloud_migrate, reset_db
from config import boot
from config.envconf import resolve_database

PASSWORD = "senha-inventada-123"
HOST = "ep-example-123456.sa-east-1.aws.neon.tech"
REMOTE_URL = f"postgresql://myexams_app:{PASSWORD}@{HOST}/neondb?sslmode=require"


@pytest.fixture
def migrate_mode(settings, monkeypatch):
    """Banco da nuvem de mentira: só o `settings`; nenhum comando de verdade roda."""
    result = resolve_database({"EXAMS_CLOUD_MIGRATE": "1", "DATABASE_MIGRATE_URL": REMOTE_URL})
    monkeypatch.setitem(settings.DATABASES, "default", result.default)
    settings.CLOUD_MIGRATE_MODE = True
    settings.DATABASE_SCHEMA = "myexams"
    return result


def test_refuses_outside_the_cloud_migrate_mode(settings):
    settings.CLOUD_MIGRATE_MODE = False

    with mock.patch.object(cloud_migrate, "call_command") as inner:
        with pytest.raises(CommandError, match="mise run cloud-migrate"):
            call_command("cloud_migrate", "--yes")

    inner.assert_not_called()


def test_without_yes_it_shows_the_target_but_never_the_password_and_migrates_nothing(migrate_mode):
    out = StringIO()
    with mock.patch.object(cloud_migrate, "call_command") as inner:
        with pytest.raises(CommandError) as info:
            call_command("cloud_migrate", stdout=out)

    inner.assert_not_called()
    shown = out.getvalue() + str(info.value)
    assert HOST in shown and "banco=neondb" in shown
    assert "esquema=myexams" in shown
    assert PASSWORD not in shown


def test_with_yes_it_migrates_and_then_creates_the_cache_table(migrate_mode, settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "django_cache",
        }
    }
    out = StringIO()

    with mock.patch.object(cloud_migrate, "call_command") as inner:
        call_command("cloud_migrate", "--yes", stdout=out)

    assert [c.args[0] for c in inner.call_args_list] == ["migrate", "createcachetable"]
    assert PASSWORD not in out.getvalue()


def test_cache_table_is_skipped_when_the_cache_is_not_the_database(migrate_mode, settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

    with mock.patch.object(cloud_migrate, "call_command") as inner:
        call_command("cloud_migrate", "--yes", stdout=StringIO())

    assert [c.args[0] for c in inner.call_args_list] == ["migrate"]


# --- reset_db nunca roda contra um banco remoto -------------------------------------------


def test_reset_db_refuses_a_remote_database_url(settings, monkeypatch):
    remote = resolve_database({"DATABASE_URL": REMOTE_URL}).default
    monkeypatch.setitem(settings.DATABASES, "default", remote)

    with mock.patch.object(reset_db, "drop_and_recreate_schema") as drop:
        with pytest.raises(CommandError, match="só roda em banco local"):
            call_command("reset_db", "--yes")

    drop.assert_not_called()


def test_reset_db_accepts_a_local_database_url(settings, monkeypatch):
    local = resolve_database({"DATABASE_URL": "postgresql://u:p@localhost:5433/scratch"}).default
    monkeypatch.setitem(settings.DATABASES, "default", local)

    assert reset_db.assert_local_database()["NAME"] == "scratch"


def test_reset_db_refuses_the_cloud_migrate_mode_even_on_a_local_host(settings, monkeypatch):
    local = resolve_database({"DATABASE_URL": "postgresql://u:p@localhost:5433/scratch"}).default
    monkeypatch.setitem(settings.DATABASES, "default", local)
    settings.CLOUD_MIGRATE_MODE = True

    with pytest.raises(CommandError, match="modo de migração da nuvem"):
        reset_db.assert_local_database()


def test_reset_db_drops_the_dedicated_schema_and_never_public(settings):
    settings.DATABASE_SCHEMA = "myexams"
    cursor = mock.MagicMock()
    with mock.patch.object(reset_db, "connection") as connection:
        connection.ops.quote_name = lambda name: f'"{name}"'
        connection.cursor.return_value.__enter__.return_value = cursor
        reset_db.drop_and_recreate_schema()

    statements = [c.args[0] for c in cursor.execute.call_args_list]
    assert statements == ['DROP SCHEMA "myexams" CASCADE', 'CREATE SCHEMA "myexams"']


# --- boot -----------------------------------------------------------------------------------


def test_boot_migrates_by_default_and_skips_when_auto_migrate_is_off(settings):
    with mock.patch.object(boot, "call_command") as inner:
        settings.AUTO_MIGRATE = True
        boot.run_pending_migrations()
        assert inner.call_count == 1

        settings.AUTO_MIGRATE = False
        boot.run_pending_migrations()
        assert inner.call_count == 1
