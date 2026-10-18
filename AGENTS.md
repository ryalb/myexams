# MyExams AGENTS.md

Rastreia resultados de exames médicos: o usuário envia o PDF de um laudo e um modelo
local lê o documento, extrai exame, valor, unidade e referência e grava no banco.
Base técnica: `myfinances` (mesma stack e convenções).

## Toolchain
- **Task runner**: `mise.toml` é a fonte da verdade. Prefira `mise run <task>`.
- **Backend**: `uv` (Python 3.12, Django 5.1, DRF, Graphene, Celery 5.4, Redis, PostgreSQL 16 + `pg_trgm`).
- **Frontend**: `bun` (Nuxt 3.13, PrimeVue 4, Tailwind, Pinia, Apollo, `@nuxtjs/i18n`, Chart.js).
- **Lint/format/typecheck**: `ruff` + `ty` (backend; regras desligadas em `pyproject.toml`
  por falta de plugin Django), ESLint flat config com `@intlify/vue-i18n` (frontend).
- **Pre-commit**: `prek.toml`. Instale com `uv tool run prek install`.

### Python — somente `uv`
- Sempre `uv run ...` a partir de `backend/`. Nunca `python`, `pip`, `poetry` direto.
- Dependência nova: `uv add <pkg>`. **Só licenças permissivas** (MIT/BSD/Apache): não use
  PyMuPDF (AGPL) nem nada GPL/copyleft.

## Setup / comandos
```bash
mise install && mise run setup      # db 5433, redis 6380, deps, modelos do Ollama
cp backend/.env.example backend/.env
mise run dev                        # API (Daphne) + worker Celery + Nuxt
mise run check                      # lint + ty + pytest + eslint
mise run check-i18n                 # códigos do backend x frontend/locales/pt-BR.json
mise run extraction-eval            # opt-in: Ollama real sobre fixtures/private
```
Portas próprias para não colidir com o `myfinances` (que usa 3000/8000/5432/6379):
db 5433, redis 6380, API 8100, web 3100. As tasks `kill-port` só mexem nas portas do `myexams`.

## Arquitetura
- **App único** `backend/apps/exams`; pipeline em `apps/exams/extraction/` (um módulo por etapa).
- **Fluxo**: `POST /api/v1/exam-documents/` (multipart, 202) -> arquivo temporário privado ->
  Celery `process_exam_document` -> ingest (pdfplumber; página sem texto vai para o modelo de
  visão) -> remove cabeçalho/rodapé repetidos -> blocos (`Material:` ... `Liberado por`) ->
  regras determinísticas (`layouts.py`: 1 exame por bloco e tabelas simples, sem LLM) ou, nos
  blocos que as regras não reconhecem (nunca meia tabela), LLM com resposta curta em texto puro
  (`compact.py`, temperatura 0, sem gramática JSON: ~4,5 tok/s medidos com JSON restrito), com
  orçamento de tempo por laudo (`EXTRACTION_LLM_BUDGET_SECONDS`; estourado, o resto é pulado
  com `llm_budget_exceeded`); cabeçalho por regex, LLM só se achar < 3 campos ->
  normalização **em código** -> validação -> banco ->
  **PDF apagado** -> evento WebSocket `document_event`.
- **Layout "ficha"** (sem `Material:`; um exame por trecho, com `RESULTADO` / `VALORES DE
  REFERÊNCIA`): `blocks._split_ficha_blocks` (bloco = título ... `RECEBIDO/COLETADO EM`; páginas de
  `LAUDO EVOLUTIVO` são descartadas, nunca viram resultado) + `layouts.extract_ficha_block`
  (resultado único, linhas rotuladas e hemograma/leucograma). O texto impresso da referência vai
  inteiro para `reference_raw`, mas a faixa só é lida quando não é matriz por idade/sexo, prosa nem
  tem duas faixas "normais" para o paciente (`strict_reference` em `build_draft`). Bloco que as
  regras não leem sem dúvida inteiro vai ao LLM (nunca meia tabela).
