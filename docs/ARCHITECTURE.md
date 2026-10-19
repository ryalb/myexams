# Arquitetura do MyExams

Visão técnica do fluxo de dados e das decisões de projeto. Para subir o projeto, veja o
[`README.md`](../README.md). O desenho abaixo é o do desenvolvimento local; a variante de nuvem (Vercel + Neon, leitura
dentro da requisição, sem worker, Redis nem WebSocket) está em [`DEPLOY.md`](DEPLOY.md).

## Componentes

```mermaid
flowchart TD
    User["Usuário no navegador"]

    subgraph Front["Frontend: Nuxt 3 SPA, porta 3100"]
        Pages["Páginas: documents, results, analytes, profile"]
        I18n["@nuxtjs/i18n, locale pt-BR"]
        Charts["Chart.js"]
    end

    subgraph Back["Backend: Django via Daphne, porta 8100"]
        REST["API REST, DRF + JWT"]
        GQL["GraphQL, Graphene"]
        WS["WebSocket, Channels: /ws/documents/"]
        Storage["storage.py: PDF temporário privado"]
    end

    subgraph Worker["Worker Celery, pool solo"]
        Task["process_exam_document"]
        Pipeline["extraction/: pipeline em etapas"]
    end

    Ollama["Ollama local, porta 11434: modelo de texto e modelo de visão"]
    PG[("PostgreSQL 16 com pg_trgm, porta 5433")]
    Redis[("Redis, porta 6380: broker, channel layer, cache, throttling")]
    Disk[("tmp_uploads: PDF até o fim da extração")]

    User --> Pages
    Pages --> REST
    Pages --> GQL
    Pages <--> WS
    REST --> Storage
    Storage --> Disk
    REST --> PG
    GQL --> PG
    REST -->|"enfileira tarefa"| Redis
    Redis -->|"entrega tarefa"| Task
    Task --> Pipeline
    Pipeline -->|"lê e apaga"| Disk
    Pipeline -->|"transcrição, HTTP local"| Ollama
    Pipeline -->|"grava resultados"| PG
    Task -->|"document_event"| Redis
    Redis --> WS
```

Pontos que o diagrama resume:

- **O navegador só fala com a API.** O PDF vai por `multipart/form-data` para
  `POST /api/v1/exam-documents/`; a resposta é `202` e o resto acontece no worker.
- **O worker é o único processo que fala com o Ollama** (`OllamaClient`, que recusa hosts
  não locais).
- **O Redis cumpre quatro papéis**: broker do Celery, camada de canais do WebSocket, cache
  e contadores de throttling do DRF.
- **O estado do laudo** (`uploaded` -> `extracting` -> `completed`, `needs_review` ou
  `failed`) vai para o frontend por WebSocket (`document_event`); o frontend recarrega os
  dados quando recebe um status final.

## Pipeline de extração

```mermaid
flowchart TD
    A["POST /exam-documents/: valida tamanho, assinatura %PDF-, hash e páginas"] --> B["Grava PDF temporário e cria ExamDocument uploaded"]
    B --> C["Celery: process_exam_document"]
    C --> D["Cria ExtractionRun e muda o laudo para extracting"]
    D --> E["build_llm_client: recusa OLLAMA_URL não local"]
    E --> F["ingest: pdfplumber agrupa palavras em linhas e marca colunas com barra vertical"]
    F --> G{"Página tem texto? 30 caracteres ou mais"}
    G -->|"sim"| H["Linhas da página"]
    G -->|"não"| I["Renderiza 200 DPI e transcreve com o modelo de visão"]
    I --> H
    H --> J["redact: CPF, RG e sequências de 9 a 14 dígitos viram marcadores"]
    J --> K["Salva page_text no ExamDocument"]
    K --> L["strip_boilerplate: remove cabeçalho e rodapé repetidos"]
    L --> M["split_blocks: título, Material: ... Liberado por"]
    M --> N["Cabeçalho: regex primeiro, LLM extract_header só se achar menos de 3 campos"]
    M --> O["Para cada bloco: regras (layouts.py: 1 exame ou tabela simples) primeiro; o que elas não reconhecem vai ao LLM em texto compacto (compact.py), temperatura 0, dentro do orçamento de tempo"]
    O --> P["build_draft: normalização em código"]
    P --> P1["Unidade, número, comparador, conversão"]
    P --> P2["Exame: apelido, trigramas, LLM restrito ao catálogo"]
    P --> P3["Referência por sexo e idade, sinalização, interpretação"]
    P1 --> Q{"Valor existe no texto do bloco?"}
    P2 --> Q
    P3 --> Q
    Q -->|"não"| R["Aviso value_not_in_page"]
    Q -->|"sim"| S["Resultado automático"]
    R --> T["review_status = needs_review"]
    S --> U["Linhas Anteriores viram resultados lab_previous"]
    T --> U
    N --> V["persist: transação"]
    U --> V
    V --> W{"Há resultados atuais?"}
    W -->|"não"| X["Falha: no_results_found"]
    W -->|"sim"| Y["Laboratório, deduplica por nº do pedido, grava ExamResult"]
    Y --> Z{"Algum needs_review ou aviso do laudo?"}
    Z -->|"sim"| Z1["Laudo needs_review"]
    Z -->|"não"| Z2["Laudo completed"]
    X --> F2["Finaliza: apaga o PDF, fecha ExtractionRun"]
    Z1 --> F2
    Z2 --> F2
    F2 --> G2["Evento document_event pelo WebSocket"]
```

