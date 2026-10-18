set -e

if ! command -v ollama >/dev/null 2>&1; then
  echo "--- instalando ollama via homebrew ---"
  brew install ollama
fi

echo "--- garantindo que o serviço ollama está rodando ---"
brew services start ollama

until ollama list >/dev/null 2>&1; do sleep 1; done

pull_model_if_missing() {
  model="$1"
  case "$model" in
    *:*) tagged_model="$model" ;;
    *) tagged_model="$model:latest" ;;
  esac

  if ollama list | awk '{print $1}' | grep -qx "$tagged_model"; then
    echo "--- modelo $model já está instalado ---"
  else
    echo "--- baixando modelo $model ---"
    ollama pull "$model"
  fi
}

pull_model_if_missing "${OLLAMA_CHAT_MODEL:-qwen2.5:14b}"
# Modelo de visão: só usado para páginas sem camada de texto (escaneadas).
pull_model_if_missing "${OLLAMA_VISION_MODEL:-qwen2.5vl:7b}"
