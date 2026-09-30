# Changelog — Transformativa Revenue Engine

Formato exigido pelo baseline (doc 10 §7): **Added**, **Changed**, **Fixed**, **Deprecated**, **Removed**,
**Security**. Uma linha por mudança relevante, com o card que a produziu.

## [W0 — Governança e Baseline] — 29/09/2026

### Added

- **Data Contract V1.0 congelado** (`TRE-W0-E03-T01`) — schema `sales_intelligence` com 12 tabelas, IDs
  canônicos, ownership (source of truth), envelope de eventos, vocabulários fechados e score model:
  - `db/migrations/0001_sales_intelligence_v1.sql` — DDL das 12 tabelas + 16 índices mínimos (especificação
    congelada; **não aplicada** em nenhum ambiente naquele momento — aplicada em **dev** em 30/09/2026,
    ver a seção W1);
  - `docs/data/DATA_CONTRACT_V1.md` — contrato legível, com ER, deduplicação, compliance e governança da
    mudança;
  - `docs/data/data_contract_v1.json` — contrato legível por máquina;
  - `scripts/verificar_contrato_dados.py` — verificador de não divergência entre os três artefatos
    (26 itens; teste negativo com 6 mutações: todas detectadas).
- **Estrutura do repositório** (`TRE-W0-E01-T01`) — 23 diretórios do doc 10, `BRANCHING.md`, `.env.example`
  sem segredo, 6 ADRs iniciais, `scripts/verificar_estrutura.sh` e `scripts/secret_scan.sh`.
- **Política de gestão de secrets V1** (`TRE-W0-E01-T02`) — princípios, locais dos segredos, matriz de
  segregação Dev Harness × Sales AI, gatilhos de rotação, hook `pre-commit` e teste negativo (segredo
  plantado é bloqueado). Homologada pelo operador humano em 29/09/2026.
- **Separação Hermes Dev Harness × Sales AI** (`TRE-W0-E02-T01`) — políticas versionadas por papel
  (`hermes/policies/`), matriz de permissões e credenciais, e `scripts/verificar_papeis.sh` (13 itens).
  Homologada em 29/09/2026.
- **`docs/operations/registro-de-aprovacoes.md`** — registro obrigatório de aprovação humana (quem, quando,
  o quê, evidência), primeira linha preenchida pela homologação das duas matrizes.
- **ADR-0007** — ambientes e backup: PostgreSQL/Odoo/vetor em VPS dedicada, backup automatizado com restore
  testado e destino em storage de objeto externo.
- **`CHANGELOG.md`** (este arquivo) e **`docs/runbooks/provisionamento-contabo.md`**.

### Security

- Segredo nunca é versionado: hook `pre-commit` bloqueia padrão de token/chave e o repositório é privado
  (`TRE-W0-E01-T02`).

### Notas de estado

- **W0 não está concluído:** faltam `TRE-W0-E01-T03` (backup/rollback testável — bloqueado até o
  provisionamento da VPS Contabo) e os quatro cards de JEV (`TRE-W0-E04-*`).
- Nada foi aplicado em produção; nenhuma DDL nasce em produção (ADR-005).

## [W1 — PostgreSQL] — 30/09/2026

### Added

- **Schema `sales_intelligence` aplicado no ambiente dev** (`TRE-W1-E01-T01`) — primeira aplicação real do
  Data Contract V1.0, na VPS do TRE (Contabo), container `pg-sales-dev`, banco `sales_intelligence`:
  - `scripts/db/aplicar_migracoes.sh` — runner de migrations versionado: ordem lexicográfica,
    idempotente por sha256, migration aplicada é imutável, rastro em `public.tre_schema_migrations`,
    aplicação por `docker cp` + `psql -f` (não depende de stdin) e **recusa de produção** (ADR-005:
    exige `TRE_APROVACAO_HUMANA` e homologação com as versões registradas);
  - `scripts/db/estado_do_ambiente.sh` — relatório read-only (tabelas, índices, `psql \dt`, controle);
  - `deploy/environments/dev.env` — par ambiente→container/usuário/banco, **sem segredo**; variável de
    ambiente do operador tem precedência sobre o arquivo;
  - `docs/runbooks/aplicar-migracoes.md` — runbook de aplicação, guardrail de produção e rollback.
- **Modo `--banco` no verificador do contrato** (`TRE-W1-E01-T01`) — `scripts/verificar_contrato_dados.py`
  passa a conferir também o **schema real do ambiente** (schema, tabelas, colunas/tipos/NOT NULL, PK, FK,
  vínculos lógicos sem FK e os 30 índices), por leitura em `information_schema`/`pg_indexes`; o modo atual
  (arquivos do repo) continua idêntico e passando.

### Fixed

- **Runner: precedência de configuração e stdin** (`TRE-W1-E01-T01`, defeito achado por teste no mesmo card)
  — o arquivo versionado sobrescrevia a variável do operador e o `docker exec -i` consumia o stdin de quem
  orquestra por SSH (o script remoto morria no meio). Corrigido: variável vence o arquivo; migration entra
  no container por `docker cp`; ambiente dev reparado pelo próprio plano de rollback (drop + reaplicação).
- **Verificador: exemplo de `--banco` prescrevia o padrão proibido** (`TRE-W1-E01-T01-D03`, defeito medido
  na verificação independente do D01) — o help/docstring exemplificava `--banco 'docker exec -i …'`: a
  mesma forma que consome o stdin de quem orquestra por `ssh … 'bash -s'` e mata o script remoto em
  silêncio (sem erro visível e deixando container descartável órfão). Corrigido: exemplos passam a
  `docker exec <container> psql …` (sem `-i`) e `verificar_banco()` roda o psql com
  `stdin=subprocess.DEVNULL`, ficando imune a qualquer prefixo com `-i`.

### Notas de estado

- **Evidência medida (dev):** `12 tabelas | 30 índices`, `psql \dt` com as 12 tabelas do contrato e
  `verificar_contrato_dados.py --banco …` → `PASS (37 itens)`, exit 0; idempotência comprovada
  (`PULADO`); provas negativas em container descartável (tabela removida, coluna inventada, índice
  removido, migration editada depois de aplicada) reprovam com exit 1.
- **Produção não foi tocada:** o runner recusa `prod`; `docker ps -a` na VPS só tem `pg-sales-dev` e as
  árvores `/opt/tre/{prod,homolog}` seguem sem arquivo.
- **Divergência registrada (decisão do dono):** o Data Contract V1.0 declara o banco de inteligência como
  `transformativa_ai`; o ambiente dev provisionado usa banco `sales_intelligence` (container
  `pg-sales-dev`, usuário `sales_ai`) — nome que os scripts de backup já assumem como padrão.
