"""`reset_db`: as travas de segurança (apagar de verdade: testado à parte)."""

from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.exams.management.commands import reset_db


@pytest.mark.parametrize("host", ["db.interno.exemplo", "10.0.0.5", "exemplo.rds.amazonaws.com"])
def test_refuses_any_database_that_is_not_local(settings, monkeypatch, host):
    monkeypatch.setitem(settings.DATABASES["default"], "HOST", host)

    with mock.patch.object(reset_db, "drop_and_recreate_schema") as drop:
        with pytest.raises(CommandError, match="só roda em banco local"):
            call_command("reset_db", "--yes")

    drop.assert_not_called()


@pytest.mark.parametrize("host", ["", "localhost", "127.0.0.1", "::1", "LOCALHOST"])
def test_local_hosts_are_accepted(settings, monkeypatch, host):
    monkeypatch.setitem(settings.DATABASES["default"], "HOST", host)

    assert reset_db.assert_local_database()["HOST"] == host


def test_without_yes_a_wrong_database_name_cancels_before_dropping(settings):
    with (
        mock.patch.object(reset_db, "drop_and_recreate_schema") as drop,
        mock.patch("builtins.input", return_value="outro_banco"),
    ):
        with pytest.raises(CommandError, match="nome digitado não confere"):
            call_command("reset_db")

    drop.assert_not_called()


def test_confirmation_must_match_the_exact_database_name(settings):
    name = settings.DATABASES["default"]["NAME"]
    with (
        mock.patch.object(reset_db, "drop_and_recreate_schema") as drop,
        mock.patch.object(reset_db, "call_command") as run,
        mock.patch("builtins.input", return_value=f"  {name}  "),
    ):
        call_command("reset_db")

    drop.assert_called_once()
    assert [c.args[0] for c in run.call_args_list] == ["migrate", "sync_catalog"]


@pytest.mark.django_db
def test_keep_users_saves_accounts_privately_and_removes_the_file_after_restoring(
    settings, django_user_model, tmp_path
):
    django_user_model.objects.create_user("pessoa", "pessoa@example.com", "senha-de-teste-1")
    seen: dict = {}

    def fake_call_command(name, *args, **kwargs):
        if name == "loaddata":
            seen["file"] = args[0]
            seen["mode"] = oct(Path(args[0]).stat().st_mode & 0o777)
            seen["has_user"] = "pessoa@example.com" in Path(args[0]).read_text()
        elif name == "dumpdata":
            return call_command(name, *args, **kwargs)

    with (
        mock.patch.object(reset_db, "drop_and_recreate_schema"),
        mock.patch.object(reset_db, "connection"),
        mock.patch.object(reset_db, "call_command", side_effect=fake_call_command),
    ):
        call_command("reset_db", "--yes", "--keep-users")

    assert seen["mode"] == "0o600"
    assert seen["has_user"] is True
    assert not Path(seen["file"]).exists()


@pytest.mark.django_db
def test_failed_restore_keeps_the_file_and_says_where_it_is():
    def fake_call_command(name, *args, **kwargs):
        if name == "loaddata":
            raise RuntimeError("falha simulada")
        if name == "dumpdata":
            return call_command(name, *args, **kwargs)

    with (
        mock.patch.object(reset_db, "drop_and_recreate_schema"),
        mock.patch.object(reset_db, "connection"),
        mock.patch.object(reset_db, "call_command", side_effect=fake_call_command),
    ):
        with pytest.raises(CommandError, match="NÃO voltaram") as info:
            call_command("reset_db", "--yes", "--keep-users")

    path = Path(str(info.value).rsplit(": ", 1)[1])
    try:
        assert path.exists()
    finally:
        path.unlink(missing_ok=True)
