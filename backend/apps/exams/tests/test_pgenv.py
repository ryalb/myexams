"""`config.pgenv`: da connection string direta do `.env.cloud` para variáveis `PG*` do libpq.

É assim que o `cloud-backup` entrega as credenciais ao `pg_dump` dentro do Docker sem pôr a URL
nem a senha na linha de comando. Valores inventados, sem rede.
"""

import stat

import pytest
from django.core.exceptions import ImproperlyConfigured

from config import pgenv

PASSWORD = "senha-inventada-123"
DIRECT = f"postgresql://myexams_app:{PASSWORD}@ep-example-123456.sa-east-1.aws.neon.tech/neondb"
POOLED = (
    f"postgresql://myexams_app:{PASSWORD}@ep-example-123456-pooler.sa-east-1.aws.neon.tech/neondb"
)
QUERY = "?sslmode=require&channel_binding=require"


def test_a_direct_neon_url_becomes_libpq_environment_variables():
    env = pgenv.libpq_env({"DATABASE_MIGRATE_URL": DIRECT + QUERY})

    assert env == {
        "PGHOST": "ep-example-123456.sa-east-1.aws.neon.tech",
        "PGPORT": "5432",
        "PGUSER": "myexams_app",
        "PGPASSWORD": PASSWORD,
        "PGDATABASE": "neondb",
        "PGSSLMODE": "require",
        "PGCHANNELBINDING": "require",
        "PGCONNECT_TIMEOUT": "10",
    }


def test_url_parameters_without_a_libpq_variable_or_with_host_files_are_left_out():
    url = DIRECT + QUERY + "&sslcert=/x/c.pem&sslkey=/x/k.pem&keepalives=1&application_name=bk"

    env = pgenv.libpq_env({"DATABASE_MIGRATE_URL": url})

    assert env["PGAPPNAME"] == "bk"
    assert not {"sslcert", "PGSSLCERT", "PGSSLKEY", "PGKEEPALIVES"} & set(env)


def test_the_special_characters_of_the_password_are_decoded():
    url = "postgresql://u:p%40ss%3Dw%23@ep-example-123456.sa-east-1.aws.neon.tech/neondb"

    assert pgenv.libpq_env({"DATABASE_MIGRATE_URL": url})["PGPASSWORD"] == "p@ss=w#"


def test_the_url_is_required():
    for values in ({}, {"DATABASE_MIGRATE_URL": "  "}):
        with pytest.raises(ImproperlyConfigured, match="DATABASE_MIGRATE_URL"):
            pgenv.libpq_env(values)


def test_the_pooled_endpoint_is_refused_because_pg_dump_needs_a_direct_connection():
    with pytest.raises(ImproperlyConfigured, match="modo transação") as info:
        pgenv.libpq_env({"DATABASE_MIGRATE_URL": POOLED + QUERY})

    assert PASSWORD not in str(info.value)


def test_a_value_with_a_line_break_is_refused_because_it_would_break_the_env_file():
    url = "postgresql://u:a%0Ab@ep-example-123456.sa-east-1.aws.neon.tech/neondb"

    with pytest.raises(ImproperlyConfigured) as info:
        pgenv.libpq_env({"DATABASE_MIGRATE_URL": url})

    assert "a\nb" not in str(info.value)


def test_postgres_major_defaults_to_17_and_reads_the_setting():
    assert pgenv.pg_major({}) == 18
    assert pgenv.pg_major({"CLOUD_PG_MAJOR": " 16 "}) == 16


@pytest.mark.parametrize("value", ["abc", "13", "99", "17.2"])
def test_postgres_major_must_be_a_supported_version(value):
    with pytest.raises(ImproperlyConfigured, match="CLOUD_PG_MAJOR"):
        pgenv.pg_major({"CLOUD_PG_MAJOR": value})


def test_values_come_from_the_file_but_the_process_environment_wins(tmp_path):
    file = tmp_path / "cloud.env"
    file.write_text(f'DATABASE_MIGRATE_URL="{DIRECT}"\nCLOUD_PG_MAJOR=16\nOUTRA=1\n')

    values = pgenv.read_values(file, {"CLOUD_PG_MAJOR": "18", "PATH": "/bin"})

    assert values["DATABASE_MIGRATE_URL"] == DIRECT
    assert values["CLOUD_PG_MAJOR"] == "18"
    assert "OUTRA" not in values and "PATH" not in values


def test_a_missing_file_falls_back_to_the_process_environment(tmp_path):
    values = pgenv.read_values(tmp_path / "nao-existe.env", {"DATABASE_MIGRATE_URL": DIRECT})

    assert values == {"DATABASE_MIGRATE_URL": DIRECT}


def test_the_env_file_is_private_and_holds_one_assignment_per_line(tmp_path):
    out = tmp_path / "pg.env"
    out.write_text("lixo antigo\n")
    out.chmod(0o644)

    pgenv.write_env_file(out, {"PGHOST": "h", "PGPASSWORD": "p=q#r"})

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert out.read_text() == "PGHOST=h\nPGPASSWORD=p=q#r\n"


def write_cloud_file(tmp_path, url=DIRECT + QUERY, extra=""):
    file = tmp_path / "cloud.env"
    file.write_text(f"DATABASE_MIGRATE_URL={url}\n{extra}")
    return file


def test_cli_env_writes_the_file_and_prints_only_the_target(tmp_path, capsys):
    out = tmp_path / "pg.env"

    code = pgenv.main([str(write_cloud_file(tmp_path)), "env", str(out)])

    shown = capsys.readouterr()
    assert code == 0
    assert "ep-example-123456.sa-east-1.aws.neon.tech" in shown.out and "neondb" in shown.out
    assert PASSWORD not in shown.out + shown.err
    assert f"PGPASSWORD={PASSWORD}" in out.read_text()


def test_cli_major_prints_the_version(tmp_path, capsys):
    file = write_cloud_file(tmp_path, extra="CLOUD_PG_MAJOR=18\n")

    assert pgenv.main([str(file), "major"]) == 0
    assert capsys.readouterr().out.strip() == "18"


def test_cli_fails_with_a_message_and_no_secret_when_the_url_is_missing(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("DATABASE_MIGRATE_URL", raising=False)
    out = tmp_path / "pg.env"

    code = pgenv.main([str(tmp_path / "vazio.env"), "env", str(out)])

    assert code == 2
    assert "DATABASE_MIGRATE_URL" in capsys.readouterr().err
    assert not out.exists() or out.read_text() == ""


def test_cli_fails_on_a_pooled_url_without_writing_anything(tmp_path, capsys):
    out = tmp_path / "pg.env"

    code = pgenv.main([str(write_cloud_file(tmp_path, POOLED + QUERY)), "env", str(out)])

    shown = capsys.readouterr()
    assert code == 2 and PASSWORD not in shown.out + shown.err
    assert not out.exists() or out.read_text() == ""


def test_cli_usage_error_is_exit_code_2(capsys):
    assert pgenv.main([]) == 2
    assert pgenv.main(["a.env", "fazer-algo"]) == 2