Reprocessar entra no fluxo em `L`: `process_document` pula `ingest` e reconstrói as
páginas a partir de `ExamDocument.page_text`, o que dispensa o PDF.

### Papel de cada etapa

| Etapa | Módulo | Determinística? |
|---|---|---|
| Leitura do PDF, colunas, redação de CPF/RG | `linearize.py` | Sim (a transcrição de páginas escaneadas usa o modelo de visão) |
| Remoção de cabeçalho/rodapé e blocos | `blocks.py` | Sim |
| Cabeçalho do laudo | `pipeline.py` (+ `llm.py`) | Regex primeiro; o LLM só entra se o regex achar menos de 3 dos 5 campos (e completa os vazios) |
| Linhas de resultado de blocos com 1 exame | `layouts.py` (regras) | Sem LLM; a referência é copiada pelo código |
| Linhas de resultado de tabelas simples (`NOME \| VALOR \| UNIDADE \| REFERÊNCIA`, hemograma com percentual + absoluto) | `layouts.py` (regras de tabela) | Sem LLM; na dúvida, o bloco inteiro vai ao LLM (nunca meia tabela) |
| Linhas de resultado dos demais blocos | `llm.py` + `compact.py` | **Só aqui o LLM decide o que ler** (resposta curta em texto), limitado por `EXTRACTION_LLM_BUDGET_SECONDS`: estourado o teto, o resto é pulado com `block_unreadable` (um por bloco: página + nome do exame) + `llm_budget_exceeded` |
| Números, comparadores e ponto/vírgula | `numbers.py` | Sim |
| Unidades e conversões | `units.py` | Sim |
| Faixas de referência (sexo, idade, rótulos) | `reference.py` | Sim |
| Sinalização `low`/`normal`/`high`/`abnormal` | `flags.py` | Sim |
| Resolução do exame | `analytes.py` | Apelido e trigramas sim, LLM só como último recurso e restrito ao catálogo |
| Checagens (aterramento, plausibilidade, nome do paciente) | `validate.py` | Sim |
| Recálculo após edição do usuário | `recompute.py` | Sim, sem LLM |

### Garantias

- **O LLM só transcreve.** O prompt exige copiar valor, unidade e referência como
  impressos, em texto. Nada é convertido, corrigido ou calculado pelo modelo.
- **Aterramento (grounding).** O valor devolvido pelo modelo precisa aparecer no texto do
  bloco; senão o resultado ganha `value_not_in_page` e vai para revisão. Valor vazio vira
  `value_missing`. Em nenhum caso a linha é descartada.
- **Sem exame mapeado, o resultado é salvo mesmo assim** (`unmapped_analyte`), com
  `analyte = NULL`, para o usuário atribuir um exame depois. Essa atribuição vira um
  apelido (`AliasSource.USER`).
- **Idempotência.** A tarefa pode rodar de novo: os resultados do laudo são substituídos
  dentro de uma transação, e resultados `lab_previous` já semeados por outro laudo para o
  mesmo exame e dia são substituídos pelo resultado atual.
- **PDF apagado no `finally`** da tarefa, com sucesso ou falha.

## Modelo de dados

```mermaid
erDiagram
    USER ||--o| PROFILE : tem
    USER ||--o{ EXAM_DOCUMENT : envia
    USER ||--o{ EXAM_RESULT : possui
    LABORATORY ||--o{ EXAM_DOCUMENT : emite
    EXAM_DOCUMENT ||--o{ EXTRACTION_RUN : registra
    EXAM_DOCUMENT ||--o{ EXAM_RESULT : contem
    ANALYTE ||--o{ EXAM_RESULT : classifica
    ANALYTE ||--o{ ANALYTE_ALIAS : tem
    ANALYTE ||--o{ UNIT_CONVERSION : tem
```

