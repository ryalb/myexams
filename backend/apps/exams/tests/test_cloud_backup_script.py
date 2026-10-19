"""`scripts/cloud-backup.sh` com um `docker` de mentira: sem rede, sem Docker, sem banco.

O stub registra os argumentos do `docker` (a URL e a senha NÃO podem aparecer neles) e o
conteúdo do `--env-file` que receberia. Valores inventados.
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "cloud-backup.sh"
PASSWORD = "senha-inventada-123"
HOST = "ep-example-123456.sa-east-1.aws.neon.tech"
POOLED_HOST = "ep-example-123456-pooler.sa-east-1.aws.neon.tech"

DOCKER_STUB = """#!/bin/sh
all="$*"
echo "$all" >> "$STUB_LOG"
envfile=""
while [ $# -gt 0 ]; do
  [ "$1" = "--env-file" ] && envfile="$2"
  shift
done
if [ -n "$envfile" ]; then
  echo "$envfile" >> "$STUB_ENVFILES"
  cat "$envfile" >> "$STUB_ENVLOG"
fi
case "$all" in
  *pg_dump*)
    [ -n "${STUB_DUMP_FAIL:-}" ] && { echo "pg_dump: erro de mentira" >&2; exit 1; }
    printf 'PGDMP-conteudo-sintetico'
    ;;
  *pg_restore*)
    cat > /dev/null
    [ -n "${STUB_RESTORE_FAIL:-}" ] && exit 1
    ;;
esac
exit 0
"""


@pytest.fixture
def sandbox(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_STUB)
    docker.chmod(0o755)
    return tmp_path


def cloud_file(
    sandbox, url=f"postgresql://myexams_app:{PASSWORD}@{HOST}/neondb?sslmode=require", extra=""
):
    file = sandbox / "cloud.env"
    file.write_text(f"DATABASE_MIGRATE_URL={url}\n{extra}")
    return file


def run(sandbox, env_file, **extra_env):
    env = {
        "PATH": f"{sandbox / 'bin'}:/usr/bin:/bin",
        "HOME": str(sandbox),
        "PYTHONPATH": str(ROOT / "backend"),
        "EXAMS_PYTHON": sys.executable,
        "EXAMS_CLOUD_ENV_FILE": str(env_file),
        "EXAMS_BACKUP_DIR": str(sandbox / "backups"),
        "STUB_LOG": str(sandbox / "docker.log"),
        "STUB_ENVLOG": str(sandbox / "envfile.log"),
        "STUB_ENVFILES": str(sandbox / "envfiles.list"),
        **extra_env,
    }
    return subprocess.run(
        ["sh", str(SCRIPT)], env=env, cwd=ROOT, capture_output=True, text=True, timeout=60
    )


def dumps(sandbox):
    folder = sandbox / "backups"
    return sorted(folder.glob("cloud-*.dump")) if folder.exists() else []


def docker_log(sandbox):
    log = sandbox / "docker.log"
    return log.read_text() if log.exists() else ""


def test_script_has_valid_shell_syntax():
    assert subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True).returncode == 0


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck não instalado")
def test_script_passes_shellcheck():
    result = subprocess.run(["shellcheck", "-s", "sh", str(SCRIPT)], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout


def test_refuses_without_the_migrate_url_and_never_calls_docker(sandbox):
    result = run(sandbox, sandbox / "nao-existe.env")

    assert result.returncode != 0
    assert "DATABASE_MIGRATE_URL" in result.stderr
    assert docker_log(sandbox) == "" and dumps(sandbox) == []


def test_refuses_the_pooled_endpoint_and_never_calls_docker(sandbox):
    url = f"postgresql://myexams_app:{PASSWORD}@{POOLED_HOST}/neondb?sslmode=require"

    result = run(sandbox, cloud_file(sandbox, url))

    assert result.returncode != 0
    assert PASSWORD not in result.stdout + result.stderr
    assert docker_log(sandbox) == "" and dumps(sandbox) == []


def test_dumps_to_a_private_file_through_docker_without_the_secret_on_the_command_line(sandbox):
    result = run(sandbox, cloud_file(sandbox))

    assert result.returncode == 0, result.stderr
    [dump] = dumps(sandbox)
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
    assert stat.S_IMODE((sandbox / "backups").stat().st_mode) == 0o700
    assert dump.read_bytes() == b"PGDMP-conteudo-sintetico"

    log = docker_log(sandbox)
    assert "postgres:18-alpine" in log and "pg_dump -Fc" in log and "pg_restore --list" in log
    assert PASSWORD not in log and HOST not in log and "neondb" not in log
    assert PASSWORD not in result.stdout + result.stderr
    # as credenciais viajam pelo --env-file (variáveis PG* do libpq)
    envfile = (sandbox / "envfile.log").read_text()
    assert f"PGPASSWORD={PASSWORD}" in envfile and f"PGHOST={HOST}" in envfile
    assert "PGDATABASE=neondb" in envfile
    # e o arquivo temporário com a senha não sobra
    for path in (sandbox / "envfiles.list").read_text().split():
        assert not Path(path).exists()


def test_the_postgres_major_follows_cloud_pg_major(sandbox):
    result = run(sandbox, cloud_file(sandbox, extra="CLOUD_PG_MAJOR=16\n"))

    assert result.returncode == 0, result.stderr
    assert "postgres:16-alpine" in docker_log(sandbox)


def test_extra_docker_arguments_are_passed_before_the_image(sandbox):
    result = run(sandbox, cloud_file(sandbox), CLOUD_BACKUP_DOCKER_ARGS="--network=rede-de-teste")

    assert result.returncode == 0, result.stderr
    assert "--network=rede-de-teste" in docker_log(sandbox).split("postgres:18-alpine")[0]


def test_a_failed_pg_dump_leaves_no_file_behind(sandbox):
    result = run(sandbox, cloud_file(sandbox), STUB_DUMP_FAIL="1")

    assert result.returncode != 0
    assert dumps(sandbox) == []
    assert PASSWORD not in result.stdout + result.stderr


def test_a_dump_that_pg_restore_cannot_read_is_deleted(sandbox):
    result = run(sandbox, cloud_file(sandbox), STUB_RESTORE_FAIL="1")

    assert result.returncode != 0
    assert "inválido" in result.stderr
    assert dumps(sandbox) == []


def test_refuses_when_docker_is_not_installed(sandbox):
    if shutil.which("docker", path="/usr/bin:/bin"):
        pytest.skip(reason="há um docker em /usr/bin ou /bin")
    (sandbox / "bin" / "docker").unlink()

    result = run(sandbox, cloud_file(sandbox))

    assert result.returncode != 0 and "docker" in result.stderr.lower()
    assert dumps(sandbox) == []


def test_default_destination_is_the_git_ignored_private_folder():
    text = SCRIPT.read_text()
    ignored = (ROOT / ".gitignore").read_text()

    assert "fixtures/private/db-backups" in text and "fixtures/private/" in ignored
    assert os.access(SCRIPT, os.R_OK)
