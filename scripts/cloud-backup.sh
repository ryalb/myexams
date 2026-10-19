#!/bin/sh
# Backup do banco da NUVEM (Neon) em fixtures/private/db-backups/cloud-<data>.dump, pasta fora do
# git e só do dono: o dump contém dados de saúde.
#   mise run cloud-backup
#
# Usa a connection string DIRETA (DATABASE_MIGRATE_URL de backend/.env.cloud): o pg_dump usa SET,
# que o pooler em modo transação do Neon não guarda. Não há pg_dump na máquina: ele roda no Docker
# (postgres:<versão do projeto Neon>-alpine; CLOUD_PG_MAJOR no .env.cloud, padrão 18). As
# credenciais entram no contêiner como variáveis PG* de um --env-file temporário (0600, apagado
# ao sair): URL e senha NUNCA vão para a linha de comando (que aparece no `ps`).
#
# Restaurar: docs/DEPLOY.md, seção "Backup e restauração".
# Variáveis opcionais (testes e depuração): EXAMS_CLOUD_ENV_FILE, EXAMS_BACKUP_DIR, EXAMS_PYTHON,
# CLOUD_BACKUP_DOCKER_ARGS (argumentos extras do `docker run`, ex.: --network=...).
set -eu
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
env_file="${EXAMS_CLOUD_ENV_FILE:-$root/backend/.env.cloud}"
dir="${EXAMS_BACKUP_DIR:-$root/fixtures/private/db-backups}"
python="${EXAMS_PYTHON:-uv run python}"
docker_args="${CLOUD_BACKUP_DOCKER_ARGS:-}"

die() {
  echo "$1" >&2
  exit 1
}

pgenv() {
  # shellcheck disable=SC2086  # $python pode ser "uv run python"
  (cd "$root/backend" && $python -m config.pgenv "$env_file" "$@")
}

command -v docker > /dev/null 2>&1 || die "docker não encontrado: o pg_dump roda num contêiner."

major="$(pgenv major)" || die "CLOUD_PG_MAJOR inválido."

pgvars="$(mktemp)"
file=""
cleanup() {
  rm -f "$pgvars"
  if [ -n "$file" ]; then
    rm -f "$file"
  fi
}
trap cleanup EXIT INT TERM

# Valida a URL (tem de ser a direta) e grava as variáveis PG*; imprime só host, porta e banco.
pgenv env "$pgvars" || die "Backup cancelado: confira DATABASE_MIGRATE_URL em $env_file."

mkdir -p "$dir"
chmod 700 "$dir"
file="$dir/cloud-$(date +%Y%m%d-%H%M%S).dump"
image="postgres:${major}-alpine"

# shellcheck disable=SC2086  # $docker_args é uma lista de opções
if ! docker run --rm -i $docker_args --env-file "$pgvars" "$image" \
  pg_dump -Fc --no-owner --no-acl --lock-wait-timeout=30s > "$file"; then
  die "pg_dump falhou; nenhum arquivo foi mantido."
fi
chmod 600 "$file"

# Confere que o arquivo é legível pelo pg_restore antes de dizer que deu certo.
# shellcheck disable=SC2086
if ! docker run --rm -i $docker_args "$image" pg_restore --list < "$file" > /dev/null; then
  die "Backup inválido; arquivo removido."
fi

echo "Backup gravado em $file ($(wc -c < "$file" | tr -d ' ') bytes)"
file=""
