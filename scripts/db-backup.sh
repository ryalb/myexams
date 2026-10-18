#!/bin/sh
# Backup do banco de desenvolvimento (formato custom do pg_dump) em fixtures/private/db-backups/,
# pasta fora do git e com permissão só do dono: o dump contém dados de saúde.
#   mise run db-backup
#   Restaurar: docker compose exec -T db pg_restore -U myexams -d myexams --clean --if-exists < ARQUIVO
set -eu
cd "$(dirname "$0")/.."

dir="fixtures/private/db-backups"
mkdir -p "$dir"
chmod 700 "$dir"
file="$dir/myexams-$(date +%Y%m%d-%H%M%S).dump"

docker compose exec -T db pg_dump -U myexams -Fc myexams > "$file"
chmod 600 "$file"

# Confere que o arquivo é legível pelo pg_restore antes de dizer que deu certo.
if ! docker compose exec -T db pg_restore --list < "$file" > /dev/null; then
  rm -f "$file"
  echo "Backup inválido; arquivo removido." >&2
  exit 1
fi
echo "Backup gravado em $file ($(wc -c < "$file" | tr -d ' ') bytes)"
