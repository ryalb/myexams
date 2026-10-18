set -e

# Sobe Postgres e Redis (Podman ou Docker, o que estiver disponível).
if command -v podman >/dev/null 2>&1; then
  podman compose up -d db redis
else
  docker compose up -d db redis
fi

cd backend
uv sync --group dev
cd ..

cd frontend
bun install
cd ..

sh scripts/ollama-pull.sh
