# Deploy: Vercel + Neon

> **Estado: preparado, não executado.** Nada foi publicado, nenhum recurso de nuvem foi criado e nenhuma
> conta foi usada. Este guia, o código e as tarefas do `mise` deixam tudo pronto para você seguir os passos
> abaixo, na ordem. O que só dá para confirmar com uma conta real está listado em
> [O que não foi verificado](#20-o-que-não-foi-verificado).

O MyExams é um app para uma pessoa e a família. A leitura dos laudos roda **só com regras** (`EXTRACTION_USE_LLM=false`),
em 1 a 2 segundos por laudo. Por isso a nuvem **não precisa** de worker Celery, Redis nem WebSocket: a leitura acontece
dentro da própria requisição de upload.

## 1. Arquitetura

```mermaid
flowchart LR
  U["Navegador"] -->|"HTTPS"| A["Vercel projeto A<br/>frontend/ (Nuxt SPA)"]
  U -->|"HTTPS, JWT, CORS"| B["Vercel projeto B<br/>backend/ (Django, WSGI)<br/>região gru1"]
  B -->|"endpoint com pooler<br/>(host -pooler, PgBouncer)"| N[("Neon Postgres 18<br/>aws-sa-east-1, só o banco")]
  D["Sua máquina<br/>mise run cloud-*"] -->|"conexão direta<br/>(sem -pooler)"| N
  D -.->|"pg_dump no Docker"| K["fixtures/private/db-backups"]
```

| Peça | O que é | O que **não** é usado |
|---|---|---|
| Neon | Só o Postgres gerenciado (`aws-sa-east-1`, São Paulo, Postgres 18) | Data API, Auth, Functions, Object Storage |
| Vercel projeto A | `frontend/`, Nuxt 3 em modo SPA (`ssr: false`) | SSR, funções próprias |
| Vercel projeto B | `backend/`, Django em **WSGI** como uma função Python | WebSocket, worker, Redis, `/admin/` |
| Sua máquina | Migrações, carga dos laudos antigos, **backup** (`cloud-backup`) | — |

Fluxo de um upload na nuvem: `POST /api/v1/exam-documents/` (multipart) -> PDF gravado em `/tmp` (0600) -> a extração roda
na mesma requisição (`CELERY_TASK_ALWAYS_EAGER=true`, sem broker) -> PDF apagado -> a resposta já traz o laudo
`completed`, `needs_review` ou `failed`. Se a função for cortada no meio, o laudo fica em "Extraindo" e a faxina
(`sweep_orphans`, a cada upload) o marca como falho depois de `EXAMS_ORPHAN_HOURS`.

## 2. O que a pesquisa encontrou (documentação oficial, lida em 18/10/2026)

Onde o plano original divergia da documentação, **vale a documentação**.

### Vercel

| Tema | O que a documentação diz | Fonte |
|---|---|---|
| Runtime Python | 3.12 (padrão), 3.13 e 3.14; versão por `requires-python`, `.python-version` ou `Pipfile.lock` | Using the Python Runtime with Vercel Functions; Set the Python version |
| Django | Detecta o `manage.py` (também em subpasta imediata) e lê `WSGI_APPLICATION` ou `ASGI_APPLICATION`. **Com os dois definidos, usa o ASGI.** `tool.vercel.entrypoint` aponta para outro módulo. Roda `collectstatic` sozinho se houver `STATIC_ROOT` | Deploy a Django app on Vercel |
| Dependências | `pyproject.toml` (com ou sem `uv.lock`), `requirements.txt` ou `Pipfile`; o `uv` é o instalador padrão | Python runtime; changelog "uv is now available for builds" |
| Tamanho do pacote | **500 MB** descompactado para Python (250 MB nos outros runtimes); "Large Functions" (beta) vai a 5 GB. Python não faz tree-shaking: use `excludeFiles` | Vercel Functions Limits |
| Duração máxima | Com Fluid compute: padrão **300 s**; Hobby máximo **300 s**; Pro até 800 s (1800 s em beta) | Vercel Functions Limits |
| Memória | Hobby 2 GB / 1 vCPU; Pro até 4 GB / 2 vCPU | Vercel Functions Limits |
| Corpo da requisição | **4,5 MB** (resposta também); acima disso, 413 `FUNCTION_PAYLOAD_TOO_LARGE` | Vercel Functions Limits |
| Disco | Somente leitura, exceto `/tmp` (até 500 MB de rascunho por instância) | KB "Migrate a Python API from Heroku to Vercel" |
| Regiões | Padrão `iad1`. `gru1` = São Paulo (`sa-east-1`). Hobby: uma região qualquer; Pro: até 5 | Configuring regions; Global network and regions |
| Variáveis de ambiente | 64 KB no total por deploy; tipos **Config** (legível) e **Secret** (só escrita); mudanças valem **só para deploys novos** | Environment variables |
| Monorepo | Um projeto por pasta, com **Root Directory** própria; deploys de projetos não afetados são pulados | Using Monorepos |
| Proteção de deploys | *Standard Protection* (todos os planos): protege tudo **menos** os domínios de produção. *All Deployments* também protege produção. Senha: só Pro (US$ 20/mês por projeto) | Deployment Protection |
| IP do cliente | A Vercel **sobrescreve** `X-Forwarded-For` (não repassa IPs externos), então `DRF_NUM_PROXIES=1` é seguro | Request headers |
| Uso | Hobby é só para uso **pessoal e não comercial** | KB "Migrate a Python API from Heroku to Vercel" |
| WebSocket | Existe em **beta** em todos os planos, mas fixa a conexão numa instância e o `InMemoryChannelLayer` não replica entre instâncias | Deploy a Django app on Vercel |
| Nuxt | `nuxt build` é o padrão (SSR ou SPA); `nuxt generate` gera estático; os cabeçalhos podem sair do `routeRules` | Nuxt on Vercel |
| Gerenciador de pacotes | Detecta `bun.lock` e usa `bun install` | Package Managers |

### Neon

| Tema | O que a documentação diz | Fonte |
|---|---|---|
| Duas strings | **Direta** (`ep-xxx.<região>.aws.neon.tech`) e **com pooler** (o mesmo host com `-pooler` no fim do ID do endpoint). O pooler é o **PgBouncer em modo transação** (`pool_mode=transaction`, até 10.000 clientes, `default_pool_size` = 90% do `max_connections`, `query_wait_timeout=120`). Serverless e "connection-per-request": pooler. Migrações, `pg_dump`, replicação lógica e tarefas administrativas: **direta** | Connection pooling |
| O que o pooler **não** suporta | `SET`/`RESET`, `LISTEN`/`NOTIFY`, `WITH HOLD CURSOR`, `PREPARE`/`DEALLOCATE` em SQL, tabelas temporárias com `PRESERVE`/`DELETE ROWS`, `LOAD`, advisory locks de sessão. **Parâmetros de inicialização** além de `client_encoding`, `datestyle`, `timezone`, `standard_conforming_strings` e `application_name` dão `unsupported startup parameter in options`: ou seja, `options=-c search_path=...` **não passa** pelo pooler. A saída documentada é `ALTER ROLE ... SET search_path ...` | Connection pooling; Connection errors |
| Prepared statements | O PgBouncer do Neon (>= 1.22, `max_prepared_statements=1000`) **suporta prepared statements no nível do protocolo**; só o `PREPARE`/`EXECUTE` em SQL não. O psycopg 3 usa o nível do protocolo, então `prepare_threshold=None` **não é obrigatório**. Mantivemos desligado de propósito: o app não ganha nada com isso e some uma classe de erro (a página de erros do Neon lista "prepared statement already exists" em conexões com pooler, no Prisma) | Connection pooling; Connection errors |
| Django | `psycopg` 3, `sslmode=require`, `DISABLE_SERVER_SIDE_CURSORS=True` e `CONN_HEALTH_CHECKS=True`. O compute ocioso fecha as conexões: use `CONN_MAX_AGE=0` **ou** um valor maior junto com `CONN_HEALTH_CHECKS` (Django >= 4.1). Erro `SSL SYSCALL error: EOF detected` = conexão reaproveitada depois da suspensão | Connect Django to Neon |
| SSL | Toda conexão é SSL/TLS; sem SSL é recusada. A string padrão vem com `sslmode=require&channel_binding=require`. A página diz que `require` "verifica o certificado"; **no libpq `require` só cifra** (a verificação exige `verify-full` + raiz). O Neon recomenda `verify-full` (raiz ISRG Root X1, a do Let's Encrypt, normalmente já no sistema). `channel_binding=require` (SCRAM-SHA-256-PLUS) vale com libpq >= 13 | Connect securely |
| SNI | O Neon roteia pelo SNI (primeiro trecho do host). Precisa de libpq >= 14; senão: "The endpoint ID is not specified". O `psycopg[binary]` 3.3 do projeto traz o **libpq 18** (conferido: `psycopg.pq.version()` = 180006) | Connection errors; Connect Django to Neon |
| IPv4 | Projetos na AWS aceitam **IPv4 e IPv6**, nas duas strings (nada de ter que escolher o pooler por causa da rede) | Manage projects |
| Scale to zero | Plano gratuito: o compute é suspenso após **5 min** sem atividade e **não dá para desligar**. Reativa "em algumas centenas de milissegundos" (a página de erros fala em "alguns segundos"). Recomendações: `connect_timeout` maior (exemplo: 10 s) e **nova tentativa com espera** na conexão. Atualizações de manutenção reiniciam o compute (alguns segundos, semanais) | Scale to zero; Latency and timeouts; Manage projects |
| Plano gratuito | 100 projetos; **100 CU-horas/projeto/mês** (0,25 CU = 400 h); autoscaling até 2 CU; **1 GB de armazenamento por projeto** (20 GB na conta), compartilhado por todas as branches; 5 GB de saída (egress)/projeto/mês; 10 branches/projeto; monitoramento de 1 dia. Sem IP Allow, sem branches protegidas, sem SLA | Plans; Pricing |
| Se estourar | Sem CU-horas ou saída: o compute fica suspenso até o próximo ciclo (ou até o upgrade). Acima de 1 GB: **gravações falham**. Acima de 10 branches: não cria branch. **Nenhum desses limites apaga dados** | Plans; Pricing |
| Inatividade | Nas páginas lidas **não há regra de pausar ou apagar projetos gratuitos na AWS por inatividade** (a regra de "90 dias inativo, sujeito a exclusão" está só nos projetos das regiões Azure, descontinuadas). O compute é suspenso sozinho e acorda na próxima conexão. Confira os termos antes de depender disso | Azure regions deprecation |
| Restauração (PITR) | Janela de histórico do plano gratuito: **6 horas** (teto de 1 GB de histórico); pago: até 7 dias (Launch) ou 30 (Scale). Restauração instantânea só em **branches raiz**, na própria branch (a anterior vira `<branch>_old_<data>`) | History window; Backup & restore |
| Snapshots | Gratuito: **1 snapshot manual**, **sem agendamento**. Pago: 100 manuais + agendados (retenção de até 35 dias, US$ 0,09/GB-mês) | Backup & restore; Plans |
| Backup externo | O Neon recomenda `pg_dump` para cópia fora da plataforma, **sempre pela string direta**, com cliente de versão igual (ou maior) que a do projeto. Sem `pg_dumpall` e sem `-C/--create`. Projeto apagado: recuperável por **7 dias** (CLI/API) | Backup with pg_dump; pg_dump / pg_restore; Manage projects |
| Branches | Cópia na escrita, instantânea, não pesa no pai; os papéis vêm junto. Dá para criar a partir de um instante passado (dentro da janela) e definir **expiração**. No gratuito, a armazenagem delas conta nos 1 GB do projeto | Branching; Plans |
| Versões do Postgres | **14 a 18**. A versão principal é escolhida na criação e **não sobe no mesmo projeto** (migrar para projeto novo). Nota: a página de `pg_dump` ainda diz "14 a 17"; valem as páginas de compatibilidade e de versões (14 a 18) | Version support; Compatibility |
| Regiões | `aws-sa-east-1` (**São Paulo**) existe, com Postgres; a região é fixa por projeto. Também Frankfurt, Londres, N. Virginia, Ohio, Oregon, Singapura e Sydney | Regions |
| Papéis | O projeto nasce com `neondb_owner` (banco `neondb`), membro de `neon_superuser` (`CREATEDB`, `CREATEROLE`, `BYPASSRLS`, `REPLICATION`, `pg_read_all_data`, `pg_write_all_data`). Papéis criados no Console/CLI/API também. Papéis criados **por SQL** não recebem isso: são papéis comuns, com os privilégios que você der. Postgres 15+: o papel `public` só tem `USAGE` no esquema `public` | Manage roles; Database access |
| Extensões | `pg_trgm` é suportada e é "trusted" no Postgres 13+: quem tem `CREATE` no banco a instala. Papéis do Neon só instalam as extensões que o Neon suporta | pg_trgm; Compatibility |
| Data API | **Opcional e desligado por padrão**, ativado por branch/banco. Usa os papéis **`authenticated`** e **`anonymous`** (não existem `anon` nem `service_role`). A opção "Grant public schema access" dá `SELECT/INSERT/UPDATE/DELETE` a `authenticated` em **todas as tabelas atuais e futuras do `public`**; só o RLS segura as linhas. Não funciona com IP Allow. Não é recuperado se o projeto apagado for restaurado | Data API: get started; Access control; Manage projects |
| Collation | O banco nasce com `C.UTF-8` (ordem por ponto de código: nomes com acento ordenam depois dos sem acento) | Compatibility |
| Integração com a Vercel | Há a integração "gerenciada pela Vercel" (cobrança na Vercel) e a "gerenciada pelo Neon" (conta Neon própria). Ver seção 19 | Vercel-managed integration |

### O que isto mudou no plano

1. **ASGI x WSGI.** Com `ASGI_APPLICATION` e `WSGI_APPLICATION` definidos, a Vercel escolhe o ASGI (e o `config/asgi.py` migraria o banco a cada partida a frio). Agora: `tool.vercel.entrypoint = "config.wsgi:application"`, o `ASGI_APPLICATION` some quando `VERCEL` está definida e o `AUTO_MIGRATE` vira `false` por padrão na Vercel.
2. **Pooler.** Detectado pelo host (`-pooler` no primeiro trecho), não por porta: há só duas strings (direta e com pooler). `DATABASE_POOLER=direct|transaction` continua forçando.
3. **`search_path` no pooler.** O pooler recusa `options=-c search_path=...`. Direta e local usam `options`; **o pooler usa o `search_path` do papel** (`ALTER ROLE`). Falha alto (tabela ou função não encontrada), nunca em silêncio (testado em banco descartável).
4. **Esquema próprio e RLS saíram do caminho.** O Neon não expõe nada por padrão (o Data API vem desligado; os papéis dele, `authenticated` e `anonymous`, só nascem se você o ligar). O padrão passa a ser o esquema `public`. O `DATABASE_SCHEMA` continua como opção, agora só como plano B caso alguém ligue o Data API (ele só concede acesso ao `public`). O `harden_database` (RLS + revogar papéis) foi **removido**: protegia contra um risco que não existe aqui, e os papéis da Data API do Neon (`authenticated`/`anonymous`) só nascem se você a ligar.
5. **`pg_trgm`.** Sem o esquema `extensions`: a migration usa o `TrigramExtension` padrão do Django. O dono do banco cria a extensão uma vez; sem isso, a migration a cria se o papel tiver `CREATE` no banco (testado nos dois casos, com e sem esquema próprio).
6. **Migrar e `pg_dump` pela string direta.** `DATABASE_MIGRATE_URL` recusa host com `-pooler`.
7. **Cold start.** `connect_timeout` de 10 s e **uma** nova tentativa só ao abrir a conexão (`config/db_backend`), apenas com banco remoto. Nada muda no desenvolvimento local.
8. **Backup de verdade.** No plano gratuito a janela de PITR é de 6 horas e há 1 snapshot manual: a proteção real é o `mise run cloud-backup` (seção 12).
9. **Duração.** O padrão da Vercel é 300 s, não 10 s. O `backend/vercel.json` limita a função a **60 s** de propósito (a leitura leva 1 a 2 s).
10. **WebSocket.** Já existe em beta, mas continua não valendo a pena: instâncias separadas, nada de fanout sem Redis. A nuvem usa polling (5 s) e, no upload, a resposta já vem com o laudo lido.
11. **`CORS_ALLOWED_ORIGINS`** estava fixo no código (só `localhost:3100`); agora vem de `DJANGO_CORS_ALLOWED_ORIGINS`.
12. **Pacote.** Medido: **133 MB** descompactados (Linux x86_64, só dependências de produção), longe dos 500 MB. `daphne`, `redis` e `channels_redis` foram para o grupo `local`, fora do pacote.
13. **Páginas do Neon que se contradizem** (seguimos a mais nova ou a mais específica): `pg_dump` diz Postgres 14 a 17 e as de versões dizem 14 a 18; "Connect securely" diz que `require` verifica o certificado, mas o libpq só cifra (por isso `verify-full` aparece como melhoria, não como pré-requisito).

## 3. Antes de começar

- Contas: [Vercel](https://vercel.com) (plano Hobby serve para uso pessoal e não comercial) e [Neon](https://neon.com) (plano gratuito serve, com as ressalvas de armazenamento e backup das seções 2 e 12). Ative a verificação em duas etapas no provedor de login da Neon (GitHub, Google...) e na Vercel.
- Na sua máquina: `mise install` e **Docker** (não há `pg_dump` local: o `cloud-backup` roda o cliente do Postgres num contêiner). O `psql` também pode rodar no contêiner (seções 9 e 12).
- Meça o tamanho do banco local antes (o plano gratuito tem 1 GB): `docker exec myexams-db-1 psql -U myexams -d myexams -c "select pg_size_pretty(pg_database_size('myexams'))"`.
- Gere os segredos (nenhum vai para o git):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # DJANGO_SECRET_KEY (outra para cada ambiente)
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # senha do papel myexams_app
```

A senha do papel só tem letras, números, `-` e `_`: entra na URL sem precisar de codificação. O Neon exige senha com pelo menos 60 bits de entropia; a acima tem 192.

Sobre privacidade, a regra do projeto continua: **nunca** coloque laudo, valor de exame, CPF ou senha em issue, chat, log ou
exemplo. Os valores abaixo são sempre `<placeholders>`.

## 4. Passo 1: projeto no Neon

1. **New project**: nome `myexams`, região **AWS South America (São Paulo) `aws-sa-east-1`** (a mesma da função `gru1` da Vercel; a região não muda depois), **Postgres 17** (o desenvolvimento local usa 16: um dump do 16 restaura no 17; a versão do projeto não sobe depois, só migrando para um projeto novo). Anote a versão: ela vai em `CLOUD_PG_MAJOR`.
2. **Ligado por padrão e deve ficar assim:** conexões só por SSL. **Não ligue o Data API** (Settings > Postgres > Data API; vem desligado). **IP Allow** não existe no plano gratuito e não serviria de qualquer forma: as funções da Vercel saem de IPs dinâmicos.
3. **Computação** (Branches > o compute da `production` > Edit): autoscaling no mínimo (0,25 CU) e um teto baixo (por exemplo 1 CU) para as 100 CU-horas/mês durarem. Cada acordar mantém o compute ativo por pelo menos 5 minutos: 0,25 CU x 5 min = ~0,02 CU-hora, então até 100 acordares por mês gastam ~2 CU-horas. O scale to zero de 5 minutos é fixo no plano gratuito.
4. **SQL Editor** (ou `psql`), conectado como `neondb_owner`, uma vez. O papel do app é criado **por SQL**: fica sem `neon_superuser` (sem `CREATEROLE`, `CREATEDB`, `BYPASSRLS`, `REPLICATION`), só com o que ele precisa:

```sql
CREATE ROLE myexams_app LOGIN PASSWORD '<SENHA-FORTE-GERADA>';
GRANT CONNECT ON DATABASE neondb TO myexams_app;
GRANT CREATE ON SCHEMA public TO myexams_app;   -- Postgres 15+: sem isto o papel não cria tabelas no public
CREATE EXTENSION IF NOT EXISTS pg_trgm;         -- o dono cria uma vez; a migration só confere (similaridade de nomes)
```

   Dá para usar o `neondb_owner` direto (funciona e é mais simples), mas ele pode criar papéis e bancos e ignora RLS; se a credencial da Vercel vazar, o estrago é maior. O papel `myexams_app` fica dono das tabelas que criar.

5. **Connect** (botão no topo do painel, branch `production`, banco `neondb`). Copie a string e troque usuário e senha pelos do papel do app. Você precisa de **duas**, que diferem só pelo `-pooler` no host:

| String | Onde vai | Exemplo (placeholders) |
|---|---|---|
| **Com pooler** (toggle *Connection pooling* ligado; host `ep-<endpoint>-pooler...`) | Vercel projeto B, `DATABASE_URL` | `postgresql://myexams_app:<senha>@ep-<endpoint>-pooler.sa-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require` |
| **Direta** (toggle desligado; host `ep-<endpoint>...`) | `backend/.env.cloud`, `DATABASE_MIGRATE_URL` | `postgresql://myexams_app:<senha>@ep-<endpoint>.sa-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require` |

   - O app descobre o pooler pelo `-pooler` no host (e liga o modo transação sozinho). `DATABASE_MIGRATE_URL` **recusa** um host com `-pooler`.
   - Se a autenticação falhar **só** na string com pooler, tire `&channel_binding=require` dela (ver seção 20).
   - Para endurecer o SSL depois de tudo funcionar: `sslmode=verify-full&sslrootcert=system` (libpq >= 16; o do projeto é o 18). Com só `require` a conexão é cifrada mas o servidor não é verificado.
   - Se a senha tiver caracteres especiais, codifique-os na URL (`@` -> `%40`, `/` -> `%2F`, `#` -> `%23`, `?` -> `%3F`, `%` -> `%25`).

6. Verifique (SQL Editor, como `neondb_owner`):

```sql
SELECT rolname, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication FROM pg_roles WHERE rolname = 'myexams_app';  -- tudo false
SELECT extname, extnamespace::regnamespace FROM pg_extension WHERE extname = 'pg_trgm';                                -- public
SELECT rolname FROM pg_roles WHERE rolname IN ('authenticated', 'anonymous');                                          -- 0 linhas (Data API desligado)
```

### Esquema próprio (opcional, não recomendado)

Só faz sentido se você pretende ligar o Data API um dia (ele só concede acesso ao `public`). Antes da primeira migração, como `neondb_owner`:

```sql
CREATE SCHEMA myexams AUTHORIZATION myexams_app;
ALTER ROLE myexams_app IN DATABASE neondb SET search_path = myexams, public;   -- vale também pelo pooler
```

e defina `DATABASE_SCHEMA=myexams` no projeto B e no `.env.cloud`. Sem o `ALTER ROLE`, o pooler não acha as tabelas (erro "relation does not exist", testado). O `pg_trgm` criado no `public` continua resolvendo pelo `search_path` `myexams, public`.

## 4b. Registro: o que já foi feito neste projeto (18/10/2026)

O projeto Neon **MyExams** (`aws-sa-east-1`, **Postgres 18**, branch `production`) já existe e este
repositório já está ligado a ele. O que foi executado, e o que ficou de fora de propósito:

- **CLI**: `npx neon@latest` (sem instalação global). `neon login` abriu o navegador uma vez.
- **`neon init -y --services postgres --no-agent-setup --package-manager bun`**. Ficou de fora:
  - `auth`: o app tem login próprio (JWT); o Neon Auth criaria um serviço e variáveis que ninguém usa.
  - a instalação do Neon nos agentes de código (`--no-agent-setup`): mexe na configuração das suas
    ferramentas de IA, sem relação com o app.
- **Arquivos criados na raiz**: `neon.ts` (política: `auth: false`, nada na branch padrão, branches
  novas expiram em 7 dias), `package.json` + `bun.lock` (só `@neon/config` e `@neon/env`, para o
  `neon.ts`). `.neon` (vínculo), `.env.local` e `node_modules` ficam **fora do git**.
- **`neon deploy --no-env-pull`**: "No changes", como esperado. `.env.local` (0600) guarda as duas
  connection strings: a **POOLED** (`-pooler`) vai para `DATABASE_URL` no projeto Vercel do backend;
  a **direta** foi copiada para `backend/.env.cloud` como `DATABASE_MIGRATE_URL` (com `CLOUD_PG_MAJOR=18`).
  Elas não vão para `backend/.env`: o servidor local nunca deve apontar para a nuvem sem querer.
- **Ensaio numa branch descartável** (`neon branches create --name rehearsal`, apagada depois):
  migrações completas, `pg_trgm`, 19 tabelas, 116 exames, login JWT, upload processado na própria
  requisição pelo endpoint do pooler, `/health/` e limpeza dos dados de teste.
- **Produção migrada** (`cloud_migrate`, só o esquema e o catálogo; **sem usuários nem laudos**) e
  **backup provado**: `scripts/cloud-backup.sh` gerou um `pg_dump -Fc` (Postgres 18, no Docker),
  conferido com `pg_restore --list`.

**Latência medida**: deste computador até o Neon em São Paulo foram **~230 ms por consulta**
(a Vercel `gru1` fica a poucos ms). Consequências práticas:

- Qualquer carga em massa feita *daqui* é lenta: o upload de um laudo pequeno levou ~9 s; um laudo
  de 50 resultados pode levar de dezenas de segundos a minutos. Para trazer os laudos já lidos,
  prefira **dump + restore** (seção 9, opção B: uma transação, em lote) a `reload-reports`.
- O seed do catálogo (`sync_catalog`) agora é **em lote** (~dez consultas; antes eram milhares, uma
  transação por exame, e a migração `0002` não terminava). O teste `test_catalog_sync.py` trava o
  número de consultas.
- **Armadilha que custou tempo**: rodar a migração dentro de um `subprocess` com *timeout* mata só o
  `uv` e deixa o Python filho vivo, com a transação aberta. A tentativa seguinte então espera para
  sempre no `CREATE EXTENSION pg_trgm` (lock `transactionid`). Se uma migração parecer travada,
  veja as sessões (`select pid, state, wait_event, left(query, 80) from pg_stat_activity`) e mate o
  processo antes de tentar de novo.

## 4c. Registro: Vercel já criada e no ar (19/10/2026)

Feita pela CLI (`npx vercel@latest`, login pelo navegador). Dois projetos Git-ligados a `ryalb/myexams`, branch `main`:

| Projeto | Root Directory | Framework | Domínios públicos | Região da função |
|---|---|---|---|---|
| `myexams-api` (backend) | `backend` | Django | `myexams-api.vercel.app` (**use este como base da API**), `myexams-api-ryanleite-9041.vercel.app` | `gru1` |
| `myexams-app` (frontend) | `frontend` | Nuxt (SPA) | **`myexams-app.vercel.app`**, `myexams-ryanleite-9041.vercel.app` | `gru1` |

O que a CLI mostrou e vale lembrar:

- **O build do backend instala do `uv.lock`** ("Installing required dependencies from uv.lock"), não do
  `requirements.txt`; a função ficou com ~80 MB (limite: 500). O build também executa o Django, então
  as variáveis (a `DJANGO_SECRET_KEY` em especial) precisam existir **antes** do primeiro deploy.
- **Proteção de deploy**: o padrão do projeto novo (`all_except_custom_domains`) põe o login da Vercel na
  frente de qualquer endereço `*.vercel.app` que não seja o principal, inclusive o alias com o nome da
  equipe. Os dois projetos ficaram com `ssoProtection = preview` (só previews protegidos; o app tem login
  próprio). O endereço por deploy (`myexams-<hash>-...vercel.app`) responde 400 porque não está em
  `DJANGO_ALLOWED_HOSTS`, o que é desejado.
- **Variáveis do backend**: só em *Production* (previews não recebem o banco). `DJANGO_SECRET_KEY` e
  `DATABASE_URL` (a string **pooled**) são *sensitive* e não dá para lê-las de volta; as demais são
  texto simples. `DATABASE_MIGRATE_URL` e `CLOUD_PG_MAJOR` **não** vão para a Vercel.
- **Cuidado ao importar pelo painel**: colar o `.env.cloud` inteiro no importador põe a string DIRETA do
  banco nos ambientes *Production e Preview* do projeto errado. Aqui isso aconteceu uma vez e foi
  desfeito (`vercel api ... -X DELETE ... --dangerously-skip-permissions`; sem a flag o comando pede
  confirmação e, em script, falha sem avisar).
- **Deploy sem subir arquivos locais**: `vercel deploy` a partir desta pasta enviaria o que o `.gitignore`
  esconde (os PDFs de `fixtures/private/`, `.env.local`). Para deployar sem novo commit use a API, que
  faz a Vercel clonar o GitHub: `POST /v13/deployments` com `gitSource` (`type: github`, `repoId`, `ref`).
- **CORS** do backend: `https://myexams-app.vercel.app` e o alias com o nome da equipe.
  `DJANGO_CSRF_TRUSTED_ORIGINS`: o da API. Trocou o endereço do frontend? Atualize o CORS **e faça novo deploy do backend**.
- **Por que não `myexams.vercel.app`**: esse nome já é de outro projeto no mundo (responde 200 com o site dele), então
  a Vercel acrescentou um sufixo aleatório (`-zeta`). O nome do endereço `*.vercel.app` vem do **nome do projeto**:
  o frontend foi renomeado para `myexams-app` (livre; teste com `curl -sI https://NOME.vercel.app`: `404` +
  `x-vercel-error: DEPLOYMENT_NOT_FOUND` = livre). Renomear **não** liga o novo endereço sozinho: foi preciso
  `POST /v10/projects/myexams-app/domains` com `{"name":"myexams-app.vercel.app"}`; o `-zeta` saiu do projeto com
  `DELETE /v9/projects/myexams-app/domains/myexams-zeta.vercel.app` (`--dangerously-skip-permissions`).
  Um domínio próprio, se um dia quiser, entra pelo mesmo caminho (seção 10).
- **Provado na stack real** (usuário sintético, apagado no fim): login 200 (1,5 s), upload 202 (1,2 s,
  lido na própria requisição), resultados, 413 acima de 4,5 MB, e cada usuário só vê os próprios laudos.

**Dados levados (19/10/2026), pela Opção B** (seção 9): conta, perfil, catálogo, laboratórios, 22 laudos, 946 resultados
(826 do laudo, 119 anteriores impressos, 1 digitado à mão) e 22 execuções, numa única transação (13 s). Conferido
tabela a tabela contra o banco local por **contagem e checksum do conteúdo** (oito tabelas idênticas), sem FK
órfã e com as sequências acima do maior id. A conta veio **com a mesma senha do ambiente local** e já era
administradora; `make_admin` rodou de novo na nuvem só para confirmar. Backup logo depois:
`fixtures/private/db-backups/cloud-<data>.dump`.

- Os arquivos SQL com os dados são apagados ao fim (o `trap` do script roda mesmo se algo falhar).
- **Laboratórios são linhas compartilhadas**: apagar um usuário de teste não apaga o laboratório que o
  upload dele criou. O ensaio na stack real deixou um laboratório fictício ("Exemplo"), que foi removido
  à mão (só se nenhum laudo o referencia). Em ensaios futuros, confira `select * from exams_laboratory`.

## 5. Passo 2: segredos e arquivos locais

Crie `backend/.env.cloud` (ignorado pelo git; só as tarefas `cloud-*` o leem, **nunca** o servidor local):

```bash
cp backend/.env.cloud.example backend/.env.cloud
chmod 600 backend/.env.cloud
# edite: DATABASE_MIGRATE_URL (a DIRETA), CLOUD_PG_MAJOR (18) e, se usou, DATABASE_SCHEMA
```

## 6. Passo 3: Vercel, projeto B (backend)

Dashboard > **Add New... > Project** > importe o repositório (um projeto por pasta do monorepo).

| Configuração | Valor |
|---|---|
| Project Name | `myexams-api` (qualquer nome) |
| **Root Directory** | `backend` |
| Framework Preset | detectado como Django (ou *Other*); o `manage.py` está na raiz do projeto |
| Install Command | padrão (a Vercel instala o `requirements.txt` com `uv`) |
| Build Command | padrão (`collectstatic` roda sozinho e não encontra nada: o admin está desligado) |
| Python | 3.12 (`backend/.python-version` e `requires-python`) |
| Região das funções | `gru1` (já em `backend/vercel.json`; no Hobby cabe uma região) |
| Duração máxima | 60 s (em `backend/vercel.json`) |
| Deployment Protection | *Standard Protection* (protege só previews; a produção fica aberta e o login é o do app) |

Confira no log do primeiro build a linha de instalação das dependências: deve listar o conteúdo do `requirements.txt`
(~50 pacotes, sem `daphne`/`pytest`/`ruff`). Se vier mais coisa, o pacote ainda cabe (243 MB no pior caso), só fica maior.

### Variáveis do projeto B

Marque **Secret** nas que dizem "sim" (ficam só de escrita depois de salvas). Ambiente: **Production** (e *Preview* só se for usar previews do backend: nesse caso **não** copie a `DATABASE_URL` de produção para lá).

| Variável | Valor | Secret | Para quê |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | 64+ caracteres aleatórios | **sim** | Assina os JWTs; o app recusa subir com a chave de exemplo |
| `DJANGO_DEBUG` | `false` | não | Nunca `true` na nuvem |
| `DJANGO_ALLOWED_HOSTS` | `api.exemplo.com.br,myexams-api.vercel.app` | não | Hosts aceitos (sem esquema) |
| `DATABASE_URL` | String **com pooler** do papel `myexams_app` (passo 1) | **sim** | Banco em tempo de execução |
| `DATABASE_SCHEMA` | só se usou o esquema próprio (`myexams`) | não | Tabelas fora do `public` |
| `CELERY_TASK_ALWAYS_EAGER` | `true` | não | Modo inline: lê o laudo na requisição |
| `EXTRACTION_USE_LLM` | `false` | não | **Obrigatório**: o padrão do código é `true` e tentaria falar com o Ollama |
| `DJANGO_CACHE` | `db` | não | Throttling no banco (tabela `django_cache`); instâncias não compartilham memória |
| `DJANGO_CHANNEL_LAYER` | `none` | não | Sem WebSocket |
| `DJANGO_ADMIN_ENABLED` | `false` | não | Sem `/admin/` do Django |
| `DRF_BROWSABLE_API` | `false` | não | Só JSON |
| `DRF_NUM_PROXIES` | `1` | não | O throttle usa o IP real (a Vercel reescreve `X-Forwarded-For`) |
| `DJANGO_CORS_ALLOWED_ORIGINS` | `https://app.exemplo.com.br` | não | Origem do frontend (esquema incluído, sem barra) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://api.exemplo.com.br` | não | Só importa para sessão/admin; por segurança |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | `true` | não | Confia no `X-Forwarded-Proto` da Vercel |
| `DJANGO_SECURE_SSL_REDIRECT` | `true` | não | HTTP -> HTTPS |
| `DJANGO_SECURE_HSTS_SECONDS` | `3600`, depois `31536000` | não | Comece baixo; suba quando tudo funcionar |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | `true` | não | Só se todos os subdomínios forem HTTPS |
| `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE` | `true` | não | Cookies só por HTTPS |
| `DJANGO_ALLOW_PUBLIC_REGISTRATION` | `false` | não | Contas só criadas por você ou pelo administrador do app |
| `EXAMS_MAX_UPLOAD_MB` | `4` | não | A Vercel corta o corpo em 4,5 MB; 4 MiB + o envelope cabem |
| `EXAMS_UPLOAD_DIR` | `/tmp/myexams-uploads` | não | Único lugar gravável |
| `EXAMS_MAX_PAGES` | `30` | não | Opcional: limita o custo por laudo |
| `EXAMS_ORPHAN_HOURS` | `0.25` | não | Laudo preso em "Extraindo" vira falho depois de 15 min (varrido a cada upload) |
| `DJANGO_AUTO_MIGRATE` | `false` | não | Já é o padrão na Vercel; explícito por clareza |

Já valem sozinhos, sem variável (banco remoto): `connect_timeout=10` e **uma** nova tentativa ao abrir a conexão (`DATABASE_CONNECT_TIMEOUT`, `DATABASE_CONNECT_RETRIES`); modo transação pelo `-pooler` do host (`DATABASE_POOLER` só para forçar). Não defina `DATABASE_MIGRATE_URL` na Vercel (é só para as tarefas `cloud-*`), nem `REDIS_URL`, `OLLAMA_*` ou `AI_ALLOW_REMOTE_INFERENCE`. O `DJANGO_LOG_PLAIN` já vale `true` quando a
Vercel define `VERCEL`. Mudou uma variável? **Faça um novo deploy** (variáveis não valem para deploys antigos).

## 7. Passo 4: Vercel, projeto A (frontend)

Dashboard > **Add New... > Project** > o **mesmo** repositório, outra pasta.

| Configuração | Valor |
|---|---|
| Project Name | `myexams-web` |
| **Root Directory** | `frontend` |
| Framework Preset | **Nuxt** (detectado) |
| Install Command | padrão (detecta `bun.lock` e roda `bun install`) |
| Build Command | padrão (`nuxt build`, preset `vercel`; `ssr: false` gera uma SPA) |
| Node.js | 22.x (Project Settings > General) |
| Deployment Protection | *Standard Protection* (previews protegidos, produção aberta; o app exige login) |

Deep links (`/documents/12`) funcionam sem configuração extra: o `nuxt build` do preset `vercel` gera uma rota-fallback que
serve a SPA (conferido no build: `/(.*)` -> `__fallback`).

### Variáveis do projeto A

Nenhuma é segredo (o Nuxt as grava no JavaScript do navegador). Valem no **build**: depois de mudar uma, faça novo deploy.

| Variável | Valor | Para quê |
|---|---|---|
| `NUXT_PUBLIC_API_BASE_URL` | `https://api.exemplo.com.br/api/v1` | Base da API |
| `NUXT_PUBLIC_GRAPHQL_ENDPOINT` | `https://api.exemplo.com.br/graphql/` | GraphQL |
| `NUXT_PUBLIC_REALTIME` | `false` | Sem WebSocket: as telas atualizam por polling, sem socket, erro no console nem retentativa |
| `NUXT_PUBLIC_MAX_UPLOAD_MB` | `4` | Opcional: plano B do limite (a API informa o dela em `/auth/me/`) |

Há um modelo em `frontend/.env.production.example`.

## 8. Passo 5: primeira sequência

Pode ser feito antes ou depois de criar o projeto B, mas **antes do primeiro acesso ao app**, e com o `backend/.env.cloud` pronto.

```bash
# 1) Migra o banco da nuvem pela string DIRETA: cria as tabelas, a tabela de cache e confere o pg_trgm.
#    Mostra só host, porta, banco e esquema (nunca a senha) e exige --yes.
mise run cloud-migrate            # confira o alvo impresso...
mise run cloud-migrate -- --yes   # ...e confirme

# 2) Primeiro usuário e primeiro administrador do app. O login usa o e-mail como username.
#    (Pule este item se for usar a Opção B do passo 6: as contas vêm no dump, e a tabela de usuários tem de estar vazia.)
mise run cloud-manage -- createsuperuser --username voce@exemplo.com --email voce@exemplo.com
mise run cloud-manage -- make_admin voce@exemplo.com

# 3) Primeiro backup (ainda vazio, mas prova que o caminho funciona) e primeiro teste de leitura do dump.
mise run cloud-backup
```

- `cloud-migrate` roda `migrate` e `createcachetable` (com `DJANGO_CACHE=db`). O compute pode levar um instante para acordar: o `connect_timeout` e a nova tentativa cobrem isso.
- A função da Vercel **nunca** migra (`AUTO_MIGRATE=false`). Migrou? Só pela máquina, antes de promover o código novo, e **depois de um `cloud-backup`**.
- Depois do primeiro login, preencha **sexo** e **data de nascimento** em `/profile` (escolhem a faixa de referência) e crie as
  contas da família em **Usuários** (README, "Administração de usuários").

## 9. Passo 6: trazer os dados locais

**Opção A (recomendada): reler os PDFs apontando para a nuvem.** Os PDFs **não saem da sua máquina**; só as linhas extraídas
vão para o banco. É o mesmo comando do `reload-reports`, com o alvo trocado:

```bash
mise run cloud-manage -- import_reports ../fixtures/private --recursive --no-llm \
  --laboratory-map ../fixtures/private/laboratories.json --email voce@exemplo.com
```

(A conta `voce@exemplo.com` tem de existir na nuvem: passo 5.) Pode rodar de novo à vontade: laudos já importados são pulados.

**Opção B: dump de dados e carga** (leva também as **contas**, com as mesmas senhas, e os resultados que você corrigiu à mão).
Faça **antes** de criar o primeiro usuário na nuvem (a tabela `auth_user` da nuvem precisa estar vazia). Sem `pg_dump` local, use o
do contêiner do próprio banco de desenvolvimento. O catálogo de exames é levado junto e **substitui** o semeado pela migração,
porque os resultados apontam para o exame pelo id. Com o esquema `public` (padrão) não há nada para renomear:

```bash
umask 077   # os arquivos têm dado de saúde; fixtures/private/ é ignorado pelo git
docker exec myexams-db-1 pg_dump -U myexams -d myexams --data-only --no-owner --no-privileges \
  -t 'public.exams_*' -t public.auth_user > fixtures/private/dados-local.sql
# Uma transação só: se algo falhar, nada fica pela metade.
( echo "TRUNCATE public.exams_analyte, public.exams_analytealias, public.exams_unitconversion RESTART IDENTITY CASCADE;"
  cat fixtures/private/dados-local.sql ) > fixtures/private/carga-nuvem.sql
PGENV="$(mktemp)"   # variáveis PG* da string direta do .env.cloud; a senha não vai para a linha de comando
( cd backend && uv run python -m config.pgenv .env.cloud env "$PGENV" )
docker run --rm -i --env-file "$PGENV" postgres:<CLOUD_PG_MAJOR>-alpine \   # a mesma versão do projeto Neon (18)
  psql -v ON_ERROR_STOP=1 -1 -q -o /dev/null < fixtures/private/carga-nuvem.sql
rm -f "$PGENV" fixtures/private/dados-local.sql fixtures/private/carga-nuvem.sql
```

(Validado com bancos descartáveis em Postgres 17: contas, perfis e catálogo chegam e as sequências continuam certas.) Com `DATABASE_SCHEMA=myexams`,
troque os `public.` por `myexams.` nas linhas `COPY` e `setval` do dump (`sed -E "s/^COPY public\./COPY myexams./; s/^(SELECT pg_catalog\.setval\(')public\./\1myexams./"`) e no `TRUNCATE`.
Os tokens antigos deixam de valer (a chave secreta é outra). Se algo der errado no meio, o `reset_db` recusa bancos remotos:
restaure o backup anterior (seção 12) ou use o PITR do Neon.

## 10. Passo 7: domínio, CORS e CSRF (ordem importa)

1. Faça o deploy do **backend** com a URL padrão (`myexams-api.vercel.app`). `DJANGO_ALLOWED_HOSTS` com esse host; teste `https://myexams-api.vercel.app/health/` (resposta `{"status":"ok"}`).
2. Faça o deploy do **frontend** com `NUXT_PUBLIC_API_BASE_URL` apontando para essa URL. Anote a URL dele.
3. No projeto B, ponha a URL do frontend em `DJANGO_CORS_ALLOWED_ORIGINS` e a da API em `DJANGO_CSRF_TRUSTED_ORIGINS`; novo deploy do backend. Teste o login.
4. Adicione os **domínios próprios** (Settings > Domains): `api.exemplo.com.br` no projeto B e `app.exemplo.com.br` no A (siga o DNS que a Vercel mostrar).
5. Atualize `DJANGO_ALLOWED_HOSTS` (inclua `api.exemplo.com.br`), `DJANGO_CORS_ALLOWED_ORIGINS` (`https://app.exemplo.com.br`) e `NUXT_PUBLIC_API_BASE_URL` (`https://api.exemplo.com.br/api/v1`, e o GraphQL); novo deploy **dos dois**.
6. Teste de novo e só então suba o HSTS para `31536000`.

Observações: a origem do CORS é **exata** (esquema + host, sem barra final), então **previews do frontend não conseguem chamar a
API** de produção. Teste pelo domínio de produção. Não ligue *All Deployments* (nem senha) no projeto da API: o navegador do
app não consegue passar pela tela da Vercel e tudo vira 401.

## 11. Proteção de previews

- Em cada projeto: Settings > **Deployment Protection** > *Vercel Authentication* com escopo **Standard Protection** (todos os planos). Previews e URLs geradas ficam atrás do login da Vercel; o domínio de produção continua aberto (o login do app protege os dados).
- Para nem criar previews, limite a **Production Branch** e desligue os deploys de outras branches (Settings > Git) ou conecte o repositório só à `main`.
- Previews do backend usam as variáveis do ambiente *Preview*: **não** copie a `DATABASE_URL` de produção para lá.

## 12. Backup e restauração

No plano gratuito do Neon a janela de restauração instantânea (PITR) é de **6 horas** e há **1 snapshot manual**, sem agendamento
(Launch: até 7 dias e snapshots agendados; Scale: até 30 dias). Seis horas não cobrem "percebi na semana seguinte", então a
cópia que importa é a sua:

```bash
mise run cloud-backup
# -> Alvo: host=ep-<endpoint>... porta=5432 banco=neondb
# -> Backup gravado em fixtures/private/db-backups/cloud-AAAAMMDD-HHMMSS.dump (NNNNN bytes)
```

O que a tarefa faz (`scripts/cloud-backup.sh`):

- lê `DATABASE_MIGRATE_URL` de `backend/.env.cloud` (a **direta**; com `-pooler` ela se recusa, porque o `pg_dump` usa `SET`);
- roda `pg_dump -Fc --no-owner --no-acl` no Docker (`postgres:<CLOUD_PG_MAJOR>-alpine`, padrão 18, a versão do projeto Neon);
- entrega as credenciais por variáveis `PG*` de um `--env-file` temporário (0600, apagado ao sair): **a URL e a senha não aparecem na linha de comando nem no `ps`**;
- grava em `fixtures/private/db-backups/` (pasta 0700, ignorada pelo git), arquivo 0600, e confere com `pg_restore --list`; se falhar, o arquivo é apagado.

Quando: **antes de toda migração**, antes de importar dados, e pelo menos uma vez por semana. Guarde uma cópia **cifrada** fora do
computador (o dump tem dado de saúde). Docker parado ou sem a imagem: o script para com uma mensagem, sem criar arquivo.

### Restaurar

1. **Erro recente (até 6 h): PITR no Console.** Branch `production` > **Backup & restore** > escolha data e hora > *Preview data* (confira antes) > **Restore**. A branch volta no tempo e o estado anterior fica guardado numa branch `production_old_<data>` (apague depois; confira que a string de conexão continua valendo). Só vale para branches raiz.
2. **Antes de algo arriscado:** crie o snapshot manual (Backup & restore > *Create snapshot*); só cabe um no plano gratuito, então substitua o antigo.
3. **A partir do `.dump`** (mais antigo que a janela, ou outro projeto/conta): restaure num banco **vazio** e novo, de preferência numa branch ou num projeto de ensaio, e só então troque as conexões. Num arquivo só do **destino** (ex.: `/tmp/destino.env`, 0600, com a linha `DATABASE_MIGRATE_URL=<string direta do destino>`):

```bash
PGENV="$(mktemp)"
( cd backend && uv run python -m config.pgenv /tmp/destino.env env "$PGENV" )
docker run --rm -i --env-file "$PGENV" postgres:17-alpine \
  sh -c 'exec pg_restore --no-owner --no-acl --single-transaction --exit-on-error -d "$PGDATABASE"' \
  < fixtures/private/db-backups/cloud-AAAAMMDD-HHMMSS.dump
rm -f "$PGENV"
```

   Num banco que já tem tabelas, acrescente `--clean --if-exists` ao `pg_restore`. O dump traz a extensão `pg_trgm`: o papel que restaura precisa de `CREATE` no banco (ou o dono cria a extensão antes). Sem `-C`: o Neon não aceita `--create`. (Testado em Postgres 17 descartável, com e sem objetos já existentes.)
4. **Projeto apagado sem querer:** recuperável por 7 dias (`neon projects recover <id>` ou pela API). O Data API, se ligado, não volta.

Depois de qualquer restauração: confira `/health/`, entre no app, abra um laudo e compare as contagens (`select count(*) from exams_examresult`).

### Ensaio com uma branch do Neon

Uma branch é uma cópia instantânea (copiada na escrita) da `production`, com os papéis e os dados: ideal para ensaiar uma migração antes de rodá-la de verdade. **Ela contém os dados de saúde de verdade**: trate como produção, não a compartilhe e apague ao terminar.

1. Console > Branches > **New branch** a partir da `production` (opcional: defina uma expiração). Ela ganha um endereço próprio; copie a string **direta** dela (toggle de pooling desligado).
2. Ensaie sem tocar no `.env.cloud` (o ambiente do processo vence o arquivo):

```bash
DATABASE_MIGRATE_URL='postgresql://myexams_app:<senha>@ep-<outro-endpoint>.sa-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require' \
  mise run cloud-migrate -- --yes
```

3. Se deu certo, repita na `production` (depois de um `cloud-backup`). Apague a branch: no plano gratuito ela divide o 1 GB e o limite de 10 branches do projeto.
4. Também serve para testar um restore do `.dump` (item 3 acima) sem risco.

## 13. Operação: scale to zero, limites e rollback

**Scale to zero.** Depois de 5 minutos sem consulta o compute é suspenso e a primeira conexão o acorda. Segundo a documentação, isso leva "centenas de milissegundos" (pode chegar a alguns segundos). Como o app está configurado para isso: `connect_timeout=10` e uma nova tentativa (1 s depois) só ao abrir a conexão; nunca refaz uma transação. A primeira requisição depois de um tempo parado soma isso à partida a frio da função (Python + Django), então conte alguns segundos. As requisições seguintes são normais. Como o throttling usa a tabela `django_cache`, **toda requisição autenticada** acorda o banco: não há como "manter frio" nem é preciso manter quente. Não há `ping` externo recomendado.

**Limites do plano gratuito** (seção 2): 100 CU-horas/mês (o uso típico de uma família é de poucas), 1 GB de armazenamento (as gravações falham acima disso; confira de tempos em tempos: `select pg_size_pretty(pg_database_size(current_database()))`), 5 GB de saída, janela de 6 h. Nenhum desses limites apaga dados. **Se o uso crescer ou o app virar importante**, o plano Launch (pago pelo uso) dá 7 dias de PITR, snapshots agendados, branches protegidas e scale to zero desligável.

**Manutenção.** O Neon reinicia o compute para atualizações (alguns segundos, em geral semanais): a nova tentativa na abertura da conexão cobre a maior parte; uma requisição no meio de uma consulta pode falhar uma vez.

**Rollback.** Código: no painel da Vercel, promova um deploy anterior (ou `vercel promote <url-do-deploy-bom>`). Banco: as
migrações só andam para a frente; para voltar, use o PITR (até 6 h), um snapshot ou o `.dump` (seção 12) e promova o código
compatível. Rollback de código **não** desfaz migração.

## 14. Checklist de segurança

Marque cada item depois do deploy:

- [ ] `DJANGO_DEBUG=false` (com `true`, o GraphiQL e o `/admin/` ficariam à mostra). `DJANGO_ADMIN_ENABLED=false`; `GET /admin/` responde 404; `GET /graphql/` pelo navegador não abre GraphiQL.
- [ ] `DJANGO_SECRET_KEY` aleatória com 64+ caracteres, **só** no cofre da Vercel (marcada Secret), diferente do `.env` local.
- [ ] `cd backend && DJANGO_DEBUG=false DJANGO_SECRET_KEY=... uv run python manage.py check --deploy` sem avisos (rode com as mesmas variáveis do projeto B).
- [ ] **Data API do Neon desligado** (Settings > Postgres > Data API) e sem os papéis dele. Conferir no SQL Editor (esperado: 0 linhas):

```sql
SELECT rolname FROM pg_roles WHERE rolname IN ('authenticated', 'anonymous');
```

  Se um dia ligar o Data API: **não** marque "Grant public schema access" (ele dá `SELECT/INSERT/UPDATE/DELETE` em todas as tabelas do `public` ao papel `authenticated`), use o esquema próprio (`DATABASE_SCHEMA`, seção 4) e habilite RLS nas tabelas.
- [ ] Papel do app sem privilégios de administração (item 6 da seção 4: `rolcreaterole`, `rolcreatedb`, `rolbypassrls`, `rolreplication` todos `false`). A senha do `neondb_owner` fica só no gerenciador de senhas, fora da Vercel e fora do `.env.cloud`.
- [ ] SSL: o Neon recusa conexões sem SSL; use `sslmode=require` e, depois de testar, `verify-full&sslrootcert=system`. **IP Allow** não existe no plano gratuito (e as funções da Vercel têm IP dinâmico): a defesa é a credencial forte e o SSL.
- [ ] Conta do administrador com senha forte; `DJANGO_ALLOW_PUBLIC_REGISTRATION=false` (`POST /api/v1/auth/register/` deve dar 404).
- [ ] Verificação em duas etapas no login da Neon e na conta Vercel; nenhuma API key do Neon guardada no repositório ou na Vercel.
- [ ] Deployment Protection ligado (seção 11) e `DJANGO_CORS_ALLOWED_ORIGINS` só com o seu domínio.
- [ ] Dumps e `.env.cloud` fora do git (`fixtures/private/`, `.env.cloud` estão no `.gitignore`); `.env.cloud` com permissão 0600.
- [ ] Para trocar a senha do banco (vazamento ou rotina): `ALTER ROLE myexams_app PASSWORD '<nova>'` (ou *Reset password*), atualize `DATABASE_URL` na Vercel e `DATABASE_MIGRATE_URL` no `.env.cloud`, e faça um novo deploy.

## 15. Privacidade e LGPD

- **Onde ficam os dados.** Banco: Neon, AWS `sa-east-1` (São Paulo; a região é fixa por projeto). Processamento: função Vercel em `gru1` (também São Paulo). O frontend estático é servido pelo CDN global da Vercel (sem dado de saúde). Os backups ficam na sua máquina (`fixtures/private/db-backups/`).
- **Operadores.** Vercel e Neon (e a AWS por baixo) tratam dados em seu nome. Leia o DPA e os termos de cada um; as certificações de conformidade (SOC 2, ISO 27001, GDPR, HIPAA) estão listadas pelo Neon só no plano Scale, e suporte técnico e backups deles podem envolver acesso e países fora do Brasil.
- **Branches e restaurações** duplicam os dados dentro do mesmo projeto Neon; apague as de ensaio.
- **Nenhuma inferência sai do app.** Com `EXTRACTION_USE_LLM=false` não há modelo, nem local nem remoto; `AI_ALLOW_REMOTE_INFERENCE` fica sem uso.
- **O PDF.** Passa pela memória da função e fica em `/tmp` (0600) só durante a leitura; é apagado ao final, com sucesso ou falha. A Vercel guarda metadados da requisição (caminho, IP, tempo), não o corpo. O app **não** registra valores de exame em log.
- **Gravatar.** A barra lateral ainda pede o avatar ao gravatar.com com o hash do e-mail (limitação já registrada no README).
- **Base legal (não é aconselhamento jurídico).** Dado de saúde é dado pessoal **sensível** (LGPD, art. 5º, II, e art. 11). O tratamento por pessoa natural para fins exclusivamente particulares e não econômicos está fora da lei (art. 4º, I); se você guarda dados de **outras pessoas da família**, combine com elas e registre o consentimento.
- **Apagar.** Apagar um laudo no app apaga os resultados; apagar o projeto Neon apaga o banco (recuperável por 7 dias). Os `.dump` locais precisam ser apagados por você. Contas não têm exclusão pela API (de propósito): desative.

## 16. Limitações conhecidas

- **Sem tempo real.** Sem WebSocket; as telas usam polling de 5 s. No upload, a resposta já traz o laudo lido.
- **Upload de até 4 MB** (limite da Vercel: 4,5 MB no corpo). Laudos maiores: reduza o PDF ou leia na máquina (`import_reports` apontando para a nuvem).
- **Tempo da função:** 60 s (`backend/vercel.json`); o teto do plano Hobby é 300 s. Laudo de muitas páginas pode estourar; ajuste `EXAMS_MAX_PAGES`.
- **Partida a frio:** a primeira requisição depois de um tempo parado leva alguns segundos (Python + Django + o compute do Neon acordando). O Fluid compute reduz, não elimina.
- **Sem PDF escaneado:** sem modelo, um PDF só de imagem falha com `llm_disabled`.
- **Laudo preso:** se a função for cortada no meio da leitura, o laudo fica em "Extraindo" até o próximo upload varrer (ou até você apagá-lo).
- **Previews do frontend não falam com a API** (CORS exato; seção 10).
- **Armazenamento de 1 GB e janela de 6 h** no plano gratuito: acompanhe o tamanho e faça o seu backup (seção 12).
- **Conexão por requisição:** em modo transação o app não reaproveita conexões (`CONN_MAX_AGE=0`); cada requisição abre uma nova com o pooler (TLS + SCRAM). Funciona; é um ponto de ajuste possível (a documentação do Neon aceita `CONN_MAX_AGE` maior com `CONN_HEALTH_CHECKS`).
- **Ordenação de textos:** o banco do Neon usa a collation `C.UTF-8` (por ponto de código); nomes com acento ordenam depois dos sem acento onde o app ordena por texto.
- **Throttling no banco:** cada requisição autenticada faz algumas consultas à tabela `django_cache`. É o custo de não ter Redis.
- **Cabeçalhos de segurança do frontend** (CSP, X-Frame-Options) não estão configurados: a API já manda os dela. Se quiser, adicione `headers` em `frontend/vercel.json` e valide após o deploy.

## 17. Testar localmente "como se fosse a nuvem"

Usa um Postgres 17 **descartável num contêiner próprio** (outra porta, sem tocar no `myexams-db-1` nem no banco `myexams` de desenvolvimento) e papéis criados por SQL, como no Neon.

```bash
# 1) Postgres 17 descartável + banco, papel do app e extensão (como o dono faria no SQL Editor)
docker run -d --rm --name myexams-neon-scratch -e POSTGRES_PASSWORD=scratch-admin -p 127.0.0.1:54399:5432 postgres:17-alpine
sleep 5
P="docker exec -i myexams-neon-scratch psql -U postgres -v ON_ERROR_STOP=1 -q"
$P -c "CREATE DATABASE neon_scratch" -c "CREATE ROLE myexams_app LOGIN PASSWORD 'scratch-pass-123'"
$P -d neon_scratch -c "GRANT CREATE ON SCHEMA public TO myexams_app" -c "CREATE EXTENSION pg_trgm"

# 2) Arquivo de ambiente descartável (não toca em um backend/.env.cloud de verdade que você já tenha)
export EXAMS_CLOUD_ENV_FILE=/tmp/myexams-scratch.env.cloud
cat > "$EXAMS_CLOUD_ENV_FILE" <<'EOF'
DATABASE_MIGRATE_URL=postgresql://myexams_app:scratch-pass-123@localhost:54399/neon_scratch
DATABASE_CONNECT_TIMEOUT=10
DATABASE_CONNECT_RETRIES=1
DJANGO_CACHE=db
CELERY_TASK_ALWAYS_EAGER=true
DJANGO_CHANNEL_LAYER=none
EXTRACTION_USE_LLM=false
EOF
mise run cloud-migrate -- --yes
mise run cloud-manage -- createsuperuser --username teste@example.com --email teste@example.com

# 3) API "da nuvem" na porta 8101, simulando o pooler de transação (sem opções de inicialização)
cd backend && DATABASE_URL='postgresql://myexams_app:scratch-pass-123@localhost:54399/neon_scratch' \
  DATABASE_POOLER=transaction DJANGO_CACHE=db CELERY_TASK_ALWAYS_EAGER=true \
  DJANGO_CHANNEL_LAYER=none EXTRACTION_USE_LLM=false EXAMS_UPLOAD_DIR=/tmp/myexams-uploads \
  EXAMS_MAX_UPLOAD_MB=4 DJANGO_CORS_ALLOWED_ORIGINS=http://localhost:3101 \
  uv run python manage.py runserver 8101 --noreload

# 4) Frontend na porta 3101, sem WebSocket
cd frontend && NUXT_PUBLIC_API_BASE_URL=http://localhost:8101/api/v1 \
  NUXT_PUBLIC_GRAPHQL_ENDPOINT=http://localhost:8101/graphql/ NUXT_PUBLIC_REALTIME=false \
  bun x nuxt dev --port 3101

# 5) Backup de ponta a ponta contra o contêiner (a rede dele é compartilhada; sem SSL, só aqui)
printf 'DATABASE_MIGRATE_URL=postgresql://myexams_app:scratch-pass-123@localhost:5432/neon_scratch?sslmode=disable\n' > /tmp/myexams-scratch-backup.env
EXAMS_CLOUD_ENV_FILE=/tmp/myexams-scratch-backup.env CLOUD_BACKUP_DOCKER_ARGS="--network=container:myexams-neon-scratch" \
  sh scripts/cloud-backup.sh
rm fixtures/private/db-backups/cloud-*.dump   # só os de teste!

# 6) Limpeza
rm -f "$EXAMS_CLOUD_ENV_FILE" /tmp/myexams-scratch-backup.env; unset EXAMS_CLOUD_ENV_FILE
docker stop myexams-neon-scratch
```

(Os testes automáticos dessa configuração estão em `backend/apps/exams/tests/`: `test_deploy_settings.py`, `test_cloud_profile.py`,
`test_db_backend.py`, `test_dbschema.py`, `test_pgenv.py`, `test_cloud_backup_script.py` e `test_inline_mode.py`.)

## 18. Referência: variáveis novas

Backend (todas opcionais; sem elas o app roda como no desenvolvimento):

| Variável | Padrão | O que faz |
|---|---|---|
| `DATABASE_URL` | vazio (usa `POSTGRES_*`) | URL `postgresql://...` do banco; credenciais decodificadas; `sslmode` padrão `require` fora de localhost; `sslmode`, `channel_binding`, `sslrootcert`, `sslnegotiation`, `connect_timeout`, `application_name`, `options` e `target_session_attrs` da URL vão para o libpq |
| `DATABASE_MIGRATE_URL` | vazio | Só `mise run cloud-*`: a string **direta** (recusa `-pooler`) |
| `DATABASE_POOLER` / `DATABASE_MIGRATE_POOLER` | detecta (`-pooler` no host = transação) | `direct` ou `transaction` |
| `DATABASE_CONN_MAX_AGE` | `0` | Reuso de conexão (ignorado em modo transação; acima de 0 liga `CONN_HEALTH_CHECKS`) |
| `DATABASE_CONNECT_TIMEOUT` | `10` com banco remoto, `0` (nenhum) no local | Segundos para conectar (`connect_timeout` do libpq; a URL pode trazer o seu) |
| `DATABASE_CONNECT_RETRIES` | `1` com banco remoto, `0` no local | Novas tentativas só ao ABRIR a conexão (`config/db_backend`); 1 s de pausa |
| `DATABASE_SCHEMA` | vazio (`public`) | Esquema próprio (opcional); `search_path = <esquema>,public` |
| `CLOUD_PG_MAJOR` | `18` | Só `cloud-backup` (no `.env.cloud`): versão do cliente `pg_dump` no Docker |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Modo inline (sem broker; canais em memória se não houver `REDIS_URL`) |
| `DJANGO_CACHE` | `redis` | `redis`, `db` ou `locmem` |
| `DJANGO_CHANNEL_LAYER` | `redis` (`memory` em inline sem `REDIS_URL`) | `redis`, `memory` ou `none` |
| `DJANGO_CORS_ALLOWED_ORIGINS` | `http://localhost:3100` | Origens do frontend (vírgulas) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | vazio | Origens confiáveis do CSRF |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | `false` | Confia em `X-Forwarded-Proto` |
| `DJANGO_SECURE_SSL_REDIRECT` | `false` | Redireciona HTTP para HTTPS |
| `DJANGO_SECURE_HSTS_SECONDS` / `_INCLUDE_SUBDOMAINS` / `_PRELOAD` | `0` / `false` / `false` | HSTS |
| `DJANGO_SESSION_COOKIE_SECURE` / `DJANGO_CSRF_COOKIE_SECURE` | `false` | Cookies só por HTTPS |
| `DJANGO_ADMIN_ENABLED` | `true` | `false` remove o `/admin/` e o app `django.contrib.admin` |
| `DRF_BROWSABLE_API` | `true` | `false` = só JSON |
| `DJANGO_AUTO_MIGRATE` | `true` (`false` na Vercel) | Migra ao subir o servidor |
| `DJANGO_LOG_PLAIN` | `false` (`true` na Vercel) | Log em texto puro |
| `EXAMS_CLOUD_MIGRATE` / `EXAMS_CLOUD_ENV_FILE` | — | Definidas pelas tarefas `cloud-*` (lê `backend/.env.cloud`) |
| `EXAMS_MAX_UPLOAD_MB`, `EXAMS_UPLOAD_DIR`, `EXAMS_ORPHAN_HOURS`, `EXAMS_MAX_PAGES`, `DRF_NUM_PROXIES` | já existiam | Valores da nuvem na seção 6 |

Frontend (build): `NUXT_PUBLIC_API_BASE_URL`, `NUXT_PUBLIC_GRAPHQL_ENDPOINT` (já existiam), `NUXT_PUBLIC_REALTIME` (padrão
`true`), `NUXT_PUBLIC_WS_BASE_URL` (padrão vazio: derivada da API) e `NUXT_PUBLIC_MAX_UPLOAD_MB` (padrão desconhecido).

Tarefas do `mise`: `cloud-migrate`, `cloud-manage`, `cloud-backup` e `backend-requirements` (regenera `backend/requirements.txt`
a partir do `uv.lock`; um teste falha se ele ficar desatualizado).

## 19. Integração Neon–Vercel (opcional)

O Neon tem duas integrações com a Vercel. Este guia usa a **configuração manual** (você cria o projeto Neon, escolhe região, versão e papéis, e cola as strings na Vercel). A integração é uma alternativa, não um requisito, e **nenhum código depende dela**.

A integração *gerenciada pela Vercel* (Marketplace > Neon > Install) cria o projeto Neon dentro de uma organização `Vercel: <time>`, cobra tudo na fatura da Vercel e injeta no projeto da Vercel:

| Variável injetada | O que é | Como entra no MyExams |
|---|---|---|
| `DATABASE_URL` | String **com pooler** (PgBouncer) | Já tem o nome certo: o `-pooler` do host liga o modo transação sozinho |
| `DATABASE_URL_UNPOOLED` | String direta | Copie para `backend/.env.cloud` como `DATABASE_MIGRATE_URL` (a Vercel não deve ter essa variável) |
| `PGHOST`, `PGHOST_UNPOOLED`, `PGUSER`, `PGDATABASE`, `PGPASSWORD`, `POSTGRES_*` | Peças da conexão (legado) | Ignoradas: `DATABASE_URL` vence as `POSTGRES_*` |

Pesos a considerar: a conta fica atrelada à Vercel (não há `neon login`; só chave de API), o papel é o `neondb_owner` (o papel de menor privilégio da seção 4 precisaria ser criado e a variável trocada à mão) e o **Preview Branching** cria uma branch do Neon, **com cópia dos dados**, para cada deploy de preview: deixe desligado (previews do backend não são alcançáveis pelo frontend, ver seção 10, e cada branch duplica dado de saúde). Não foi verificado quais parâmetros (`sslmode`, `channel_binding`) a `DATABASE_URL` injetada traz; o app adiciona `sslmode=require` sozinho para qualquer host remoto.

## 20. O que não foi verificado

Tudo abaixo precisa de conta real; foi preparado e conferido o que dá localmente (testes automáticos, Postgres 17 descartável com papéis criados por SQL, `pg_trgm` com e sem esquema próprio, simulação do pooler sem `options`, retentativa de conexão contra uma porta fechada, `cloud-backup` e restauração de ponta a ponta contra o contêiner, `check --deploy`, build do frontend, instalação só com dependências de produção):

- O build e o deploy reais na Vercel (detecção do `requirements.txt` x `pyproject.toml`, `tool.vercel.entrypoint`, `excludeFiles`, `headers` do `vercel.json`).
- A conexão real pelo PgBouncer do Neon: `channel_binding=require` **na string com pooler** (a documentação mostra a opção nas strings diretas; se a autenticação falhar só ali, remova-a), o `ALTER ROLE ... SET search_path` valendo no pooler (a documentação diz que sim; só vale se você usar o esquema próprio) e o papel `myexams_app` criado por SQL com as permissões do Neon (testado em Postgres comum).
- `sslmode=verify-full&sslrootcert=system` dentro da função da Vercel (depende do repositório de certificados do ambiente de execução).
- A latência real de despertar do compute (centenas de ms segundo a documentação) e São Paulo -> São Paulo; o comportamento de uma consulta que cai exatamente numa manutenção.
- Que o plano gratuito não pausa nem apaga o projeto por inatividade (não encontrei regra nenhuma para a AWS na documentação lida; confira os termos).
- `pg_dump` do Postgres 17 contra o servidor Neon real (testado contra um Postgres 17 comum) e a janela real de PITR/snapshot no Console.
- A versão do Postgres que o Neon oferece como padrão ao criar o projeto, e o tamanho do seu banco contra 1 GB.
- Que `X-Forwarded-Proto` chega como `https` (o `DJANGO_SECURE_SSL_REDIRECT=true` pode dar laço de redirecionamento se não chegar: nesse caso desligue-o; a Vercel já força HTTPS na borda).
- Deep links do frontend no ambiente real (conferido localmente com o mesmo renderizador de SPA do Nitro).
- A ordenação dos nomes com a collation `C.UTF-8` do Neon nas telas do app.

## 21. Ordem dos passos manuais (resumo)

1. Criar o projeto Neon (`aws-sa-east-1`, Postgres 18), conferir que o Data API está desligado, ajustar o compute, e rodar o SQL do papel `myexams_app` e da extensão (seção 4).
2. Copiar as duas strings (com pooler e direta); preencher `backend/.env.cloud` (seção 5).
3. `mise run cloud-migrate -- --yes`; criar o usuário e o administrador; `mise run cloud-backup` (seção 8).
4. Criar o projeto Vercel B (`backend`) com as variáveis da seção 6; conferir `/health/`.
5. Criar o projeto Vercel A (`frontend`) com as variáveis da seção 7.
6. Ajustar CORS/CSRF/hosts e domínios na ordem da seção 10; subir o HSTS por último.
7. Ligar a proteção de previews (seção 11) e rodar o checklist (seção 14).
8. Trazer os dados (seção 9), fazer o `cloud-backup` e guardar uma cópia cifrada fora do computador (seção 12).
