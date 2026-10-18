#!/bin/sh
# Acompanha o log do Ollama mostrando só o que ajuda a diagnosticar lentidão:
#   - cada requisição (hora, status, duração, endpoint) -> identifica QUEM está usando o Ollama
#     (o myexams usa /api/chat; ferramentas "OpenAI-compatíveis" usam /v1/chat/completions);
#   - a velocidade de geração (tokens/s), amostrada a cada ~50 tokens para não inundar a tela;
#   - carga/descarga de modelos, reinícios e erros.
#
# Uso:  mise run ollama-log            (últimas 15 linhas úteis + acompanha em tempo real)
#       mise run ollama-log -- 40      (mostra as últimas 40 linhas úteis antes de acompanhar)
#       mise run ollama-log -- 0       (só acompanha daqui para frente)
#
# O log no nível padrão NÃO contém o texto dos laudos. Não ligue OLLAMA_DEBUG=1 com dados de
# saúde: o modo debug pode registrar o conteúdo dos prompts.
set -eu

HISTORY="${1:-15}"

# Homebrew (serviço) ou app de desktop; OLLAMA_LOG permite apontar para outro arquivo.
LOG="${OLLAMA_LOG:-}"
if [ -z "$LOG" ]; then
  for candidate in \
    "$(brew --prefix 2>/dev/null || echo /opt/homebrew)/var/log/ollama.log" \
    /opt/homebrew/var/log/ollama.log \
    /usr/local/var/log/ollama.log \
    "$HOME/.ollama/logs/server.log"; do
    if [ -f "$candidate" ]; then LOG="$candidate"; break; fi
  done
fi

if [ -z "$LOG" ] || [ ! -f "$LOG" ]; then
  echo "Não encontrei o log do Ollama. Informe o caminho: OLLAMA_LOG=/caminho/ollama.log mise run ollama-log" >&2
  exit 1
fi

echo "== Log: $LOG"
echo "== Modelos carregados agora:"
ollama ps 2>/dev/null | sed 's/^/   /' || echo "   (ollama não respondeu)"
echo "== Linhas úteis (Ctrl+C para sair)"
echo

# Filtro: requisições, velocidade, modelos, erros. Tudo o mais (cache de prompt, sampler...) é ruído.
# GET/HEAD são consultas de rotina (ollama ps/list, health check): ficam de fora.
NOISE='GIN.*\| (GET|HEAD) '
USEFUL='GIN|print_timing|eval time|load_tensors|model load|loaded runner|llama runner|offload|Listening|server config|level=(WARN|ERROR)|panic|fatal|out of memory|unloading|OOM'

# print_timing sai a cada ~4 tokens: mostra uma amostra a cada 50 tokens decodificados (e reinicia
# a contagem a cada nova tarefa).
SAMPLE='
/print_timing/ {
  task = ""; n = -1; tg = ""
  if (match($0, /task [0-9]+/)) task = substr($0, RSTART + 5, RLENGTH - 5)
  if (match($0, /n_decoded = *[0-9]+/)) { s = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", s); n = s + 0 }
  if (match($0, /tg = *[0-9.]+ t\/s/)) { tg = substr($0, RSTART, RLENGTH); sub(/tg = */, "", tg) }
  if (task != last_task) { last_task = task; last_n = -50 }
  if (n - last_n >= 50) { last_n = n; printf "   geração: tarefa %s, %d tokens, %s\n", task, n, tg; fflush() }
  next
}
{ print; fflush() }
'

# Histórico filtrado primeiro (sem amostragem de print_timing: só o último de cada tarefa).
if [ "$HISTORY" -gt 0 ] 2>/dev/null; then
  grep -a -E "$USEFUL" "$LOG" | grep -a -v -E "$NOISE|print_timing" | awk -v want="$HISTORY" '
    { lines[NR] = $0 }
    END {
      start = NR - want + 1; if (start < 1) start = 1
      for (i = start; i <= NR; i++) print lines[i]
    }' || true
  echo "   --- ao vivo ---"
fi

tail -n 0 -F "$LOG" | grep -a --line-buffered -E "$USEFUL" | grep -a --line-buffered -v -E "$NOISE" | awk "$SAMPLE"