- **Layout "Softl@b"** (cabeçalho `Pedido: <nº> <NOME>` / `Pag.: n de m`; `MATERIAL:SORO`
  colado): `blocks._split_softlab_blocks` (detectado por `Softl@b`, `DATA LIBERAÇÃO:` ou pontilhada
  longa; exame = título `NOME | VALOR UNIDADE` até a pontilhada, `DATA DA COLETA:` ou o próximo
  título; painel = título só com o nome, absorve as linhas de resultado até ser fechado) +
  `softlab.extract_softlab_block` (gramática de linha em `softlab_lines.py`). A referência impressa
  pertence à linha de resultado anterior (antes de qualquer linha: à primeira; depois de `X` e
  `X(%)`: a `X`; duas linhas esperando uma referência: o bloco vai ao LLM). Só vira faixa a
  referência simples (uma faixa, ou par homens/mulheres) e conferida contra o texto impresso
  (`simple_reference`); matrizes ficam só em `reference_raw`. "Resultados Anteriores" são por
  linha (`row["previous"]`). Linha em colunas com cara de resultado que não é lida derruba o bloco.
  Ponto decimal e ponto de milhar coexistem (`4.5`, `5.600 /mm3`): só `/µL` trata o ponto como
  milhar; fora disso `1.234` fica com `ambiguous_number_format`. Sem data de nascimento nem sexo no
  cabeçalho: valem os do perfil.
- **Laboratório do laudo** (`Laboratory` é global; a atribuição é por laudo): detectado sem LLM por
  `pipeline.laboratory_from_text` (rodapé Softl@b `LAB. <NOME> CRM-PJ <nº>` por extenso, senão o radical
  do domínio de e-mail/site, sem TLD e com >= 3 letras; o texto inteiro das páginas, sem
  `strip_boilerplate`). O usuário corrige em `PATCH /exam-documents/<id>/` (`laboratory_name`; vazio
  limpa; nunca renomeia a linha compartilhada; recusa `uploaded`/`extracting` e colisão de
  `(laboratório, pedido)` com códigos) e vê as sugestões em `GET /laboratories/` (só os dele).
- **O LLM só transcreve.** Números, unidades, faixas, sinalização e resolução do exame são
  determinísticos e testados (`numbers.py`, `units.py`, `reference.py`, `flags.py`, `analytes.py`).
- **Guardas de alucinação**: valor transcrito precisa existir no texto do bloco; faltando valor
  vira `needs_review`, nunca é descartado.
- **Guardar o impresso e o normalizado**: `*_raw` ao lado de `value_numeric`/`unit`/...
  A referência é por resultado (varia por laboratório, sexo, idade); nunca por exame.
- **"Anteriores:"** impressos no laudo viram resultados `origin=lab_previous`.
- O texto das páginas (sem CPF/RG) fica em `ExamDocument.page_text` e permite reprocessar.

## Convenções
- **Privacidade (dado de saúde)**: inferência só local (`assert_local_inference`; sobrescrever só
  com `AI_ALLOW_REMOTE_INFERENCE=true`). **Nunca logar valores de exame**, nem CPF/RG.
  O PDF não é servido em lugar nenhum e é apagado ao fim da extração.
- **Multiusuário**: todo queryset filtra por `request.user`; FKs graváveis escopadas
  (`UserScopedPrimaryKeyRelatedField`). O catálogo de exames (`Analyte`) é global.
- **Texto visível ao usuário em pt-BR, com acentuação correta.** O backend **não** produz
  texto customizado: devolve **códigos + params** (`apps/exams/codes.py`,
  `errors.CodedAPIError`) e o frontend traduz em `frontend/locales/pt-BR.json`. Código novo =
  chave nova; `check-i18n` falha se faltar. Mensagens nativas do Django/DRF já saem em pt-BR.
- **Enums**: `TextChoices` com valores em inglês e rótulos pt-BR. Preserve as classes.
- **Rotas do frontend e da API em inglês** (como o `finances`); só o texto é pt-BR.
- **Ícones**: `@iconify/vue`. Sem SVGs customizados.
- **Env**: backend lê `.env` via `python-dotenv`; frontend lê `NUXT_PUBLIC_*`.

## Frontend
- `layouts/default.vue` usa o provider de sidebar: chame `useSidebarProvider()` antes de
  `useSidebar()`. Páginas de auth usam `definePageMeta({ layout: false })`.
- Nada de texto cru nos templates (`@intlify/vue-i18n/no-raw-text`): use `$t(...)`.
- Upload usa `useApi().upload()` (FormData, sem `Content-Type` JSON).

## CI
GitHub Actions: `mise install` -> deps -> `mise run check` -> `frontend-build` -> `prek`.
Postgres e Redis como serviços; **sem Ollama no CI** (testes usam `FakeLLMClient`).