`Analyte`, `AnalyteAlias`, `UnitConversion` e `Laboratory` são **globais**; todo o resto
pertence a um usuário e é filtrado por ele em cada consulta.

## O que mudou em relação ao `myfinances`

| Tema | `myfinances` | `myexams` |
|---|---|---|
| Fonte de dados | Pluggy (Meu Pluggy), webhook, instituições, contas, cartões | **Removidos.** A fonte é o PDF enviado pelo usuário |
| Dados de mercado, assinaturas, metas, categorização | Presentes | **Removidos** (sem equivalente no domínio de exames) |
| Busca semântica | `pgvector` + embeddings + RAG (relatório de portfólio) | **Removidos.** Resolução do exame por **`pg_trgm`** (similaridade de trigramas) sobre apelidos, com o LLM só como último recurso |
| Papel do LLM | Gera relatório a partir de contexto recuperado | **Só transcreve** texto de laudo; números, unidades, faixas e sinalização são código |
| Arquivos | Não trata upload | **Upload de PDF**: armazenamento privado e temporário, hash, validação de tipo/tamanho/páginas, leitura com `pdfplumber`, modelo de visão para páginas escaneadas, PDF apagado ao fim |
| Tarefas periódicas | Celery beat (snapshots e reconciliação diárias) | **Sem beat.** Só a tarefa `process_exam_document` |
| Worker Celery | Pool padrão | `--pool=solo`: uma extração por vez, evita o pool prefork/spawn do macOS |
| Evento de WebSocket | `sync_event` | **`document_event`** (`status` e `document_id`, endpoint `ws/documents/`) |
| GraphQL | Presente | **Mantido** (visão geral e histórico por exame); laudos, resultados e perfil usam REST |
| Frontend: i18n | Texto direto nos componentes | **`@nuxtjs/i18n`** com `locales/pt-BR.json`, ESLint `@intlify` e backend devolvendo só códigos |
| Ícones | `@iconify/vue` sem coleção embutida (busca remota em tempo de execução) | Coleção `mdi` embutida (`bun run icons`), sem chamada de rede |
| Portas | API 8000, web 3000, db 5432, redis 6379 | **API 8100, web 3100, db 5433, redis 6380** |
| Postgres | Exige a extensão `pgvector` (dependência `pgvector` no backend) | `postgres:16-alpine` padrão: `pg_trgm` vem no contrib |
| Inferência | Ollama local (chat e embeddings) | Ollama local (modelo de texto e de visão), sem embeddings; mesma guarda `AI_ALLOW_REMOTE_INFERENCE` |
| `ty` | Regras padrão | Classes de regra desligadas em `backend/pyproject.toml` (`unresolved-attribute`, `invalid-assignment`, `invalid-argument-type` etc.), porque o `ty` não entende os campos do Django sem plugin. Nomes indefinidos e imports seguem checados |
| Privacidade | Dado financeiro local | Dado de saúde: redação de CPF/RG, PDF efêmero, nenhum valor em log, escopo por usuário em todo queryset |
| Registro público | Desligado por padrão | **Mantido** desligado por padrão (`DJANGO_ALLOW_PUBLIC_REGISTRATION=False`) |

## Decisões de projeto

- **App único** (`apps/exams`) com o pipeline em módulos pequenos por etapa, cada um com
  testes próprios. Cada etapa recebe e devolve dados simples, o que permite reprocessar
  sem o PDF.
- **Códigos em vez de texto no backend.** `ErrorCode`, `WarningCode` e os enums de status
  são o contrato com o frontend; `test_i18n_parity.py` mantém os dois lados sincronizados.
- **Autenticação por JWT** (`djangorestframework-simplejwt`, access de 60 minutos) em REST,
  GraphQL (`JWTGraphQLView`, que também isenta CSRF e devolve `401` para token inválido) e
  WebSocket (token na query string, recusa com código `4001`).
- **Throttling** por escopo: `login` (10/h) para token e registro, `upload` (30/h) porque
  cada envio dispara inferência pesada.
- **Limites do Celery**: `CELERY_TASK_TIME_LIMIT` de 60 minutos e
  `visibility_timeout` do Redis de 2 horas (maior que o limite da tarefa, para o Redis não
  reentregar uma extração longa que ainda está rodando).
