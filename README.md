# MyExams

Rastreador de resultados de exames médicos. Você envia o PDF de um laudo, um modelo
**local** (Ollama) lê o documento, extrai exame, valor, unidade e faixa de referência e
grava tudo no banco. Depois dá para listar os laudos enviados, ver os resultados de cada
um e acompanhar o histórico de cada exame em gráfico e tabela.

**Dado de saúde não sai da máquina.** A inferência é local, o PDF é apagado assim que a
extração termina e nenhum valor de exame vai para log.

Base técnica: `myfinances` (mesma stack e convenções). A arquitetura e as diferenças em
relação a ele estão em [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Stack

| Camada | Tecnologias |
|---|---|
| Backend | Python 3.12, Django 5.1, Django REST Framework, Graphene (GraphQL), Channels (WebSocket), Celery 5.4 |
| Extração | `pdfplumber` (texto do PDF), Ollama (modelo de texto `qwen2.5:14b`, modelo de visão `qwen2.5vl:7b`) |
| Dados | PostgreSQL 16 com `pg_trgm`, Redis (broker do Celery, Channels, cache e throttling) |
| Frontend | Nuxt 3 (SPA, `ssr: false`), PrimeVue 4, Tailwind, Pinia, Chart.js, `@nuxtjs/i18n` |
| Ferramentas | `mise` (tasks), `uv` (Python), `bun` (frontend), `ruff` + `ty`, ESLint, `prek` (pre-commit) |

## Estrutura

```text
myexams/
  backend/
    apps/exams/            # único app: models, views, GraphQL, tasks, WebSocket
      extraction/          # pipeline de extração (um módulo por etapa)
      tests/
    config/                # settings, urls, celery, asgi
  frontend/                # Nuxt 3 (pages, components, composables, locales)
  fixtures/private/        # laudos para avaliação local (ignorado pelo git)
  scripts/                 # setup.sh, ollama-pull.sh e ollama-log.sh
  docs/ARCHITECTURE.md
  docker-compose.yml       # só Postgres e Redis
  mise.toml
```

## Primeiros passos

Pré-requisitos: [`mise`](https://mise.jdx.dev), Docker ou Podman e Homebrew (o script de
modelos instala e sobe o Ollama via `brew`).

```bash
mise install && mise run setup      # sobe db e redis, instala dependências, baixa os modelos
cp backend/.env.example backend/.env
mise run backend-migrate            # cria as tabelas e semeia o catálogo de exames
```

### Criar um usuário

O registro público vem **desligado** (`DJANGO_ALLOW_PUBLIC_REGISTRATION=False`): o
endpoint `POST /api/v1/auth/register/` responde `404`. Crie a conta pela linha de comando.

A tela de login pede um **e-mail** e envia o valor como `username`, então o `username`
do usuário precisa ser o próprio e-mail (é assim que o registro público também cria
contas):

```bash
cd backend
uv run python manage.py createsuperuser \
  --username voce@exemplo.com --email voce@exemplo.com
```

Ou, para um usuário comum (sem acesso ao `/admin/`):

```bash
cd backend
uv run python manage.py shell -c "
from django.contrib.auth import get_user_model
get_user_model().objects.create_user(
    username='voce@exemplo.com', email='voce@exemplo.com',
    password='troque-esta-senha', first_name='Seu', last_name='Nome')"
```

Preencha nome e sobrenome: se o nome impresso no laudo não contiver todos os termos do
nome cadastrado, o laudo recebe o aviso `patient_mismatch`. Depois do primeiro login,
informe **sexo** e **data de nascimento** em `/profile`: elas escolhem a faixa de
referência aplicável (muitas faixas variam por sexo e idade).

### Rodar

```bash
mise run dev                        # API (Daphne) + worker Celery + Nuxt, em paralelo
```

| Serviço | Endereço |
|---|---|
| Web (Nuxt) | `http://localhost:3100` |
| API REST | `http://localhost:8100/api/v1/` |
| GraphQL / GraphiQL | `http://localhost:8100/graphql/` |
| Admin | `http://localhost:8100/admin/` |
| PostgreSQL | `localhost:5433` |
| Redis | `localhost:6380` |
| Ollama | `localhost:11434` |

As portas são próprias para **não colidir com o `myfinances`**, que usa 3000/8000/5432/6379.
As tasks `kill-port` só mexem nas portas do `myexams`.

O Daphne também aplica migrations pendentes ao subir (`config/boot.py`); o worker não.

### Por que o worker usa `--pool=solo`

O pool padrão do Celery (`prefork`, que cria processos filhos com `spawn`/`fork`) falhou
no macOS durante o desenvolvimento. Com `--pool=solo` o worker processa **uma extração
por vez**, no próprio processo. Isso combina com o gargalo real, que é o Ollama local:
duas extrações em paralelo só disputariam a mesma GPU/memória. Em contrapartida, um
laudo na fila espera o anterior terminar.

Referência de tempo na máquina de desenvolvimento: um laudo de **3 páginas leva cerca de
3 minutos** com `qwen2.5:14b`. Cada bloco de exame vira uma chamada ao modelo, e páginas
escaneadas somam uma chamada ao modelo de visão por página. O frontend acompanha o
andamento por WebSocket.

## Tasks (`mise run <task>`)

| Task | O que faz |
|---|---|
| `setup` | Sobe db/redis, instala dependências (backend + frontend) e baixa os modelos do Ollama |
| `dev` | Stack de desenvolvimento em paralelo (API, worker, frontend). Depende de `setup`, que roda de novo a cada `dev` |
| `backend-dev` | Django via Daphne (ASGI/WebSocket) na porta 8100 |
| `backend-worker` | Celery worker (`--pool=solo`) |
| `backend-migrate` | Aplica migrations |
| `backend-lint` | `ruff check --fix` |
| `backend-format` | `ruff format` |
| `backend-typecheck` | `ty check` |
| `backend-test` | `pytest` |
| `frontend-dev` | Nuxt dev server na porta 3100 |
| `frontend-build` | Build do frontend |
| `frontend-lint` | ESLint (inclui regras de i18n), confere os ícones embutidos e audita botões |
| `frontend-lint-fix` | Idem, com correções |
| `check` | `backend-lint` + `backend-typecheck` + `backend-test` + `frontend-lint` |
| `check-i18n` | Confere os códigos do backend contra `frontend/locales/pt-BR.json` |
| `extraction-eval` | Avalia a extração com o Ollama real sobre `fixtures/private/` (opt-in) |
| `ollama-pull` | Baixa os modelos do Ollama usados pela extração |
| `ollama-log` | Acompanha o log do Ollama só com o útil: requisições (quem está usando), tokens/s, carga de modelos e erros. `mise run ollama-log -- 40` mostra as últimas 40 linhas antes de acompanhar |
| `import-reports` | Importa PDFs de laudos em lote, offline (ver "Importar laudos antigos em lote") |
| `export-reports` | Passo 1 da carga única: PDFs -> JSON revisável, sem tocar no banco (ver "Carga única em duas etapas") |
| `import-json` | Passo 2 da carga única: carrega no banco o JSON revisado |
| `kill-port <porta>` | Mata processos numa porta |

Hooks de commit:

```bash
uv tool run prek install
uv tool run prek run --all-files
```

## Privacidade

Laudo é dado de saúde. As decisões abaixo existem para que ele não vaze:

- **Inferência só local.** `OllamaClient` recusa um `OLLAMA_URL` que não aponte para a
  própria máquina (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0` ou o host `ollama` do
  Docker) e a extração falha com `inference_not_local`. Só
  `AI_ALLOW_REMOTE_INFERENCE=true` libera, e só se o envio for intencional.
- **O PDF vive só durante a extração.** Fica em `EXAMS_UPLOAD_DIR` (`backend/tmp_uploads`,
  diretório `0700`, arquivo `0600`, fora de `STATIC`/`MEDIA`, ignorado pelo git), nunca é
  servido por nenhuma rota e é **apagado ao final da tarefa, com sucesso ou falha**. Ao
  excluir um laudo, o arquivo pendente também é apagado.
- **CPF e RG são removidos antes do texto chegar ao LLM ou ao banco.** `linearize.redact`
  troca CPF por `[CPF]`, o número depois do rótulo `RG` por `[RG]` e qualquer sequência de
  9 a 14 dígitos por `[ID]`. O texto já redigido fica em `ExamDocument.page_text` para
  permitir reprocessar sem o PDF. Nome do paciente e data de nascimento impressos no
  laudo **não** são removidos: o cabeçalho os usa para o aviso `patient_mismatch`.
- **Nenhum valor de exame em log.** Falhas inesperadas registram só o tipo e o traceback,
  `ExtractionRun.stats` guarda apenas contagens e o evento WebSocket leva só o id e o
  status do laudo. Mantenha os loggers em `INFO`.
- **Multiusuário.** Todo queryset de laudos e resultados filtra por `request.user`; o
  catálogo de exames (`Analyte`) é global. FKs graváveis são escopadas ao usuário.
- **Ícones embutidos.** O `@iconify/vue` normalmente busca ícones em `api.iconify.design`.
  Aqui a coleção usada é gerada em `frontend/assets/icons/mdi-subset.json` e registrada
  no plugin, sem chamada de rede. **Ao usar um ícone novo, rode `bun run icons`** (em
  `frontend/`); o `bun run lint` falha se o arquivo estiver desatualizado.
- **Limitação conhecida:** a barra lateral exibe o avatar do **Gravatar** do usuário
  (`django-gravatar2`). Isso faz o navegador requisitar `gravatar.com` com o hash do
  e-mail, uma chamada de terceiros que contradiz a regra acima.

## Pipeline de extração

Resumo (diagrama completo em [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)):

1. **Upload** (`POST /api/v1/exam-documents/`, `202`): valida tamanho, assinatura `%PDF-`,
   duplicidade por hash e número de páginas; grava o PDF temporário e enfileira
   `process_exam_document` no Celery.
2. **Leitura**: `pdfplumber` agrupa as palavras por linha e marca quebra de coluna com
   ` | `. Página com menos de 30 caracteres de texto é renderizada (200 DPI) e transcrita
   pelo modelo de visão (`OLLAMA_VISION_MODEL`).
3. **Limpeza e blocos**: cabeçalho e rodapé repetidos saem; o texto é dividido em blocos
   por exame (`Material:` ... `Liberado por`). Sem essas âncoras, cai para blocos por
   página.
4. **Regras primeiro, LLM de reserva**: bloco de um exame só (`NOME | VALOR UNIDADE`, ou
   `RÓTULO: | VALOR UNIDADE`) e **tabelas simples** (`NOME | VALOR | UNIDADE | REFERÊNCIA`,
   inclusive o hemograma com percentual + absoluto na mesma linha) são lidos **por regras**
   (`extraction/layouts.py`), sem chamar o modelo, e a referência impressa é copiada do bloco
   por código. Na dúvida, as regras devolvem o bloco inteiro ao LLM (nunca meia tabela). Só o
   que as regras não reconhecem (layouts diferentes, resultados qualitativos) vai ao LLM:
   blocos grandes são divididos em pedaços de até 12 linhas, cada um precedido pelo título
   do bloco. O modelo responde em **texto compacto**, uma linha `n|nome|valor|unidade|calc|
   referência` por grandeza, sem JSON Schema (gerar JSON restrito custava muito mais tokens),
   com temperatura 0, `num_ctx` explícito e teto `OLLAMA_NUM_PREDICT`. O prompt manda copiar
   valor, unidade e referência **como impressos**. As regras rodam antes do modelo e as
   chamadas ao modelo têm um **orçamento de tempo** por laudo
   (`EXTRACTION_LLM_BUDGET_SECONDS`, padrão 240 s): estourado o teto, as partes restantes são
   puladas e o laudo sai com os avisos `block_unreadable` (por página) e `llm_budget_exceeded`.
   O **cabeçalho** (paciente, nº do pedido, datas, solicitante) é lido primeiro por regex; o
   LLM só é consultado quando o regex acha menos de 3 dos 5 campos. `ExtractionRun.stats`
   guarda `rule_blocks` (blocos lidos por regras), `table_blocks` (dos quais, tabelas),
   `llm_calls` (chamadas ao modelo), `llm_skipped_units` (partes puladas pelo orçamento),
   `llm_seconds` (tempo gasto no modelo) e `header_by_llm` (se o cabeçalho precisou do LLM).
5. **Código normaliza**: número e comparador (`<0,5`), unidade e conversão para a unidade
   canônica, faixa de referência (por sexo e idade), sinalização (`low`/`normal`/`high`/
   `abnormal`) e resolução do exame (apelido exato, similaridade por trigramas
   `pg_trgm`, e por último o LLM restrito ao catálogo) são **determinísticos e testados**.
6. **Checagens**: o valor transcrito precisa existir literalmente no texto do bloco
   (`value_not_in_page` se não existir); valor ausente, exame sem mapeamento, número
   ambíguo (`1.251`), valor fora do intervalo plausível, unidade sem conversão e
   divergência com a marcação impressa viram avisos. Um resultado com aviso relevante
   fica `needs_review`: **nunca é descartado**.
7. **Anteriores**: linhas `Anteriores: dd/mm/aaaa - valor` impressas no laudo viram
   resultados com `origin=lab_previous`, herdando a unidade do resultado atual e sem
   duplicar datas que o usuário já tem.
8. **Persistência e limpeza**: grava em transação, apaga o PDF, registra um
   `ExtractionRun` (modelo, versão do prompt, contagens) e emite `document_event`.

**Reprocessar** (`POST /api/v1/exam-documents/{id}/reprocess/`) refaz a análise a partir
de `page_text`, sem o PDF. É recusado (`409`) se o laudo não tem texto salvo, está em
andamento ou já tem resultado confirmado/corrigido pelo usuário.

Se o usuário corrige um resultado (`PATCH /exam-results/{id}/`), o backend recalcula
número, canônico, faixa e sinalização sem LLM e, se ele trocou o exame, grava um apelido
permanente (`source=user`) para o próximo laudo.

## Modelo de dados

| Modelo | Papel |
|---|---|
| `Profile` | Sexo e nascimento do usuário; escolhem a faixa de referência aplicável |
| `Laboratory` | Laboratório emissor, único por nome normalizado (global) |
| `ExamDocument` | Um laudo enviado: hash, status, metadados do cabeçalho, `page_text`, erro (código + params) e avisos. Único por `(usuário, sha256)` e por `(usuário, laboratório, nº do pedido)` |
| `ExtractionRun` | Histórico das execuções: modelo, modelo de visão, versão do prompt, início/fim, erro e contagens |
| `Analyte` | Exame canônico (Glicose, Hemoglobina...) com categoria, tipo, unidade canônica e limites de plausibilidade. Global |
| `AnalyteAlias` | Nome impresso normalizado -> exame (`seed`, `llm` ou `user`) |
| `UnitConversion` | Fator de conversão por exame (ex.: `ng/mL` -> unidade canônica) |
| `ExamResult` | Um valor medido, com o texto impresso e o normalizado lado a lado |

Duas decisões que importam:

- **Impresso e normalizado lado a lado.** `value_raw`, `unit_raw`, `reference_raw` e
  `flag_printed` guardam o que o laudo diz; `value_numeric`, `comparator`, `unit`,
  `value_canonical`, `flag` guardam o que o código entendeu. Assim dá para auditar uma
  interpretação, reprocessar com regras melhores e mostrar ao usuário o original ao lado
  do tratado, sem depender de o modelo acertar de primeira.
- **A referência é por resultado, nunca por exame.** A mesma dosagem tem faixas diferentes
  conforme laboratório, método, sexo e idade. `ref_kind`, `ref_low`, `ref_high`,
  inclusividade e `ref_bands` (todas as faixas impressas) moram em `ExamResult`; o
  `Analyte` só traz a unidade canônica e os limites de plausibilidade.

O catálogo (~60 exames, apelidos e conversões) é semeado pela migration `0002` e pode ser
ressincronizado, sem apagar apelidos criados por usuários, com
`uv run python manage.py sync_catalog`.

## API

Prefixo `/api/v1/`. Tudo exige `Authorization: Bearer <access>`, exceto `health/`,
`auth/token/`, `auth/token/refresh/` e `auth/register/`. Coleções grandes (`exam-documents`,
`exam-results`) vêm paginadas (`{count, next, previous, results}`, 100 por página,
`?page_size=` até 500); `analytes` não é paginado.

| Método e rota | O que faz |
|---|---|
| `GET /health/` | Health check (fora do prefixo `/api/v1/`) |
| `POST auth/token/` | Login: `{username, password}` -> `{access, refresh}` (limite `login`: 10/h) |
| `POST auth/token/refresh/` | Renova o access token |
| `POST auth/register/` | Cria usuário. `404` com registro desligado |
| `GET auth/me/` | Usuário autenticado + perfil + URL do avatar |
| `GET`/`PUT profile/` | Perfil: `sex` (`male`/`female`) e `birth_date` |
| `GET overview/` | Último valor, variação e sparkline por exame (mesmo dado de `analytesOverview`) |
| `GET analytes/` | Catálogo de exames |
| `GET analytes/{slug}/` | Um exame |
| `GET analytes/{slug}/history/` | Série histórica do usuário para o exame (`analyte`, `chartable`, `points`) |
| `GET exam-documents/` | Lista de laudos. Filtros: `status`, `laboratory` (id), `collected_from`, `collected_to` (`AAAA-MM-DD`), `out_of_range=1` ou `true` |
| `POST exam-documents/` | Upload (`multipart/form-data`, campo `file`). `202`; limite `upload`: 30/h |
| `GET exam-documents/{id}/` | Detalhe do laudo, incluindo `page_text` (texto redigido, sem o PDF) |
| `DELETE exam-documents/{id}/` | Apaga o laudo, seus resultados e o arquivo temporário, se ainda existir |
| `GET exam-documents/{id}/results/` | Resultados do laudo |
| `GET exam-documents/{id}/runs/` | Execuções de extração |
| `POST exam-documents/{id}/confirm/` | Confirma todos os resultados (exceto os já corrigidos) e limpa os avisos do laudo |
| `POST exam-documents/{id}/reprocess/` | Reextrai a partir do texto salvo. `202`, ou `409` |
| `GET exam-results/` | Resultados do usuário. Filtros: `document` (id), `analyte` (slug), `review_status`, `flag`, `origin` |
| `GET`/`PATCH exam-results/{id}/` | Edição. Campos graváveis: `analyte`, `value_raw`, `unit_raw`, `reference_raw`, `collected_at`, `notes` e `review_status` (só `confirmed`) |

Erros do upload (todos `{code, params}`): `file_missing` (400), `file_too_large` (413),
`file_not_pdf` (415), `duplicate_document` (409, com `date`), `file_too_many_pages` (400),
`pdf_encrypted` e `pdf_unreadable` (400). Limites: `EXAMS_MAX_UPLOAD_MB` (20) e
`EXAMS_MAX_PAGES` (60).

**WebSocket:** `ws://localhost:8100/ws/documents/?token=<access>`. Cada mudança de status
chega como `{"type": "document_event", "status": "...", "document_id": N}`. Token inválido
fecha a conexão com o código `4001`.

**GraphQL** (`/graphql/`, mesmo Bearer; GraphiQL habilitado). Token inválido responde
`401`. Queries:

| Query | Retorno |
|---|---|
| `analytesOverview` | Lista com `slug`, `name`, `category`, `unit`, `kind`, `count`, `latestValue`, `latestText`, `latestAt`, `latestFlag`, `latestInterpretation`, `delta`, `deltaPct`, `sparkline` |
| `analyteHistory(slug)` | `analyte`, `chartable` e `points` (valor, valor impresso, comparador, unidade, faixa, sinalização, laboratório, origem, status de revisão) |
| `examDocuments(limit)` | Laudos do usuário (padrão 100, máximo 500) |
| `examDocument(id)` | Um laudo |

O frontend usa REST para laudos, resultados e perfil, e GraphQL para a visão geral e o
histórico por exame.

## i18n

Todo texto visível é pt-BR, com acentuação correta, e **o backend não produz texto
customizado**: ele devolve códigos e parâmetros.

- Erro de API: `{"code": "duplicate_document", "params": {"date": "2026-08-25"}}`
  (`apps/exams/errors.py`, códigos em `apps/exams/codes.py`).
- Aviso de revisão (por resultado ou do laudo): `{"code": "value_not_in_page", "params": {}}`.
- Enums (`TextChoices`) têm valores em inglês; o frontend traduz o valor.

O frontend usa `@nuxtjs/i18n` com um único locale (`pt-BR`, sem prefixo de rota) e todas
as mensagens em `frontend/locales/pt-BR.json`, nos grupos `errors`, `warnings`, `status`,
`runStatus`, `flag`, `reviewStatus`, `origin`, `category`, `sex`, `method`, entre outros.
Formatos de número e data ficam em `frontend/i18n.config.ts` (`$n(v, 'decimal')`,
`$d(v, 'short')`).

Código novo no backend exige chave nova no locale. `mise run check-i18n` (um teste do
pytest) falha se faltar tradução para um código ou se sobrar chave órfã em `errors` e
`warnings`. No frontend, o ESLint com `@intlify/eslint-plugin-vue-i18n` proíbe texto cru
nos templates (`no-raw-text`), chave inexistente ou sem uso e sintaxe de mensagem
inválida. Rotas da API e do frontend são em inglês; só o texto é pt-BR.

## Testes

```bash
mise run backend-test        # ou: cd backend && uv run pytest
```

O pytest precisa do Postgres (`pg_trgm`) rodando; Redis e Ollama **não**: os testes usam
canal em memória, cache local, Celery em modo `eager` e o `FakeLLMClient`
(`extraction/fakes.py`). Nada de mock de biblioteca. Os PDFs são **sintéticos**, gerados
com `reportlab` em `tests/conftest.py`: nunca use laudo real nos testes. Há testes de
números, unidades, faixas de referência, pipeline, API e paridade de i18n. O CI não tem
Ollama.

### Avaliação com o modelo real

```bash
mise run extraction-eval     # ou: cd backend && uv run python manage.py extraction_eval
```

Opt-in e fora do CI. Lê `fixtures/private/` (ignorado pelo git; o comando aceita
`--fixtures` para outra pasta), onde cada caso é um par `<nome>.pdf` +
`<nome>.expected.json`:

```json
{ "results": [ { "analyte": "glicose", "value": "103" } ] }
```

`analyte` é o `slug` do catálogo e `value` é o valor **como impresso**. O comando roda o
pipeline com o Ollama real, compara os pares (exame, valor) e imprime esperados,
extraídos, corretos, faltantes, precisão e revocação. Nada fica gravado: cada caso roda
numa transação revertida. Mantenha laudos reais só nessa pasta.

## Importar laudos antigos em lote

Para processar uma pasta inteira de PDFs sem abrir o navegador (sem Celery, sem Redis, sem WebSocket):

```bash
# de qualquer pasta (use caminhos absolutos ou ~; o comando roda dentro de backend/)
mise run import-reports -- ~/Laudos --email voce@email.com --recursive

# ou direto
cd backend && uv run python manage.py import_reports ~/Laudos --email voce@email.com
```

| Opção | O que faz |
|---|---|
| `--email` | Dono dos laudos (e-mail ou usuário). Opcional se só existe um usuário |
| `--recursive` | Entra nas subpastas (por padrão só a pasta indicada) |
| `--no-llm` | Só regras: nenhuma chamada ao modelo e o Ollama nem precisa estar rodando. Partes que precisariam do modelo ficam marcadas para revisão |
| `--dry-run` | Só lista o que seria importado, sem gravar nada |
| `--limit N` | Processa no máximo N arquivos (bom para testar com poucos) |
| `--report resumo.csv` | Grava um resumo (arquivo, status, contagens; nunca valores de exame) |

Garantias:

- **Os PDFs originais nunca são alterados nem apagados.** Cada um é copiado para o diretório privado temporário
  (o mesmo do upload pela web) e só a cópia é apagada ao fim da leitura.
- **Pode rodar de novo à vontade.** Arquivos já importados (mesmo conteúdo) e laudos de um pedido que já existe são
  pulados. `Ctrl+C` encerra depois do arquivo atual, e o que já foi importado fica salvo.
- A saída mostra só o nome do arquivo, o status e contagens. O Postgres precisa estar de pé (`podman compose up -d db`);
  o Redis não.
- Quase todo laudo sai das regras em cerca de um segundo. O modelo só entra em layouts desconhecidos, com o teto de
  `EXTRACTION_LLM_BUDGET_SECONDS` por laudo. Para um primeiro lote grande, `--no-llm` é o jeito mais rápido e
  previsível; depois reprocesse (pelo app) só os que ficaram marcados.
- Cada laudo aparece na lista de Laudos do app, com o status (`Concluído`, `Revisão necessária` ou `Falhou` e o motivo).

## Carga única em duas etapas (PDF -> JSON revisável -> banco)

Para carregar laudos antigos com uma revisão humana no meio, sem Ollama e sem deixar nada no banco até você aprovar:

```bash
# (use caminhos absolutos: as tarefas rodam dentro de backend/)
# 1) exporta: roda o pipeline só com regras e grava um JSON. O banco NÃO muda.
mise run export-reports -- ~/Laudos -o "$PWD/fixtures/private/old_reports.json" --recursive --email voce@email.com

# 2) abra o JSON, revise/edite (regras abaixo) e simule:
mise run import-json -- "$PWD/fixtures/private/old_reports.json" --email voce@email.com --dry-run

# 3) importa de verdade
mise run import-json -- "$PWD/fixtures/private/old_reports.json" --email voce@email.com
```

**`export_reports`** aceita `--recursive`, `--limit N`, `--email` (só escolhe de quem são o perfil e os apelidos usados no
processamento: sexo e nascimento escolhem a faixa de referência), `--include-text` (inclui o texto das páginas, já sem
CPF/RG, e permite reprocessar o laudo depois) e `--force` (sobrescrever o arquivo de saída). Cada PDF roda numa transação
sempre revertida, como um usuário descartável com cópia do seu perfil; por isso o resultado não depende do que já foi
importado. Os PDFs originais nunca são alterados (só uma cópia temporária, apagada em seguida) e o arquivo de saída sai
com permissão `0600`. Se ele ficar dentro de um repositório git e não estiver ignorado, o comando avisa.
**O JSON contém dados de saúde: guarde-o em pasta ignorada pelo git** (`fixtures/private/` já é). O terminal mostra só nomes
de arquivo, status e contagens, nunca valores.

**Formato** (`"format": "myexams-reports/1"`): `format`, `generated_at`, `contains_health_data: true`, `note` e
`documents`. Cada laudo traz `source_file` (só o nome), `sha256` do PDF, `status`, `error_code`, `warnings`, os campos do
cabeçalho que o app guarda (`laboratory`, `accession_number` = número do pedido, `ordering_physician`,
`patient_name_on_doc`, `patient_birth_date`, `collected_on`, `issued_at`...) e `results`. Cada resultado traz o impresso
(`value_raw`, `unit_raw`, `reference_raw`...), o normalizado (`value_numeric`, `unit`, `ref_low`/`ref_high`, `flag`...),
`analyte` (slug do catálogo ou `null`, com `name_raw` ao lado), `origin` (`current` ou `lab_previous`), `collected_at`,
`review_status`, `warnings`, `source_line` e `page`/`position`, mais `summary` (uma linha de leitura, como
`Hemoglobina 15,1 g/dL (13,3 a 16,5) normal`, ignorada na importação). A ordem é estável (nome do arquivo, página,
posição), então dois exports diferem só no que mudou. Laudos que falham (ex.: uma ressonância, `no_results_found`) aparecem
com status e erro e zero resultados, e **não são importados**.

**O que vale na importação** (resumo; os detalhes estão no cabeçalho de `apps/exams/reports_json.py`):

| Você faz | Resultado |
|---|---|
| Apaga a linha ou põe `"keep": false` (no resultado ou no laudo) | Não é importada |
| Edita `value_raw`, `unit_raw`, `reference_raw`, `collected_at` ou `analyte` (slug) | Os campos derivados (`value_numeric`, `comparator`, `unit`, `value_canonical`, `ref_*`, `flag`, `interpretation`) são **recalculados** com o mesmo código do app, e os avisos refeitos. O texto impresso manda; o que você digitar nos derivados é descartado |
| Edita só um campo derivado (ex.: `flag`, `value_numeric`) | Idem: a linha deixou de bater com o seu `fingerprint` e é recalculada a partir do impresso |
| Põe `"reviewed": true` | A linha entra **exatamente como escrita**, sem recalcular (você assume os números; os tipos ainda são validados) |
| Põe `"review_status": "accepted"` | Fica "Confirmado", mesmo se a linha também foi editada. Com `auto`/`needs_review`: linha intacta mantém; linha recalculada vira "Corrigido" ou, se os avisos refeitos pedem, "Revisão necessária" |
| Adiciona uma linha à mão (só `name_raw`, `value_raw`, `unit_raw`, `reference_raw`, `analyte`, `collected_at`) | Sem `fingerprint`: derivada do impresso |
| Edita campos do cabeçalho do laudo | Valem como escritos |
| Laudo com `status` `failed`, `"keep": false` ou nenhuma linha atual mantida | Pulado e contado, nunca criado |

O `status` do laudo importado é recalculado com a regra do app (`Revisão necessária` se sobra resultado a revisar ou há
aviso do laudo; senão `Concluído`). Não edite `fingerprint`; `summary` é só para leitura.

**`import_json`** cria `ExamDocument` sem PDF (`upload_path` vazio) mas com o `sha256` original, então o mesmo PDF não pode
ser enviado de novo, e `ExamResult` (inclusive o histórico `lab_previous`), **um laudo por transação**. Valida o arquivo
inteiro antes de gravar qualquer coisa: JSON malformado, outra versão do formato (`myexams-reports/2`...), campo de tipo
errado ou slug de exame que não existe no catálogo dão erro claro e **nada é gravado** (as mensagens citam só o campo e a
posição, nunca o valor). O catálogo é sincronizado antes (idempotente). `--dry-run` faz tudo dentro de uma transação
revertida e mostra as mesmas contagens, sem gravar nada (nem o catálogo).

**Rodar de novo é seguro.** Laudo com o mesmo `sha256` para o usuário, ou o mesmo número de pedido no mesmo laboratório, é
**pulado e contado**, nunca sobrescrito (a segunda execução pula tudo). Linhas `lab_previous` seguem a regra do pipeline: o
dia que você já tem é descartado e um resultado atual substitui o "anterior" semeado por outro laudo. O arquivo JSON nunca
é modificado pelo comando.

## Solução de problemas

| Sintoma | Causa e solução |
|---|---|
| Laudo falha com `model_not_found` | Modelo do Ollama não baixado. `mise run ollama-pull` (baixa `OLLAMA_CHAT_MODEL` e `OLLAMA_VISION_MODEL`) |
| Laudo falha com `inference_unavailable` | Ollama parado ou `OLLAMA_URL` errado. `brew services start ollama` e confira `curl http://localhost:11434/api/tags` |
| Laudo falha com `inference_not_local` | `OLLAMA_URL` aponta para fora da máquina. Corrija a URL (ou, se for intencional, `AI_ALLOW_REMOTE_INFERENCE=true`) |
| Laudo fica em "Enviado" ou "Extraindo" | O worker não está consumindo. Rode `mise run backend-worker` (ou `mise run dev`) e confira se o Redis responde em `localhost:6380` e se `REDIS_URL` é o mesmo na API e no worker |
| Porta em uso | `mise run kill-port 8100` (API) ou `3100` (web). O `myfinances` usa 3000/8000/5432/6379, portas que este projeto evita; se algo ainda conflitar, descubra com `lsof -i :<porta>` |
| Ícone aparece em branco ou o `lint` reclama de `mdi-subset.json` | Ícone novo sem regenerar a coleção. `cd frontend && bun run icons` |
| Laudo escaneado sai vazio ou lento | Páginas sem camada de texto passam pelo modelo de visão (`OLLAMA_VISION_MODEL`, padrão `qwen2.5vl:7b`). Baixe o modelo e saiba que é mais lento; o laudo recebe o aviso `page_read_by_vision` |
| Bloco longo vem truncado | `OLLAMA_NUM_CTX` baixo (o padrão do Ollama trunca em silêncio). O projeto usa 4096, suficiente para as chamadas pequenas (cada uma tem < 3k tokens); aumente só se o bloco for maior |
| Laudo falha com `inference_timeout` | O modelo ficou em silêncio por mais de `OLLAMA_STALL_SECONDS` (padrão 120; modelo ainda carregando ou máquina sobrecarregada, ou outro cliente usando o mesmo Ollama) ou uma chamada passou do teto `OLLAMA_TIMEOUT_SECONDS` (padrão 1800, por chamada). Reprocesse o laudo, aumente os limites ou use um modelo menor. Blocos que estouram o tempo são tentados de novo uma vez e, se falharem, o laudo sai com o aviso `block_unreadable` |
| Laudo sai com o aviso `llm_budget_exceeded` | As chamadas ao modelo somaram mais que `EXTRACTION_LLM_BUDGET_SECONDS` (padrão 240 s) e as partes restantes foram puladas (cada página afetada também recebe `block_unreadable`). O que as regras leram já está salvo. Reprocesse com o computador menos ocupado (nada mais usando o Ollama), aumente o limite no `.env` ou use um modelo menor |
| Extração muito lenta | A inferência local roda a poucos tokens/s (cerca de 4,5 tok/s com `qwen2.5:14b` num M3 Pro), então o custo é o número de tokens gerados. As regras já poupam o modelo nos exames de um resultado só e nas tabelas simples (hemograma); só layouts que elas não reconhecem passam por ele, dentro do orçamento de tempo. Para ganhar velocidade use um modelo menor (`OLLAMA_CHAT_MODEL=qwen2.5:7b`). **Não rode duas extrações, nem outro cliente, contra o mesmo Ollama ao mesmo tempo**: as requisições concorrentes disputam a GPU e todas ficam mais lentas (e estouram `inference_timeout`). O progresso do laudo conta blocos por regras e pedaços de tabela |
| Login recusado para um usuário recém-criado | O `username` precisa ser o e-mail digitado (ver [Criar um usuário](#criar-um-usuário)) |
| Reprocessar responde `409` | O laudo está em andamento, não tem texto salvo ou já tem resultado confirmado/corrigido |

### Onde ver o tempo de cada chamada ao modelo

Cada leitura grava uma linha por chamada ao modelo (duração, carga do modelo, tokens, tokens/s e desfecho `ok`/`timeout`/`error`), sem nenhum conteúdo do laudo. Veja na página do documento, em **Diagnóstico da leitura**, ou em `GET /api/v1/exam-documents/{id}/runs/` (`stats.llm_calls_detail` e os totais `llm_output_tokens`, `llm_avg_tokens_per_second`, `llm_load_seconds`).
Leitura: abaixo de ~5 tok/s a máquina está sob pressão de memória ou o modelo é grande demais; um `load_seconds` alto na primeira chamada é só o modelo sendo carregado (cai nas seguintes por causa do `OLLAMA_KEEP_ALIVE`). Leituras que falharam por timeout também guardam as chamadas já medidas.

## CI

GitHub Actions: `mise install` -> dependências -> `mise run check` -> `frontend-build` ->
`prek`. Postgres e Redis como serviços; sem Ollama.
