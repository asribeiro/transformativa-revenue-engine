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
- **Massa mínima de smoke aplicada e verificada em dev** (`TRE-W1-E02-T01`) — as 12 tabelas core deixaram de
  estar vazias e passaram a ter contagem conferível:
  - `scripts/db/aplicar_fixture_smoke.sh` — aplica `db/fixtures/smoke_dev.sql` no ambiente por
    `docker cp` + `psql -f` (sem stdin), com precedência para a variável do operador, espera robusta do
    PostgreSQL (duas vezes) e **recusa de produção** (ADR-005);
  - `scripts/db/verificar_fixture_smoke.sh` — confere a contagem por tabela contra o **esperado do próprio
    fixture**, obtido de uma linha de base aplicada em container descartável (constante à mão envelheceria e
    viraria carimbo); só leitura no alvo;
  - `scripts/db/teste-fixture-smoke.sh` — prova em container descartável que a conferência tem dente: linha a
    mais e linha a menos **reprovam**, a reaplicação do fixture restaura a contagem;
  - `db/fixtures/smoke_dev_rollback.sql` — rollback da massa (filhas antes das pais), exercitado de verdade;
  - `docs/runbooks/massa-de-smoke-dev.md` — runbook da massa (aplicação, conferência, teste negativo, rollback).
- **Constraints e índices do contrato conferidos item a item** (`TRE-W1-E03-T01`) — os 30 índices e as
  constraints (PK/FK/UNIQUE) da migration 0001 passaram a ter conferência **nome a nome e coluna a coluna**
  contra o ambiente real, com prova negativa:
  - `scripts/db/verificar_constraints_indices.py` — verificador **read-only**: deriva o esperado da própria
    migration e do `data_contract_v1.json` (12 PK `<tabela>_pkey` + 15 `CREATE INDEX` nomeados + 3 UNIQUE
    inline = 30) e compara com o `pg_indexes`/`pg_constraint` do alvo: conjunto exato de índices (nome,
    tabela, unicidade e colunas com direção `DESC`), os 16 itens de índice do contrato, PK (uma por tabela,
    coluna `id`), FK (par tabela.coluna → tabela.coluna) e UNIQUE (tabela + colunas), item a item.
    `--esperado` imprime o esperado sem tocar banco; sem `--banco` o script sai 2 (fail-closed);
  - `scripts/db/teste-constraints-indices.sh` — prova em container descartável que o verificador tem dente:
    sete mutações (índice removido, índice renomeado, mesmo nome com coluna errada, FK removida, UNIQUE
    removida, PK removida, índice a mais) **reprovam apontando o motivo**, e cada mutação desfeita volta a
    aprovar (`TESTE_OK`);
  - `docs/runbooks/aplicar-migracoes.md` §8 — uso, evidência medida e escopo da conferência.
- **Deduplicação de empresa por identificadores fortes** (`TRE-W1-E04-T01`) — o contrato (seção 5) virou
  código executável no ambiente dev, com merge auditável e fila humana:
  - `scripts/dedup/deduplicar_organizacoes.py` — motor: normaliza e compara **CNPJ → domínio → LinkedIn
    Company URL** (prioridade do contrato) e o fraco **nome + cidade**; decide `MERGE` / `REVIEW_REQUIRED` /
    `SEM_DUPLICIDADE`; executa o merge (reaponta as tabelas filhas, soft-delete no duplicado em
    `deleted_at`) e grava a auditoria em `sync_events`; registra a pendência humana em `human_approvals`
    e sabe **desfazer** o merge (`--desfazer-merge`, registro `UNMERGE`);
  - `scripts/dedup/teste_dedup_sintetico.sh` — casos sintéticos (0,94/0,95, cada forte isolado e em
    conjunto, negativos, auditoria, governança do limiar) **e** prova negativa: quatro sabotagens do alvo
    têm de reprovar a suíte;
  - `scripts/dedup/teste_dedup_ambiente.sh` — cenário real no ambiente (semeia massa marcada, mede
    detecção/decisão/merge/auditoria/fila/rollback e limpa, provando que a contagem por tabela volta ao
    estado anterior);
  - `docs/runbooks/deduplicacao-strong-identifiers.md` — runbook do motor, da governança do limiar e do
    rollback de dado já mesclado.
  Decisões registradas: o limiar é **lido do contrato** a cada chamada (não há ajuste por código de
  produção, variável de ambiente ou parâmetro); evidência fraca nunca alcança a faixa de merge (teto
  `limiar − 0,01` = 0,94) e vai para revisão; CNPJ igual porém inválido detecta e vai para revisão;
  nenhuma coluna/tabela nova (criar exigiria nova versão do contrato).
- **Campo `entity_match_confidence` calculado e persistido** (`TRE-W1-E04-T02`) — o score da decisão de merge
  deixou de ser um número em memória e passou a ter nome canônico, faixa declarada e registro persistido:
  - `scripts/dedup/deduplicar_organizacoes.py` — modelo de **faixas derivadas do contrato**
    (`MERGE_AUTOMATICO [0,95; 1]`, `REVISAO_HUMANA [0,80; 0,95)`, `SEM_DUPLICIDADE [0; 0,80)`; cobre `[0,1]`
    sem lacuna/sobreposição, validador próprio e recusa de operar com contrato incompatível); o score é
    calculado por evidência (forte válido 1,00; forte inválido 0,94; fraco qualificado `min(similaridade,
    0,94)`; sem evidência qualificada **0,00**) e a **decisão do par é a decisão da faixa do score**;
    `--faixas` imprime o modelo com a origem das fronteiras;
  - persistência no registro auditado que o contrato já governa (`sync_events.request_payload` no merge e
    `human_approvals.proposed_action` na fila humana) com `entity_match_confidence`, `..._faixa`,
    `..._modelo` e a tabela de faixas vigente — **sem coluna nova** (coluna exigiria nova versão do contrato
    + aprovação humana); o nome `confianca` do E04-T01 fica no mesmo registro como alias de mesmo valor;
  - `scripts/dedup/teste_entity_match_confidence.sh` — prova em um comando: faixas impressas, suíte do motor,
    **prova negativa** (sabotar `persistencia` / `coerencia` / `limiar` tem de reprovar) e cenário real em dev
    com o campo **lido de volta do banco** nos dois registros;
  - `docs/data/entity-match-confidence.md` — modelo, faixas, onde persiste, decisões D-T02-1..5, rollback e
    limites declarados.
  Decisões registradas: nome canônico com alias de compatibilidade; score sem evidência qualificada é 0,00 e
  não a similaridade bruta (que segue auditável em `evidencias.fracos`); sem coluna em `organizations` (o
  score é do par, não atributo solto da empresa). Desvio declarado: o rollback proposto no card ("coluna fica
  nula") não se aplica — não existe coluna e a trilha de auditoria é imutável (contrato §9).

- **Modo "ambiente real" no driver de teste de backup/restore** (`TRE-W1-E06-T01`) —
  `scripts/backup/teste-backup-restore.sh --ambiente dev|homolog`: faz o backup do banco do **ambiente**
  (não de um container descartável), confere `sha256` do dump contra o manifesto, exige
  `externo: enviado` no manifesto e confere que o container do ambiente não foi tocado. O modo
  descartável (padrão) segue intacto e continua `TESTE_OK`.
- **Suíte de teste do banco** (`TRE-W1-E05-T01`) — um comando único por ambiente
  (`bash scripts/db/suite_banco.sh dev`), com **exit code como resposta** e veredito de três valores:
  `0 SUITE_OK` / `1 SUITE_FALHOU` / `3 SUITE_NAO_TESTAVEL` (não é verde) — mais `2` para uso errado:
  - `scripts/db/suite_banco.sh` — reúne as etapas numa execução: ambiente (identidade do alvo, estado do
    schema e sha da migration registrada × arquivo do repo), contrato (`verificar_contrato_dados.py
    --banco`), constraints/índices (`verificar_constraints_indices.py --banco`), dedup sintético e dedup
    no ambiente (motor do E04, com cenário real e limpeza conferida) e tenant/RLS. Guarda de
    confiabilidade: etapa **sem linha `RESULTADO:`** ou com **0 item executado** reprova a suíte
    ("sem output" nunca é verde). `--somente-leitura` não escreve no alvo (etapa de dedup vira varredura
    `--detectar`) e é o modo permitido em `prod`; sem ele, `prod` é recusado (ADR-005);
  - `scripts/db/teste_tenant_rls.sh` — isolamento entre clientes: mede dimensão de cliente/tenant, RLS
    (`pg_class.relrowsecurity`, `pg_policies`) e o papel da aplicação (`rolsuper`, `rolbypassrls`, dono de
    tabela com RLS sem `FORCE`); como o papel da aplicação, prova que a consulta **sem filtro** devolve
    vazio ou erro na sessão sem cliente e **0 linha de outro cliente** na sessão com cliente X. Sem
    dimensão de cliente o critério é indecidível e a suíte diz isso com **exit 3**, nunca com verde;
  - `--prova-de-dente` (nos dois scripts) — provas em container **descartável**: a suíte reprova alvo com
    coluna removida e com índice a mais (`SUITE_DENTE_OK`, 14 itens), e o teste de tenant reprova policy
    permissiva (`USING (true)`), `BYPASSRLS` no papel da aplicação e `DISABLE ROW LEVEL SECURITY`, voltando
    a aprovar quando cada mutação é desfeita (`TENANT_RLS_DENTE_OK`, 18 itens);
  - `docs/runbooks/suite-de-teste-do-banco.md` — runbook do comando, do vocabulário de exit, das etapas e
    dos limites conhecidos.
  **Critério de tenant/RLS não é provável contra o Data Contract V1.0** (medido em dev: 0 coluna de
  cliente/tenant nas 12 tabelas, RLS desabilitada nas 12, 0 policy, papel `sales_ai` com `superuser=true`
  e `bypassrls=true`): a suíte fecha em `SUITE_NAO_TESTAVEL` para esse critério e ele volta ao Analista de
  Requisitos — nenhum verde foi declarado sem essa prova.

### Changed

- **AC2 do E05 passou a ser medido na forma reformulada — "não existem dois clientes no mesmo banco"**
  (`TRE-W1-E05-T01`, decisão do dono `A` de 30/09/2026, card `t_e340c29b`; isolamento **físico**, um banco
  por cliente) — a etapa 5 da suíte deixa de ser `tenant/RLS` (forma antiga: "consulta sem filtro de tenant
  devolve vazio ou erro", **não decidível** contra o Data Contract V1.0, que não tem dimensão de cliente) e
  passa a medir o que é medível no ambiente atual: **0 dimensão de cliente/tenant no schema**, **1 base de
  aplicação na instância do alvo** e **1 base provisionada (`pg-*`) servindo o schema no host**. A barreira
  do critério passa a ser de **provisionamento**, não de schema — declarado em
  `docs/data/DATA_CONTRACT_V1.md` e em `docs/runbooks/suite-de-teste-do-banco.md` §4/§8. O medidor é
  `scripts/db/teste_isolamento_clientes.sh` (novo, com `--prova-de-dente`); `scripts/db/teste_tenant_rls.sh`
  fica **versionado como instrumento do V2** (não wired na suíte) para o dia em que houver multi-cliente no
  mesmo banco. `scripts/verificar_estrutura.sh` passa a exigir o artefato novo (versionado e executável).
- **Régua de aceite do E05 passou a registrar a reformulação do AC2** (`TRE-W1-E05-T01`, 30/09/2026) — a
  revisão independente mostrou que `docs/kanban/criterios-de-aceitacao.md` continuava com a forma antiga do
  2º critério ("consulta sem filtro de tenant"), **sem nota**, enquanto o entregue media a forma nova: pelo
  documento que governa o fechamento (*"card cujo critério não bater não fecha"*), o entregue não batia com o
  critério homologado. A seção `TRE-W1-E05-T01` da régua ganhou **nota datada** com a decisão do dono (opção
  A, card `t_e340c29b`, `docs/operations/registro-de-aprovacoes.md`) e o **texto vigente** — "não existem
  dois clientes no mesmo banco" —, declarando ainda que a forma antiga foi medida e devolvida ao requisito
  (`NAO_TESTAVEL`, exit 3). Junto: `scripts/db/teste_tenant_rls.sh` (instrumento do V2) passou a usar a
  **mesma superfície de detector** do teste vigente (`~*`, token `tenant|cliente|client` em qualquer
  posição), para não haver duas definições de "coluna de cliente" no repo.

### Fixed

- **Teste de isolamento entre clientes: catálogo mudo virava "0 coluna" (verde falso) e o detector só via
  nomes terminando em `tenant|cliente|client`** (`TRE-W1-E05-T01`, os 2 itens de medição da revisão
  independente) — medido por ela em alvo descartável e **reproduzido aqui nos dois artefatos, lado a lado**:
  com `organizations.tenant_uuid` presente, o artefato anterior imprimia `dimensao de cliente/tenant no
  schema: 0` e fechava `ISOLAMENTO_OK (5 itens)`, **exit 0**; com a leitura do catálogo falhando, **exit 0**
  também (verde falso). Correções: `leitura()` passou a devolver falha (exit != 0) e o item 3 exige **número**
   — leitura vazia/erro vira `NAO_TESTAVEL` (exit 3, nunca verde) com a causa impressa (no item 4, leitura que
  falha **reprova**); o detector passou a cobrir o token em qualquer posição, **case-insensitive**
  (`tenant_uuid`, `conta_Cliente`), e a superfície que fica **fora** dele (outra grafia, ex. `customer_id`)
  está declarada no runbook §8 — quem a pega é a **etapa 1** (contrato, `sobram=[...]`). O dente do AC2 ganhou
  3 casos novos (2 grafias de co-locação + catálogo ilegível): `ISOLAMENTO_DENTE_OK (26 itens, 0 falhas)`,
  exit 0, contra `17 itens` antes. Sem falso positivo no contrato: **0** colunas casam na base dev (as 203
  colunas do schema foram conferidas).
- **Veredito do item 5 (provisionamento) dizia mais do que a medição cobria** (`TRE-W1-E05-T01`, 3º item da
  revisão independente) — o item mede `docker ps` filtrando a **convenção de nome `pg-*`**, mas o texto
  afirmava "o provisionamento nao co-loca clientes". O texto passou a declarar exatamente o que é medido
  (uma base pela convenção, nenhuma **segunda** base provisionada; provisionamento fora da convenção **não é
  medido** por este item) e a saída ganhou linha **informativa** com os containers de pé fora da convenção que
  servem o schema — nunca escondidos, nunca contados. Medido no dente: um container fora da convenção
  (`e05r3-probe`) servindo o schema aparece no informativo e o item permanece `OK`. Limite documentado no
  runbook §8.
- **`--faixas` se contradizia quando o limiar do contrato não era 0,95** (`TRE-W1-E04-T02-D02`, defeito medido
  na revisão independente do T02) — a linha de detalhe do comando tinha os **nomes das faixas fixos no código**
  enquanto as decisões eram calculadas: com o contrato em 0,90 o próprio comando imprimia
  `MERGE_AUTOMATICO [0.90, 1.00] -> MERGE` na tabela e "0,94 cai em REVISAO_HUMANA" na linha de detalhe, com
  exit 0 — o artefato que prova o critério 2 se contradizendo. Corrigido extraindo `linha_detalhe_faixas()`,
  derivada de `faixa_de_confianca()` (a mesma fonte que decide o merge). O teste do projeto deixou de asserir
  a string constante (que era a evidência do critério) e passa a comparar com a saída do **próprio modelo**,
  inclusive numa cópia com o limiar em 0,90, onde tabela e linha de detalhe têm de se mover juntas; a suíte
  ganhou a sabotagem `detalhe` (`--sabotar detalhe` → `TESTE_FALHOU`, exit 1) para o item novo não nascer sem
  prova de que reprova.
- **Suíte de banco: linha do `RESUMO` mentia sobre a etapa `ambiente`** (`TRE-W1-E05-T01` — defeito achado
  pelo card de D01/`t_39838c5b`, que reproduziu a etapa reprovando com o resumo imprimindo
  `ambiente ............. OK`) — a linha era **texto fixo** e não refletia o veredito contado: quem lesse só
  o resumo (ou o resumo de um log grande) concluiria "ambiente OK" com a suíte falhando por causa daquela
  etapa. A linha agora sai do que foi contado (`FALHOU (N itens)` quando há reprovação na etapa) e entrou uma
  **guarda de consistência do próprio resumo** (o número de linhas `FALHOU` no resumo tem de cobrir as
  etapas reprovadas; desvio reprova a suíte). A guarda tem prova negativa: com o registro da migration
  divergido no alvo descartável, a suíte sai com exit 1, aponta a etapa e o resumo **não** pode dizer `OK` —
  `SUITE_DENTE_OK (19 itens, 0 falhas)`.

- **Log da migração em caminho fixo `/tmp/tre_migracao_<versao>.log`: a execução seguinte (de outro
  usuário) morria com diagnóstico vazio** (`TRE-W1-E01-T01-D02`, defeito `F1` achado na revisão
  independente do `TRE-W1-E01-T01`; card `t_41d17c27`) — `/tmp` é `tmpfs` sticky (`1777`) e o host tem
  `fs.protected_regular=2`: o `O_CREAT` de um arquivo regular **já existente e de outro dono** recebe
  `EACCES`, inclusive para `root`. Cada execução deixava o arquivo no host e a seguinte falhava **antes de
  aplicar**, imprimindo `FALHOU versao 0001 (…) falhou:` com a mensagem vazia (o `tail -3` lia um arquivo
  que nunca pôde ser escrito); o caminho fixo também era compartilhado por execuções concorrentes. Não
  houve aceite falso (fail-closed: 0 tabela, 0 linha de versão), o que faltava era robustez e diagnóstico.
  Corrigido: o log vive em diretório **por execução** (`mktemp -d`, criado antes do `psql` e removido no
  fim, inclusive em falha), o destino dentro do container também é único por execução (`$$`), e `TMPDIR`
  não gravável ou log vazio agora **dizem a causa** em vez de imprimir nada. Prova em container
  descartável próprio: `scripts/db/teste-log-migracao.sh` → `TESTE_OK (19 itens, 0 falhas)`, exit 0, com o
  comparativo antes/depois (runner anterior no mesmo cenário: exit 1, diagnóstico vazio, fail-closed).

- **Bit executável dos scripts de unit perdido no git → `tre-backup.service` morria com `203/EXEC`**
  (`TRE-W1-E06-T01-D01`, defeito medido na rotina automática pelo card E06) — `scripts/backup/*.sh` estavam
  `100644` no git; como o `ExecStart=` chama o script direto, qualquer sincronização da cópia operacional
  (`/opt/tre/repo`) devolvia `644` e o systemd recusava o exec (`status=203/EXEC`, journal
  "Failed at step EXEC ... Permission denied"). Corrigido onde o bit vive — no **git** (`100755`: **6 dos 7**
  scripts existentes mudaram de `100644` para `100755`, `teste-backup-restore.sh` já era `100755` e o novo
  verificador nasceu `100755`; commit `6a580ee`) — e na cópia operacional por `install -m 755` (conteúdo
  provado por sha256, 8/8 arquivos idênticos ao repositório). Prevenção: `scripts/backup/verificar-modos-executaveis.sh` lê os
  `ExecStart=` dos units e confere o modo no índice do git + o bit no disco (reprova o estado anterior:
  `MODOS_FALHOU` exit 1; passa depois: `MODOS_OK` exit 0) e o `instalar-timers.sh` **aborta sem habilitar
  timer** quando algum alvo está sem bit (teste negativo medido em harness isolado). Depois da correção os
  dois units executam sob `tre-deploy`: `tre-backup.service` roda `backup-tre.sh todos` (exit 0) e
  `tre-backup-verify.service` faz o restore real do último artefato (`RESTORE_OK`, 11 itens).
  **O backup diário ainda não gera artefato** — isso é o defeito irmão `t_1b2ab418` (trio `TRE_PG_*` ausente
  do `EnvironmentFile`), não o bit. **Qualificação medida (30/09 20:02–20:13 UTC):** a cópia operacional foi
  revertida para `644` duas vezes por publicação de árvore **anterior** à correção (`/opt/tre/.publicacoes.log`,
  publicações de teste do card `t_091cfea9`) — o bit no git e a guarda são duráveis, a cópia operacional
  depende do caminho versionado de publicação (ACHADO ABERTO 3). Depois da publicação versionada de
  20:12:35Z os dois critérios da cópia foram remedidos com horário (`test -x` exit 0; `systemctl start` →
  `Result=success`, `ExecMainStatus=0`) — runbook §7d/§8.
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
- **Verificador de estrutura: `exit` no meio do script tornava o resto código morto** (`TRE-W1-E04-T01`,
  defeito pré-existente achado por leitura ao estender o próprio verificador) — o resumo `RESULTADO:
  PASS/FALHOU` e o `exit` ficavam na linha 30, então **todos** os blocos de artefato versionado (backup,
  JEV policy, processo de defeitos e o novo do E04) nunca rodavam: o script imprimia `PASS` mesmo com
  artefato fora do git — aceite falso. Corrigido movendo o resumo/exit para o FIM do arquivo e provado com
  dente: com um artefato removido do índice (`git rm --cached`) o verificador devolve `FALHOU nao versionado`,
  `RESULTADO: FALHOU (1)`, exit 1; re-adicionado, `PASS (0 falhas)`, exit 0.
- **Roteiro de prova da suíte: restauração incompleta e regex errada** (`TRE-W1-E05-T01`, dois defeitos do
  próprio roteiro, achados pela prova de dente rodando contra alvo descartável) — (i) desfazer
  `DROP COLUMN cnpj` só recriava a coluna, não o índice `idx_organizations_cnpj`, que o Postgres derruba
  junto com a coluna: a suíte continuava reprovando depois de "desfeita" a divergência (o roteiro é que
  estava incompleto, o schema não); (ii) a checagem da guarda de `0 item` esperava a string `0 item`, que
  **não** é prefixo de `0 itens` — a guarda funcionava e a prova dizia que não. Corrigidos: restauração
  recria coluna **e** índice; a checagem passou a casar a mensagem real (`nao executou item nenhum`).
  Segunda execução: `SUITE_DENTE_OK (14 itens, 0 falhas)`.

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
- **Deduplicação medida em dev (`TRE-W1-E04-T01`):** `teste_dedup_sintetico.sh` → `TESTE_DEDUP_SINTETICO_OK
  (7 itens, 0 falhas)` com a suíte em `TESTE_OK (30 itens, 0 falhas)`; `teste_dedup_ambiente.sh dev` →
  `TESTE_DEDUP_AMBIENTE_OK (4 itens, 0 falhas)` com o cenário em `CENARIO_OK (17 itens, 0 falhas)` — par
  com mesmo CNPJ detectado e mesclado com confiança 1,0, par fraco (nome + cidade) parado em 0,94 e
  mandado para a fila humana, merge repetido recusado por idempotência, `UNMERGE` devolvendo os vínculos e
  contagem por tabela idêntica ao estado anterior depois da limpeza. `--detectar` no dev real: 0
  candidatos entre as duas organizações do fixture. Nada tocado em homol/prod: `docker ps -a` só tem
  `pg-sales-dev` e `/opt/tre/{prod,homolog}` seguem sem arquivo.
- **Ciclo de backup/restore provado contra o dev** (`TRE-W1-E06-T01`): artefato
  `tre_dev_20260930T193704Z` (12 tabelas, 30 índices, contagens batendo linha a linha), enviado ao bucket
  `tre-backup` com manifesto `externo: enviado`; negativos reprovados (dump truncado, dump de 0 byte, dump
  de outro banco, contagem mutada, destino externo inexistente).
- **A rotina automática de backup NÃO está funcionando** — dois achados abertos medidos no mesmo card:
  o unit `tre-backup.service` falha com `203/EXEC` (scripts de `scripts/backup/` estão `100644` no git) e,
  mesmo executando, `backup-tre.sh todos` **pula os três ambientes** (procura `pg-dev`, o dev real é
  `pg-sales-dev`) e sai `BACKUP_OK` sem gerar artefato. Detalhes em
  `docs/runbooks/backup-restore-rollback.md` §8.
- **Suíte do banco medida em dev (`TRE-W1-E05-T01`):** `suite_banco.sh dev` → `SUITE_FALHOU` (exit 1) com
  **uma** reprovação e **um** critério não testável; `--somente-leitura` roda a mesma bateria sem escrever
  no alvo (varredura `--detectar` no lugar do cenário). As etapas de contrato (37 itens), constraints/índices
  (16 itens), dedup sintético (7 itens) e dedup no ambiente (21 itens) passaram; a etapa de tenant/RLS
  fechou em `TENANT_RLS_NAO_TESTAVEL` (exit 3) — o critério homologado não é provável contra o contrato
  V1.0 (0 coluna de cliente/tenant, RLS desabilitada nas 12 tabelas, 0 policy, papel `sales_ai` superuser e
  `bypassrls`). Provas de dente: `SUITE_DENTE_OK (14 itens)` e `TENANT_RLS_DENTE_OK`. `prod` recusado
  (ADR-005, exit 1); `homolog` sem container → exit 1 apontando alvo inexistente (falha visível).
- **Divergência aberta pelo E05 (não corrigida neste card):** a etapa de ambiente da suíte acusa que a
  migration **registrada** pelo runner em dev (`bc766a818943…`) diverge do arquivo do repo
  (`0484a3701b8c…`) — o commit da decisão do trio canônico (opção 3) acrescentou um comentário ao arquivo
  **depois** de ele ter sido aplicado. Só texto (nenhuma DDL muda), mas o runner trata migration aplicada
  como imutável: `aplicar_migracoes.sh dev --somente-checar` → `MIGRACAO_FALHOU` (exit 1). Registrado como
  defeito no board, com o card do E05 esperando por ele.
- **Suíte do banco re-medida em dev na forma reformulada do AC2 (`TRE-W1-E05-T01`, 30/09/2026, após a
  decisão `A` do dono e o fechamento do D01):** `suite_banco.sh dev` → **`SUITE_OK (89 itens, 0 falhas)`,
  exit 0** — as seis etapas verdes (ambiente 3 itens, contrato 37, constraints 16, dedup sintético 7, dedup
  no ambiente 21, isolamento 5); `--somente-leitura` → `SUITE_OK (69 itens)`, exit 0, sem escrever no alvo;
  `prod` → recusado (ADR-005, exit 1); `homolog` → exit 1 apontando o alvo inexistente. Etapa 5 nova:
  `teste_isolamento_clientes.sh dev` → `ISOLAMENTO_OK (5 itens, 0 falhas)`, exit 0 (0 coluna de
  cliente/tenant, 1 base de aplicação na instância, 1 base provisionada `pg-sales-dev`); com a medição de
  provisionamento impossível → `ISOLAMENTO_NAO_TESTAVEL`, exit 3 (nunca verde). Provas de dente:
  `SUITE_DENTE_OK (19 itens, 0 falhas)` (soma a guarda do resumo ao dente do AC1/AC3) e
  `ISOLAMENTO_DENTE_OK (17 itens, 0 falhas)` (2 clientes na mesma base / segunda base na mesma instância /
  segundo serviço `pg-*` no host → exit 1, cada mutação desfeita volta a aprovar). O instrumento do V2
  (`teste_tenant_rls.sh dev`) segue em `TENANT_RLS_NAO_TESTAVEL`, exit 3, **fora da suíte**.
- **D01 fechado e visível na suíte:** o registro do runner em dev foi realinhado ao sha do arquivo
  (`0484a3701b8c…` nas três fontes) — `aplicar_migracoes.sh dev --somente-checar` → `MIGRACAO_OK`, exit 0, e
  a etapa `ambiente` da suíte saiu `OK` (antes reprovava por causa da divergência). Estado do dev antes ×
  depois da bateria: `12 tabelas | 30 índices` nas duas pontas; `docker ps -a` só `pg-sales-dev`;
  `/opt/tre/{prod,homolog}` com **0 arquivo**.
- **Caminho de escrita da cópia operacional (`TRE-W1-E05-T01` + `t_1b2ab418`, 30/09/2026):** a rodada 1 do
  E05 (e a primeira bateria da rodada 2) sincronizava `/opt/tre/repo` com
  `tar -cz … | ssh … 'tar -xz'`; esse padrão **não é o caminho de publicação** e sobrescreveu a árvore
  publicada (`c7972ca`), fazendo o `tre-backup.service` voltar a rodar a rotina pré-correção por alguns
  minutos — medido pelo card `t_1b2ab418` (`PUBLICACAO_DIVERGENTE`, exit 5, 21:43:17Z) e reparado pela
  republicação às 21:52:24Z. A rodada 2 passou a publicar o commit pelo caminho versionado em **destino
  isolado de ensaio** (`TRE_PUBLICAR_DESTINO=/opt/tre/.teste-publicacao-<card> deploy/publicar.sh --commit …`)
  e o runbook da suíte ganhou a seção 1.1 declarando o `tar` para a cópia como proibido.
- **Rodada 3 da suíte do banco (`TRE-W1-E05-T01`, 30/09/2026, commit `21ed325`) — os 3 itens da revisão
  independente fechados, medidos na VPS do dev contra o commit publicado em destino isolado de ensaio**
  (`deploy/publicar.sh --commit 21ed325` → `PUBLICACAO_OK … digest=c35ecba3… arquivos=307`; a cópia
  operacional `/opt/tre/repo` **não** foi escrita): `suite_banco.sh dev` → `SUITE_OK (89 itens, 0 falhas)`,
  exit 0; `--somente-leitura` → `SUITE_OK (69 itens)`, exit 0; `prod` → exit 1 (ADR-005, recusado antes de
  tocar no alvo); `homolog` → exit 1 (alvo inexistente); `teste_isolamento_clientes.sh dev` →
  `ISOLAMENTO_OK (5 itens)`, exit 0; `TRE_ISOLAMENTO_SEM_DOCKER=1` → exit 3; `teste_tenant_rls.sh dev` →
  exit 3 (instrumento do V2); `aplicar_migracoes.sh dev --somente-checar` → `MIGRACAO_OK`, exit 0; dentes:
  `SUITE_DENTE_OK (19 itens)`, `ISOLAMENTO_DENTE_OK (26 itens)`, `TENANT_RLS_DENTE_OK (18 itens)`. Dev antes
  × depois: `12 tabelas | 30 índices`, contagens `2 / 1 / 1`, registro da migration `0484a370…` == arquivo;
  `/opt/tre/{prod,homolog}` com **0 arquivo**. O dedup sintético passou de 47 para 48 itens **por mudança de
  outro card** (`7a6a270`, TRE-W1-E04-T02/D02) que entrou em `develop` entre as rodadas — o commit desta
  rodada toca 4 arquivos (2 scripts de teste + régua + runbook).

## [W2 — Odoo Community] — 01/10/2026

### Added

- **Odoo Community `19.0` instalado no ambiente dev (`TRE-W2-E01-T01`)** — compose versionado
  (`deploy/compose/dev/odoo.yml`) + par não-secreto (`deploy/environments/dev-odoo.env`, com
  `ODOO_VERSION`/`ODOO_HTTP_PORT`/`ODOO_DIGEST_ESPERADO` **obrigatórios**: sem o par o
  `docker compose config` falha em vez de subir "a última de hoje") + `scripts/provision/{instalar,verificar,remover}-odoo-dev.sh`
  + runbook `docs/runbooks/odoo-dev.md`. Medido na VPS Contabo `vmi3619453`, 01/10/2026: imagem
  `odoo:19.0` (`19.0-20260926`) no digest `sha256:77bac5cd…`; containers `odoo-dev` (id `12cf65a3c1c6…`)
  e `pg-odoo-dev` (id `c7cb12f75eb9…`, `postgres:16`); volumes `odoo-data-dev`/`pgdata-odoo-dev`; rede
  `tre-odoo-dev`; banco `odoo_dev` com o módulo `base` (sem dados de demonstração).
- **Aceite item a item**: `bash verificar-odoo-dev.sh` → `RESULTADO: ODOO_DEV_OK (19 itens, 0 falhas)`,
  exit 0 — compose válido, identidade (imagem/tag/digest) conferida, `HTTP 200` real em
  `127.0.0.1:8069/web/login`, banco do Odoo separado do `sales_intelligence` medido **nos dois lados**, e
  nenhuma porta pública.
- **Dentes do aceite** (provas negativas medidas): `docker` falso publicando o Odoo em `0.0.0.0` →
  `ODOO_DEV_FALHOU (19 itens, 1 falha)`, exit 1; `remover-odoo-dev.sh` sem
  `TRE_ODOO_CONFIRMAR_REMOCAO=1` → recusa, exit 1; verificador rodado após o rollback →
  `13 falha(s)`, exit 1.
- **Módulo Odoo `transformativa_sales_ai` criado (`TRE-W2-E03-T01`)** — a base das customizações e da
  integração: `odoo/addons/transformativa_sales_ai/` (manifesto `version 19.0.1.0.0`,
  `license LGPL-3`, `depends ['base','crm']`, `application False`) + 6 testes do Odoo
  (`tests/test_modulo_base.py`, tag `post_install`) + `scripts/odoo/verificar-modulo-odoo.sh`
  (aceite de 4 passos) com `manifesto_do_modulo.py` e `desinstalar_modulo.py` + runbook
  `docs/runbooks/odoo-modulo-sales-ai.md`. O módulo nasce **sem modelo, view ou ACL**: os campos de
  `res.partner`/`crm.lead`, o `tf.process.opportunity`, as views, as ACLs e a API entram nas cards
  `E04-T01/T02`, `E05`, `E06`, `E07` e `W3-E01`.
- **Aceite do módulo item a item (51 itens)**: instalação em banco limpo → teste do Odoo →
  desinstalação → reinstalação, tudo numa **dupla descartável própria** (`postgres:16` + `odoo:19.0`,
  as imagens do par de dev) e não no `odoo-dev`/`pg-odoo-dev` → `RESULTADO: MODULO_ODOO_OK (51 itens,
  0 falhas)`, exit 0. Medido: banco criado do zero, 0 ERROR/CRITICAL nos quatro logs,
  `0 failed, 0 error(s) of 6 tests`, `DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled`
  com 0 resquício no banco, e reinstalação com `latest_version` == manifesto.
- **Dentes do aceite do módulo** (`--prova-de-dente`, 2 provas, cada uma em cópia do módulo):
  `version` mutada para `18.0.1.0.0` → `MODULO_ODOO_FALHOU (19 itens, 3 falhas)`, exit 1; teste
  plantado que falha → `MODULO_ODOO_FALHOU (36 itens, 2 falhas)` com `odoo --test-enable exit 1`,
  exit 1.
- **Oportunidade canônica do lado Odoo — `tf.process.opportunity` (`TRE-W2-E05-T01`)** — primeiro
  conteúdo do módulo `transformativa_sales_ai`: `models/tf_process_opportunity.py` com a identidade
  canônica (`tf_uuid`: UUID obrigatório, único e imutável — Data Contract V1.0 §3, com o UUID do
  produtor preservado e UUID v4 gerado quando ausente), o vínculo obrigatório a `res.partner`
  (`ondelete='restrict'`) e os fatos que o contrato §2 dá ao Odoo (`stage_id`
  (`crm.stage` — que carrega o won/lost do funil via `is_won`), `expected_revenue` (`valor`) e
  `lost_reason_id` (`motivo de perda`)) + `models/__init__.py` + 9 testes do Odoo
  (`tests/test_oportunidade_canonica.py`, tag `post_install`) + runbook
  `docs/runbooks/odoo-oportunidade-canonica.md`. O modelo **não** traz score de prioridade (o
  contrato o mapeia para `res.partner`/`crm.lead`), FK para `crm.lead` (o vínculo é
  `crm.lead.tf_opportunity_id`, card E04-T02), view (E06) nem ACL (E07); a versão do módulo fica em
  `19.0.1.0.0` (nada instalado em ambiente persistente e três cards da onda editam o mesmo manifesto).
- **Aceite do card item a item (51 itens, 0 falhas)**: instalação em banco limpo → teste do Odoo →
  desinstalação → reinstalação, tudo em dupla descartável própria e com `TRE_MODULO_DIR`/`TRE_BANCO`/
  `TRE_LOG_DIR` **próprios do card** (`/opt/tre/dev/modulos-e05t01/…`, `tre_e05_t01_oportunidade`) →
  `RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas)`, exit 0. Medido: `0 failed, 0 error(s) of 15 tests`
  (6 herdados do E03 + 9 novos), 0 `ERROR/CRITICAL` nos quatro logs, `DESINSTALACAO_OK
  estado_antes=installed estado_depois=uninstalled` com 0 resquício e 0 tabela do módulo, e
  reinstalação com `latest_version` == manifesto. O teste 06 imprime a prova do vínculo no log:
  `ForeignKeyViolation … violates foreign key constraint "tf_process_opportunity_partner_id_fkey"`.
- **Dentes do aceite**: os 2 herdados do E03 (`--prova-de-dente`, em cópia do módulo) **mais 3 provas
  negativas próprias deste card**, cada uma mutando uma cópia do módulo e exigindo reprovação **com o
  teste esperado caindo**: `tf_uuid` sem `required` → `FAIL: …test_01_modelo_criado_com_os_campos_do_contrato`
  (`tf_uuid tem de ser obrigatorio`); guarda de imutabilidade neutralizada →
  `FAIL: …test_08_uuid_canonico_e_imutavel` (`ValidationError not raised`); `unique (tf_uuid)` trocada
  por `unique (id)` → `FAIL: …test_07_uuid_canonico_e_unico` (`IntegrityError not raised`). Nas três o
  aceite reprovou com `MODULO_ODOO_FALHOU (36 itens, 2 falhas)`, exit 1.
- **ACLs e regras de segurança do módulo (carteira × tenant)** (`TRE-W2-E07-T01`, `t_e0b1bcbf`):
  `odoo/addons/transformativa_sales_ai/security/transformativa_sales_ai_security.xml` e
  `security/ir.model.access.csv` — dois grupos (`Sales AI: Vendedor (carteira)` e
  `Sales AI: Gestor (tenant)`, este herdando o primeiro) num `res.groups.privilege`/`ir.module.category`
  do módulo (a API de grupos do Odoo 19: `res.groups.category_id` não existe mais), ACL do modelo
  (vendedor `1,1,1,0`; gestor `1,1,1,1`; **nada** para `base.group_user`/portal/público) e **três
  regras de registro**: tenant `[('company_id', 'in', company_ids)]` (**global**, vale para todo mundo
  que alcança o modelo), carteira `[('partner_id.user_id', '=', user.id)]` no grupo do vendedor e
  `[(1, '=', 1)]` no grupo do gestor. Nenhum campo novo: **tenant = `res.company`** (companhia do
  Odoo; o isolamento entre clientes na V1 é físico, um banco por cliente, decisão do dono de
  30/09/2026) e **carteira = `res.partner.user_id`** (vendedor do parceiro, campo nativo, `store=True`).
- **Aceite próprio das ACLs**: `scripts/odoo/verificar-acl-modulo.sh` (novo; instalado em dupla
  descartável própria, `e07t01-*`) mede 51 itens em quatro passos — instalação em banco limpo, suíte do
  Odoo (`0 failed, 0 error(s) of 26 tests`, com a classe `TestAclSeguranca` no log), as regras **lidas
  no banco** (grupos, privilégio/categoria, ACL, as 3 regras com domínio/alcance/global) e a **prova
  negativa independente** `scripts/odoo/provar_acl_modulo.py` (22 itens, 0 falhas) → `RESULTADO: ACL_OK
  (51 itens, 0 falhas)`, exit 0. Os testes do próprio módulo (`tests/test_acl_seguranca.py`, 11 testes
  `post_install`) usam usuários de verdade (`with_user`) em dois tenants e três carteiras: fail-closed
  sem o grupo, carteira alheia (busca e leitura por id), carteira vazia, outro tenant, gestor do tenant,
  criação em carteira alheia recusada e na própria aceita.
- **Duas provas de dente próprias do card** (`--prova-de-dente`, cada uma mutando uma **cópia** do
  módulo em banco próprio): regra de carteira aberta para `[(1, '=', 1)]` → `ACL_FALHOU` com 6 falhas,
  incluindo a acusação de **material alheio** na prova negativa; ACL plantada dando escrita em
  `res.users` ao grupo do vendedor → `ACL_FALHOU` com a superfície de ACL reprovando
  (`res.users, tf.process.opportunity`). `RESULTADO: ACL_DENTE_OK (2 provas, 0 falhas)`, exit 0.
- **Campos de dedup e IDs canônicos em `res.partner` (`TRE-W2-E04-T01`)** — `models/res_partner.py`
  acrescenta os **identificadores fortes** do contrato §5 (`tf_cnpj`, `tf_domain`,
  `tf_linkedin_url`, os três **indexados**) e os campos do `canonical_ids.odoo_map` que apontam para
  `res.partner` (`tf_company_id` = UUID canônico de `organizations.id`, com a **forma** do UUID
  conferida, e `tf_priority_score`). Não normaliza identificador, não valida CNPJ e não cria `UNIQUE`
  — o contrato manda *detectar e reportar* duplicidade (merge só com confiança ≥ 0,95), nunca recusar
  o dado. 7 testes do Odoo em `tests/test_res_partner_dedup.py`, conferidor de não divergência
  (`scripts/odoo/conferir_res_partner_no_contrato.py`, lê o `data_contract_v1.json` congelado por
  AST) e aceite próprio (`scripts/odoo/verificar-res-partner.sh`) + runbook
  `docs/runbooks/res-partner-campos-dedup.md`.
- **Aceite de `res.partner` item a item (64 itens)**: modelo × contrato V1.0, instalação em banco
  limpo, testes do Odoo, **catálogo do PostgreSQL** (colunas, tipos, índices btree), criação/consulta
  de parceiro sintético pelo ORM (`odoo shell`, instrumento independente dos testes) e desinstalação —
  tudo numa dupla descartável própria → `RESULTADO: RES_PARTNER_OK (64 itens, 0 falhas)`, exit 0.
  Medido: `0 failed, 0 error(s) of 13 tests` com **7 testes deste card e 6 da base efetivamente
  rodados**, índices btree reais (`res_partner__tf_*_index`), consulta por cada identificador com
  `n=1` e identificador diferente com `n=0`, e o rollback removendo as 5 colunas e todos os índices
  `tf_*` de `res_partner` com **0 resquício**.
- **Dentes do aceite de `res.partner`** (`--prova-de-dente`, 3 provas, cada uma em cópia do módulo):
  `tf_cnpj` sem `index=True` → `RES_PARTNER_FALHOU`, exit 1; `tf_domain` renomeado para `tf_dominio` →
  `RES_PARTNER_FALHOU`, exit 1; teste plantado que falha → `1 failed` no relatório, `odoo
  --test-enable exit 1`, exit 1.

- **Campos de rastreio em `crm.lead` (`TRE-W2-E04-T02`)** — `odoo/addons/transformativa_sales_ai/models/crm_lead.py`
  acrescenta a `crm.lead` os **13 campos de rastreio** do Sales AI, aditivos (nenhum campo padrão é
  alterado): os 2 que o Data Contract V1.0 §3 nomeia (`tf_opportunity_id`, `tf_priority_score`) e 11
  espelhos de artefatos do contrato (score model §8, vocabulário `next_best_action` §7, correlação/
  idempotência §3 e trilha de sincronização/eventos §6), com `tracking=True` e índice nos 3 campos de
  busca por identidade/correlação. Ferramentas: `scripts/odoo/conferir_crm_lead_no_contrato.py` (confronto
  módulo × contrato congelado, 18 itens), `scripts/odoo/medir_crm_lead.py` (medição ORM com dado
  sintético) e `scripts/odoo/verificar-crm-lead-odoo.sh` (aceite de 6 passos, com rollback medido) +
  `tests/test_crm_lead_rastreio.py` (7 testes) + runbook `docs/runbooks/odoo-crm-lead-sales-ai.md`.
  Decisão registrada: o contrato nomeia **dois** campos em `crm.lead`; o "13" do plano não tem fonte
  materializada no repo, então o inventário é declarado item a item com proveniência (runbook §2) — e
  nenhum vocabulário novo é criado.
- **Aceite de `crm.lead` item a item (64 itens)**: confronto módulo × contrato → instalação em banco
  limpo → 13 campos em `ir_model_fields` + índices reais em `pg_indexes` → `--test-enable` com
  `0 failed, 0 error(s) of 13 tests` (7 do card) → dado sintético pelo ORM (`MEDICAO_CRM_LEAD_OK`,
  58 itens) com conferência por SQL fora da sessão do Odoo → rollback por desinstalação (0 campo, 0
  coluna e 0 índice `tf_` depois; colunas padrão do `crm_lead` intactas) → limpeza e dev intacto →
  `RESULTADO: CRM_LEAD_OK (64 itens, 0 falhas)`, exit 0, numa **dupla descartável própria**.
- **Dentes do aceite de `crm.lead`** (`--prova-de-dente`, **5 provas**, cada uma em cópia do módulo):
  `tf_opportunity_id` renomeado, medido **no banco** → `CRM_LEAD_FALHOU (35 itens, 3 falhas)`, exit 1;
  índice do `tf_idempotency_key` removido, no banco → `CRM_LEAD_FALHOU (35 itens, 1 falha)`, exit 1;
  `tf_next_best_action` apagado do modelo, no banco → `CRM_LEAD_FALHOU (35 itens, 2 falhas)`, exit 1;
  a mesma renomeação medida **só pelo confronto estático** → `CRM_LEAD_FALHOU (13 itens, 1 falha)`,
  exit 1; teste plantado que falha → `CRM_LEAD_FALHOU (43 itens, 3 falhas)`, exit 1. Resultado do
  comando: `RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`, exit 0. Os dentes 1–3 rodam com
  `TRE_PULAR_CONFRONTO=1` (pula só o passo 0) para que quem reprove seja a **medição de banco** — sem
  isso a mutação de campo/índice pararia no confronto estático e o caminho de banco não era exercitado
  (defeito 4 do runbook §8).

- **Views do Sales AI no módulo Odoo (`TRE-W2-E06-T01`, `t_cf7519c9`)** — `views/` do
  `transformativa_sales_ai`: três views próprias da entidade canônica
  (`view_tf_process_opportunity_list/form/search`, com o `tf_uuid` como caminho de busca —
  contrato §3), a ação `action_tf_process_opportunity` (`list,form`) e o menu **Sales AI →
  Oportunidades** pendurado no menu raiz do CRM; mais as duas views **herdadas** que acrescentam a
  seção "Sales AI" ao formulário do parceiro (`view_partner_form_tf_sales_ai` sobre
  `base.view_partner_form`, 5 campos `tf_*`) e do lead (`view_crm_lead_form_tf_sales_ai` sobre
  `crm.crm_lead_view_form`, 13 campos `tf_*`). Ferramentas: `scripts/odoo/verificar-views-sales-ai.sh`
  (aceite de 6 passos, com rollback medido), `scripts/odoo/provar_views_sales_ai.py` (prova
  independente, 21 itens), `tests/test_views_sales_ai.py` (10 testes) e o runbook
  `docs/runbooks/odoo-views-sales-ai.md`.
- **Recorte por perfil medido no servidor (AC2)**: o menu e as duas seções "Sales AI" são recortados
  por `group_tf_sales_ai_user`, com o `groups` no **nó da arch** e no menu — no Odoo 19 o *registro*
  de view herdada não pode carregar `groups` (`ParseError: Inherited view cannot have 'groups'
  defined on the record`, medido na primeira rodada). A prova independente mede a view
  **renderizada** para três usuários que diferem só pelo grupo do módulo: o membro abre lista e
  formulário e vê a seção; o restrito não vê o menu, não vê a seção no parceiro/lead e recebe
  `AccessError` na entidade canônica (fail-closed do E07). Decisão registrada: `company_id` e
  `currency_id` seguem o convencional do Odoo (grupos padrão multi-companhia/multi-moeda, como no
  `crm.lead`) e são medidos com um usuário multi — e o grupo `base.group_multi_company` **não** se
  concede na mão (o Odoo o deriva de `company_ids`, `UsersMultiCompany` em `res_users.py`).
- **Aceite das views item a item (83 itens)**: instalação em banco limpo → `--test-enable` com
  `0 failed, 0 error(s) of 50 tests` (10 do card) → views/menus/ação e `groups` lidos **no banco**
  (arch gravada, `ir_ui_menu_group_rel`, hierarquia do menu) → prova independente com dado
  sintético (`VIEW_ITENS=21`, `VIEW_FALHAS=0`) → rollback por desinstalação (0 view, 0 menu, 0
  registro do módulo; seção "Sales AI" ausente do parceiro) → limpeza e dev/homolog/prod intactos →
  `RESULTADO: VIEWS_OK (83 itens, 0 falhas)`, exit 0, numa **dupla descartável própria**
  (`postgres:16` + `odoo:19.0`).
- **Dentes do aceite das views** (`--prova-de-dente`, **2 provas**, cada uma em cópia do módulo):
  dente 1 (o `groups` da seção do parceiro removido) → `VIEWS_FALHOU (83 itens, 6 falhas)`, exit 1
  (`FAIL: TestViewsSalesAi.test_09_ac2…`, `AC2 a view herdada view_partner_form_tf_sales_ai nao
  recorta a secao pelo grupo do vendedor (medido 0)` e a prova independente com 2 falhas); dente 2
  (a view do modelo fora do manifesto `data`) → `VIEWS_FALHOU (83 itens, 27 falhas)`, exit 1.
  Resultado do comando: `RESULTADO: VIEWS_DENTE_OK (2 provas, 0 falhas)`, exit 0.

### Security

- **Aprovação humana nunca concedida por máquina** (`TRE-W2-E07-T01`) — medido, não declarado: a
  superfície de ACL do módulo é **exatamente um modelo** (`tf.process.opportunity`); nenhum grupo do
  módulo implica ou alcança `base.group_system`/`base.group_erp_manager`; o usuário do Sales AI não
  administra **outro** usuário (`AccessError`), não cria `ir.rule` (`AccessError`) e a tentativa de se
  dar o grupo de administrador **não promove** (o grupo não entra). O módulo também não concede
  `base.group_user` por conta própria. A aprovação humana segue fora do Odoo (registro de aprovações +
  gate JEV), intocada por este card.
- **Isolamento por tenant/carteira no módulo Odoo** (`TRE-W2-E07-T01`) — regra de tenant **global**
  (`company_id in company_ids`) e regra de carteira por grupo; sem o grupo do módulo o modelo é
  inalcançável (**fail-closed**, `AccessError`, medido), e o dado do outro tenant/carteira não chega
  nem por busca (vazio) nem por leitura de id (erro) — nunca material alheio.
- **Nenhuma porta pública**: o Odoo publica só em `127.0.0.1:8069`, o PostgreSQL do Odoo não publica
  porta nenhuma (fala pela rede interna `tre-odoo-dev`) e a UFW segue com **só a 22/tcp**. Exposição
  pública continua sendo card próprio (`TRE-W2-E01-T02`, TLS/proxy).
- **Segredo fora do artefato**: `admin_passwd` e a senha do banco nascem na VPS, em
  `/etc/tre/odoo-dev/{pg.env,odoo.conf}` (600; o `odoo.conf` com dono uid 100 porque o processo roda como
  `odoo` no container). Nada de valor de senha no repo, em log ou em argumento; o verificador de
  estrutura ganhou item que reprova par de ambiente com valor de senha.

### Fixed

- **Cinco defeitos encontrados nesta execução** (todos medidos, todos consertados):
  (1) o `POSTGRES_DB` do `postgres:16` cria o banco **vazio**, e o check por `pg_database` pulou a
  inicialização — o Odoo respondia **HTTP 500** em `/web/login`; o que prova inicialização passou a ser a
  tabela do módulo `base` (`ir_module_module`);
  (2) `docker compose run` consome o stdin de quem o executa e, orquestrado por `ssh 'bash -s' < script`,
  **matava o script remoto no meio** (mesma armadilha já registrada neste CHANGELOG) — conserto com `-T` e
  `< /dev/null`, e execução por arquivo na VPS;
  (3) o item 6 do próprio verificador reprovava o **formato** real do `docker port`
  (`8069/tcp -> 127.0.0.1:8069`) em vez do comportamento — agora reprova endereço não-loopback, com a
  prova por mutação acima;
  (4) o instalador reprovava o `secret_scan.sh` do repo por escrever a chave na forma literal
  `"<chave> = <variável>"` (falso positivo) — conserto no código, não no scanner (`PASS` depois);
  (5) a guarda de "porta em uso" reprovava a **reexecução idempotente** (o próprio `odoo-dev` segurava a
  8069) — a guarda passou a valer só quando o container ainda não existe.
- **Seis defeitos do verificador do módulo, achados executando (`TRE-W2-E03-T01`)** —
  (1) o nome técnico do módulo saía errado porque o manifesto era lido montado em `/modulo` (o item
  acusava qualquer módulo) — conserto: montar em `/leitura/<nome-real>`;
  (2) o Odoo do dev **entra em qualquer banco novo** da instância `pg-odoo-dev` (medido: sessão de
  `172.18.0.3` = `odoo-dev`, `application_name=odoo-1`, em ~30s num banco criado do zero) e a limpeza
  morria com `being accessed by other users` — conserto: dupla descartável própria + `dropdb --force`;
  (3) `select name … join ir_module_module` → `column reference "name" is ambiguous` no item de
  dependências (com o `stderr` descartado, o item reprovava **sempre** — falso negativo) — conserto:
  `select d.name`;
  (4) `ir_module_module_dependency.state` é campo calculado (não tem coluna) — conserto: conferir o
  estado no módulo dependente;
  (5) o `secret_scan.sh` do repo reprovou o verificador por escrever a chave da senha na forma
  literal (`"<chave> = <variável>"` — falso positivo, o valor é variável) — conserto **no código, não
  no scanner** (chave por variável + `printf`), `secret_scan.sh` → `PASS`;
  (6) **regressão da correção (5), pega por reexecutar**: sobrou o `echo` antigo da chave mestra, o
  `odoo.conf` ficou com a opção **duas vezes** e a instalação morreu com
  `configparser.DuplicateOptionError: option 'admin_passwd' … already exists` (`MODULO_ODOO_FALHOU
  (24 itens, 3 falhas)`, exit 1) — conserto e a bateria inteira (manifesto + dentes + aceite)
  reexecutada depois de **qualquer** edição do verificador. Detalhe no runbook §8.
- **Dois defeitos do aceite de `res.partner` (`TRE-W2-E04-T01`), achados executando e consertados**
  (runbook §6): (1) `ir.model.fields.index` é **booleano** no Odoo 19 e o teste/o item do verificador
  exigiam a string `btree` — a rodada 1 morreu com `1 failed … of 13 tests` e
  `ir_model_fields.index de res.partner.tf_cnpj: 't' (esperado btree)`; o teste passou a exigir
  índice declarado no ORM e o **tipo** btree continua provado no catálogo (`pg_indexes`); (2) o item
  "nenhuma linha de teste FAIL:/ERROR:" estava ancorado no início da linha e imprimia **OK com um
  teste reprovado** (o Odoo 19 escreve `… ERROR <banco> <módulo>: FAIL: TestX.test_y`) — mesma classe
  do defeito D04 do verificador de estrutura; conserto com `grep -cE '(^| )(FAIL|ERROR): [A-Za-z_]'`,
  provado no log da rodada 1 (padrão antigo 0 casamentos / padrão novo 1) e pelo dente 3.
- **`'At least one test failed when loading the modules.'` é marcador vazio no Odoo 19**
  (`TRE-W2-E04-T01`): medido — ele **não** aparece nem com teste reprovado. O item do aceite ficou só
  como ausência (não pode dar falso OK) e os dentes reais do passo de testes são o **exit code**, o
  **relatório do runner** e as **linhas `FAIL:`**.
- **Cinco defeitos encontrados executando o aceite de `crm.lead` (`TRE-W2-E04-T02`)**, todos
  consertados e remedidos (os quatro primeiros na rodada 1; o 5º veio da **revisão independente** e
  foi remedido na rodada 2):
  (1) `test_07` do card estourava `ValueError: too many values to unpack (expected 2)` — o teste
  desempacotava a constante do vocabulário como pares, e ela é a lista de VALORES do contrato (a
  primeira rodada do aceite pegou: `0 failed, 1 error(s) of 13 tests`, exit 1);
  (2) o item "nenhuma linha de teste `FAIL:`/`ERROR:`" do verificador novo usava `^(FAIL|ERROR): ` e o
  log do Odoo prefixa a linha com data/hora/nível — o item ficava **cego** (com 1 erro real ele
  imprimia "nenhuma linha"). Conserto: padrão sem âncora de início; provado pelo dente 4 (teste
  plantado), que agora reprova o passo 3 com `3 falha(s)`;
  (3) as provas de dente reexecutavam o verificador por `"$0"`, que só funciona quando o chamador passa
  caminho com barra — invocado como `bash verificar-crm-lead-odoo.sh` as 4 provas morriam com
  `verificar-crm-lead-odoo.sh: command not found` e o comando devolvia
  `CRM_LEAD_DENTE_FALHOU (4 prova(s) sem dente)`. Conserto: caminho absoluto do próprio script
  (`SELF="$(readlink -f "$0")"`). Detalhe no runbook §8.
  (4) os dentes de campo/índice reprovavam **só no confronto estático** (passo 0) e o verificador
  encerrava logo depois do par — os itens de **banco** nunca eram exercitados (`16 itens, 1 falha`),
  então o dente não provava quem mede o quê. Conserto: `TRE_PULAR_CONFRONTO=1` (pula só o passo 0,
  registrando `INFO`) nos dentes 1–3 — que passaram a reprovar pelo banco (`35 itens`, 3/1/2 falhas) —
  mais o **dente 5**, que mede o confronto estático com a mesma mutação (`13 itens, 1 falha`); a
  bateria final deu `CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`, exit 0. Detalhe no runbook §8.
  (5) **o modo `--prova-de-dente` dava verde sem exercitar dente nenhum** (fail-open, achado pela
  revisão independente da rodada 1) — o julgamento aceitava **qualquer** `CRM_LEAD_FALHOU` como prova
  de dente, e as 5 provas morriam na **guarda**, antes de medir qualquer coisa. Reproduzido por mim
  com o artefato da rodada 1 (`verificar-crm-lead-odoo.sh 161b512e…`, nada exportado): cada prova
  devolvia `CRM_LEAD_FALHOU (6 itens, 1 falha(s))` (contrato ausente no caminho padrão, que não existe
  em nenhum ambiente medido) e o comando terminava em `CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`,
  **exit 0** — verde sem medição. Conserto na rodada 2: (a) o modo dente roda o **caminho não mutado**
  (baseline: passo 0+1+2+3) e **exige verde**, terminando em
  `CRM_LEAD_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, exit 1, quando o baseline
  não mede; (b) cada dente exige a **sua** assinatura de falha em vez de "qualquer FALHOU"; (c) aborto
  de guarda reprova o dente; (d) o contrato deixou de ter default morto — sem `TRE_CONTRATO_JSON` em
  disco o comando **recusa de cara** (`exit 1`) e diz o que exportar, e o USO do cabeçalho/runbook §3
  passaram a exportá-lo. Detalhe no runbook §8.
- **Modo `--prova-de-dente` fail-closed no aceite das views (`TRE-W2-E06-T01`, `t_cf7519c9`)** — a
  revisão independente da rodada 1 mediu dois defeitos bloqueantes no harness novo e pediu mudanças:
  (1) **fail-open** — o julgamento aceitava **qualquer** `RESULTADO: VIEWS_FALHOU`, inclusive aborto
  de guarda de ambiente: com `TRE_MODULO_DIR` inexistente, `DOCKER_HOST` inválido ou `TRE_IMAGEM`
  ausente o comando devolvia `VIEWS_DENTE_OK (2 provas, 0 falhas)`, **exit 0** (mesma classe já
  consertada no E04-T01, `a539802`/defeito `t_e1f62fae`, e no E04-T02, `e8bfe71`); (2) o **D-02 do
  E03 reincidiu** — os sub-runs do dente herdavam `TRE_LOG_DIR` e sobrescreviam os 4 logs do aceite
  (terceira incidência: `t_5c4fc7ac`/`c389223` → `t_aaaf1558` → aqui). Conserto medido na rodada 2
  (commit `1c93804`): **âncora do artefato** (sha256 arquivo a arquivo contra o checkout do card — o
  default `/opt/tre/dev/modulos/<módulo>` passa a ser **recusado**), **baseline não mutado
  obrigatório** (o caminho sem mutação tem de medir `VIEWS_OK`; sem ele o comando termina em
  `VIEWS_DENTE_FALHOU (baseline nao medido)`, exit 1), **assinatura própria por dente** + exigência
  de ter medido o aceite inteiro (`passo 6/6`) + recusa de marcador de aborto de guarda, **log
  próprio por prova** (`$TRE_LOG_DIR/dente/prova-N`) com guarda fail-closed por sha256 do diretório
  do aceite antes/depois. Resultado: aceite `VIEWS_OK (83 itens, 0 falhas)`, dente
  `VIEWS_DENTE_OK (2 provas, 0 falhas)` com baseline verde, dente 1 `83 itens / 6 falhas` e dente 2
  `83 itens / 27 falhas` com as assinaturas esperadas, `logs do aceite intactos` (4 arquivos com
  sha256 idêntico, mais guarda externa `EXIT_GUARDA=0`) e **6 de 6 controles fail-closed** com exit 1
  (`ctl-a` default compartilhado, `ctl-b` docker fora, `ctl-c` imagem ausente, `ctl-d` sem âncora,
  `ctl-e` módulo inexistente, `ctl-f` README divergente). Achado menor corrigido no mesmo rework: a
  rodada 1 registrou sha256 igual "nos 20 arquivos" e a cópia medida carregava um `README.md` de
  rodada anterior ao commit entregue. Detalhe no runbook §8.

### Notas de estado

- **Módulo `transformativa_sales_ai` já tem conteúdo (`TRE-W2-E04-T01`)**: `res.partner` ganhou 5
  campos (`tf_cnpj`, `tf_domain`, `tf_linkedin_url`, `tf_company_id`, `tf_priority_score`), sem view,
  sem ACL e sem regra automática de merge. Enquanto o módulo não for publicado na cópia operacional
  `/opt/tre/repo`, ele é medido pelo caminho do runbook `docs/runbooks/res-partner-campos-dedup.md`
  (dupla descartável própria + cópia do card em `/opt/tre/dev/cards/t_adee6ad7/`), e **não** aparece
  no `odoo-dev`.
- **Rollback testado de verdade** (não só escrito): `TRE_ODOO_CONFIRMAR_REMOCAO=1 bash remover-odoo-dev.sh`
  derrubou containers, volumes do Odoo e segredos, com `pg-sales-dev` **intacto** (`running` ao fim); o
  verificador então reprovou o ambiente ausente e a **reinstalação limpa** voltou a passar 19/19.
- **Versão escolhida pelo executor em dev** (19.0 em vez do 20.0 recém-lançado), dentro da declaração de
  ação do dono para este card (`hermes/jev/acoes-declaradas.yaml`, 01/10/2026; recibo JEV
  `dec-c6650746cbb56e76` = PASS): fica **pendente de ratificação do Anderson** para homologação/produção,
  onde a aprovação humana é obrigatória de qualquer forma (ADR-005).
- **Cópia operacional segue em linha divergente do `develop`**: `/opt/tre/repo` está em
  `fix/t_daca4bda-enforcement` (com `deploy/publicar.sh`, watchdog e enforcement), e uma árvore nascida do
  `develop` não os contém — publicar apagaria o enforcement. Por isso o dev do Odoo roda do par em
  `/opt/tre/dev/compose/`, com o compose **versionado no repo**. `homolog` e `prod` seguem **sem
  nenhum arquivo e sem container**.
- **Módulo `transformativa_sales_ai` ainda não aparece no `odoo-dev` (`TRE-W2-E03-T01`)**: o container
  monta `/opt/tre/repo/odoo/addons` como `/mnt/extra-addons`, e a cópia operacional está na linha
  divergente acima — enquanto as duas linhas não se encontrarem, o módulo é medido pelo runbook
  `docs/runbooks/odoo-modulo-sales-ai.md` (dupla descartável própria com as imagens do par de dev) e a
  publicação na cópia operacional fica pendente. O aceite do card pede banco **limpo**, e o `odoo_dev`
  não é limpo (carrega o funil do `TRE-W2-E02-T01`).

## [W3 — Integração] — 01/10/2026

### Added

- **API controlada do Odoo (`TRE-W3-E01-T01`, `t_e0489efc`)** — `POST /tf/api/v1/<operacao>` no
  módulo `transformativa_sales_ai`: a porta única de leitura/escrita de objetos de negócio do Odoo
  para a integração (doc 02 §3 — nada de escrita direta nas tabelas internas), com `auth='bearer'`
  (chave de API do Odoo 19, resolvida pelo próprio Odoo antes do controlador) e CSRF desligado.
  O que a torna **controlada** não é a autenticação: é a **política versionada**
  (`api/politica_api.json`, dentro do módulo — logo versionada no repo **e** no artefato publicado),
  que declara operação a operação o modelo alvo, os campos permitidos, os filtros/limites e a
  exigência de `idempotency_key`. Não existe rota genérica de "execute qualquer modelo/método/campo":
  fora da declaração a resposta é recusa nomeada (fail-closed). A **decisão** vive no motor
  (`api/motor.py`, que **não importa `odoo`** de propósito, para ser exercitável sem subir Odoo) e a
  **execução**, no controlador, pelo ORM e com as ACLs do dono da chave (nunca `sudo` no dado);
  0 SQL nos arquivos da API (AC2). Operações declaradas nesta versão, ambas de leitura:
  `sistema_capacidades` (fonte: sonda de saúde do consumidor, devolve a versão da política e as
  operações declaradas) e `crm_registros_ler` (`res.partner` com os campos do E04-T01 e `crm.lead`
  com o rastreio do E04-T02, só os campos declarados e com teto). Guarda de ambiente do ADR-005:
  sem `tf.api.ambiente` declarado **e** permitido pela política a API recusa tudo (503); homologação
  e produção exigem aprovação humana registrada (`tf.api.aprovacao`, formato
  `card=…,aprovador=…,validade=AAAA-MM-DD`) — e o caminho "permite + aprovado" é medido em política
  de teste, para provar que a guarda é **portão, não parede**. Rastreabilidade: **uma** linha
  `TF_API_AUDIT {json}` por chamada, inclusive nas recusas, sem payload e sem token; resposta sempre
  com `correlation_id`.
  Aceite na VPS (dupla descartável própria, banco `tre_e01_t01_api`): **`API_CONTROLADA_OK (75 itens,
  0 falhas)`**, exit 0 — inclui suíte pura do motor `MOTOR_API_OK (53 itens)`, instalação em banco
  limpo, `0 failed, 0 error(s) of 82 tests` (32 testes novos + 50 dos cards W2, sem regressão),
  **servidor HTTP real medido por `curl` de fora do processo** (401 sem token, 200 com envelope,
  404/405/422/400 no caminho fechado; chave gerada na hora, arquivo 600, fora de `ps`, do stdout e do
  log), auditoria lida do log do servidor (`8 linhas para 8 chamadas autenticadas`; sem token, sem
  `Bearer`, sem payload) e ambiente do dev/homolog/prod medido antes e depois. Dentes:
  **`API_CONTROLADA_DENTE_OK (3 provas, 0 falhas)`** — política sem a operação reprova (404 no lugar
  de 200); motor sem a checagem de campo declarado **vaza o campo `email`** na resposta; motor sem a
  checagem de aprovação passa a atender produção sem aprovação; e o artefato real sai intacto
  (guarda externa por sha256 de 29 arquivos). Runbook e detalhe item a item:
  `docs/runbooks/odoo-api-controlada.md`.
- **Operação de escrita de negócio `oportunidade_upsert` na API controlada** (`TRE-W3-E01-T04`) —
  o espelho da oportunidade canônica entra pela **mesma porta única**, por **declaração** (nenhuma
  linha de controlador): `api/politica_api.json` sobe para `1.1.0` com a operação (`tipo: escrita`,
  modelo `crm.lead`, `acao: upsert`, identidade `tf_opportunity_id`, `name` obrigatório,
  `idempotency_key` exigida, `dry_run` aceito). A **fronteira de dono** do contrato §2 é o desenho:
  estágio, valor, probabilidade e datas do funil **não** são escrevíveis (422 `campo_nao_declarado`,
  nunca silêncio) e `tf_priority_tier` é derivado do score. O rastro (`tf_idempotency_key`,
  `tf_correlation_id`, `tf_last_sync_at`, `tf_last_event_type`) é campo **declarado**, escrito com o
  que o produtor manda; a chave do envelope entra na trilha `TF_API_AUDIT`. Os testes do `E01-T01`
  deixaram de fixar a lista de operações — os itens que falam dela passam a ler o **próprio
  artefato** (âncora `ANCORA:ITEM_DATADO`), para cada card da onda acrescentar a sua sem quebrar o
  anterior. Artefatos: `tests/test_oportunidade_upsert.py` (27 testes),
  `scripts/odoo/verificar-oportunidade-upsert.sh` (aceite + 3 dentes **fail-closed**) e
  `docs/runbooks/odoo-oportunidade-upsert.md`.
  Aceite na VPS (dupla descartável própria, banco `tre_e01_t04_oportunidade`):
  **`OPORTUNIDADE_UPSERT_OK (125 itens, 0 falhas)`**, exit 0 — suíte pura `MOTOR_API_OK (68 itens)`,
  `0 failed, 0 error(s) of 109 tests` (27 novos, sem regressão), **servidor HTTP real medido por
  `curl` de fora do processo** (cria/atualiza/repete; `stage_id` e `expected_revenue` de dono do Odoo
  **idênticos antes e depois**, lidos por SQL; dois leads homônimos continuam dois registros e o
  upsert pelo UUID de um não toca o outro), **18 linhas de auditoria para 18 chamadas autenticadas**
  (sem token, sem `Bearer`, sem payload — nem o payload plantado) e a guarda do ADR-005 na escrita
  medida com o ambiente trocado **pelo ORM** e o servidor reiniciado depois da troca. Dentes:
  **`OPORTUNIDADE_UPSERT_DENTE_OK (3 provas, 0 falhas)`** — política sem a operação, motor sem a
  checagem de campo na escrita e controlador sem o upsert por identidade reprovam **o item esperado**
  (61/98/96 itens medidos por prova), com o artefato real intacto (sha256 de 29 arquivos). O harness
  de dentes é fail-closed: prova que não mede nada **reprova**.

- **Operação de escrita de negócio `atividade_criar` na API controlada** (`TRE-W3-E01-T05`) —
  a **atividade comercial** do Odoo (`mail.activity`) entra pela mesma porta única, por declaração
  (`api/politica_api.json` sobe para `1.3.0`), com **uma** superfície de código: a **tradução da
  âncora** em `models/mail_activity.py` (+ o import em `models/__init__.py` e a dependência `mail` no
  manifesto). A âncora é **valor fixo** da política (`res_model = res.partner`): o chamador **não**
  escolhe o modelo-alvo (divergência, inclusive pelo id interno `res_model_id`, é 422 nomeada) e
  `res_id` é obrigatório. A operação **não** declara identidade — a ação é `criar` e quem garante não
  duplicar é a dedup por chave (`E02-T02`), cuja ausência é **medida** no aceite (replay da mesma
  chave → 2 registros). O rastro `tf_idempotency_key`/`tf_correlation_id` fica **gravado na
  atividade**, além da trilha `TF_API_AUDIT`; a criação passa pela **ACL do dono da chave** (403
  `acesso_negado` com chave sem escrita no documento ancorado — medido com uma segunda chave).
  **Defeito medido do Odoo 19 que justifica o código:** `res_model` é campo *related*, `store=True`,
  `readonly=True` e **sem inverse** — o ORM descarta em silêncio o valor do chamador e o `INSERT`
  morre na CHECK `mail_activity_check_res_id_is_set_if_model`; a política declara o **nome** do
  modelo (id de `ir.model` é id de banco, não vai para artefato versionado) e o módulo resolve o
  nome no `ir.model` do banco em uso. **Reconciliação da onda:** a política em vigor passa a declarar
  a forma de identidade por **lista ordenada** (`campos_de_identidade`, do `E01-T02`) e os leitores do
  artefato (suíte pura do motor, verificador do `E01-T04`, itens de contrato) foram ajustados para a
  forma em vigor — a declaração do `E01-T04` nasceu na forma singular por ter sido construída em ramo
  paralelo. Artefatos: `tests/test_atividade_criar.py` (19 testes),
  `scripts/odoo/verificar-atividade-criar.sh` (aceite + **4** dentes fail-closed, incluindo um sobre a
  tradução da âncora) e `docs/runbooks/odoo-atividade-criar.md`.

- **Upsert de empresa pela API controlada (`TRE-W3-E01-T02`, `t_cdc21b43`)** — a **primeira operação de
  escrita de negócio** da porta única: `empresa_upsert` entra declarada em `api/politica_api.json`
  (versão da política **1.0.0 → 1.1.0**) para escrever em `res.partner` os campos `name`,
  `tf_company_id`, `tf_cnpj`, `tf_domain`, `tf_linkedin_url` e `tf_priority_score` — e **mais nada**
  (campo fora da declaração é 422). A **identidade é declarada, não literal**
  (`campos_de_identidade`): `tf_company_id` (UUID canônico `organizations.id`) primeiro e depois os
  fortes de dedup do contrato §5 (`tf_cnpj` → `tf_domain` → `tf_linkedin_url`); payload sem nenhum
  identificador com valor é recusa nomeada (`422 identificador_ausente`) — a API **não inventa**
  identidade. Semântica de empresa por **valor fixo declarado** (`valores_fixos: {is_company: true}`):
  o parceiro nasce empresa e continua empresa na atualização; chamador tentando decidir esse valor leva
  `422 campo_fixo_divergente`. **Ambiguidade é reportada, nunca resolvida por heurística** (contrato
  §5): se a união dos registros casados pelos identificadores presentes tiver mais de um registro, a
  resposta é `409 valor_ambiguo` e **nada** é escrito nem alterado. O upsert cria **uma vez** e atualiza
  depois (`acao_efetiva: criar|atualizar` + ids na resposta), com contagem conferida no banco.
  **Defeito fechado no card:** a escrita que falhava deixava o registro **gravado** (exceção do ORM
  capturada depois do `INSERT`, envelope `422` com o dado no banco) — a execução passou a rodar dentro
  de um **savepoint**, então recusa não deixa rastro; o teste que mede o antes/depois ficou no card.
  Aceite na VPS (dupla descartável própria, banco `tre_e01_t02_empresa`): **`EMPRESA_UPSERT_OK (104
  itens, 0 falhas)`**, exit 0 — inclui a suíte pura do motor `MOTOR_API_OK (75 itens)`, instalação em
  banco limpo, `0 failed, 0 error(s) of 101 tests` (19 testes novos + 82 dos cards anteriores, sem
  regressão), HTTP real por `curl` de fora do processo (criar/atualizar sem duplicar pela identidade
  canônica e pelos fortes, `409` ambíguo sem escrever, dry-run que não escreve, `503` fora do ambiente,
  `422` de valor fixo/chave/campo fora da declaração) e auditoria lida do log do servidor (**15 linhas
  para 15 chamadas autenticadas**, sem token e sem payload). Dentes: **`EMPRESA_UPSERT_DENTE_OK (3
  provas + 2 controles do próprio harness, 0 falhas)`**. Os dois itens **datados** do aceite do E01-T01
  (versão literal da política e lista fechada de operações) foram reescritos para **ler o próprio
  artefato** — a próxima operação de negócio não os quebra de novo. Runbook:
  `docs/runbooks/odoo-empresa-upsert.md`.
- **Consumidor de outbox em n8n (`TRE-W3-E02-T01`)** — o caminho de consumo da fila
  `sales_intelligence.outbox_events` até a escrita de negócio no CRM, **só** pela porta única
  (`POST /tf/api/v1/<operacao>`, doc 06 §7): nada de XML-RPC, SQL no Odoo ou tabela interna. O
  consumidor é declarado em quatro artefatos versionados — **contrato** (`n8n/contracts/outbox-consumer.v1.json`:
  envelope, eventos aceitos, mapeamento evento→campos da política, operação destino, teto de
  tentativas, classificação HTTP, credenciais por id/nome, entrega serializada), **núcleo em JS puro**
  (`n8n/codigo/nucleo-outbox-consumer.js`, roda em node **e** no Code node) e dois SQL
  (`n8n/sql/ler-pendentes.sql` só leitura, `status = ANY(...)` + `LIMIT` do contrato;
  `n8n/sql/registrar-resultado.sql` grava estado do evento **e** linha de `sync_events` na **mesma
  transação** — o consumidor nunca escreve na tabela da fila, quem escreve é este SQL). O
  **workflow** (`n8n/workflows/TRE-outbox-consumer.json`) é **derivado** deles pelo montador
  (`scripts/n8n/montar_workflow.py`), com o núcleo e o contrato embutidos **byte a byte** nos nós de
  código; a lente estrutural (`scripts/n8n/conferir_contrato_e_workflow.py`, **55 itens**) reprova
  divergência entre contrato, núcleo, SQL e workflow, e o workflow nasce **inativo**. Comportamento
  (contrato §6): evento sem `event_version` é **RECUSADO sem chamada**; `event_type` fora do contrato,
  identidade ausente ou campo exigido ausente são recusados com motivo nomeado; teto de 3 tentativas
  → `DEAD_LETTER` **sem nova chamada**; falha transitória → `RETRY` com motivo em `last_error` **e** na
  trilha; recusa definitiva da API (409 `valor_ambiguo`) → `DEAD_LETTER` com o código do erro.
  **Dois defeitos foram achados pelo próprio aceite e corrigidos na raiz:** (1) sem
  `authentication`/`genericAuthType` no nó HTTP o n8n **ignora** a credencial e o pedido saía sem
  `Authorization` (401 da API) — agora há item que mede o header e uma sonda independente da chave;
  (2) o nó HTTP disparava o lote **em paralelo** e dois eventos da MESMA identidade criavam **dois
  parceiros** (E1/E2 com **2 ms** de intervalo na auditoria do servidor, `acao_efetiva:"criar"` nas
  duas respostas) — a entrega passou a ser **serializada** (batch 1 + intervalo declarados no
  contrato) e o segundo evento passou a **atualizar** o mesmo parceiro (**217 ms** medidos). Aceite na
  VPS, sobre cópia byte a byte, em trio descartável próprio (postgres + odoo + n8n, banco
  `tre_e02_outbox`): **`OUTBOX_CONSUMER_OK (81 itens, 0 falhas)`**, exit 0 — inclui a suíte pura do
  núcleo **`NUCLEO_CONSUMIDOR_OK (87 itens)`** e os 55 estruturais, 7 eventos de fila no ciclo 1
  medidos item a item, Odoo **parado** → `RETRY`/`attempts=1`/trilha `FAILED`, Odoo de volta → o retry
  entrega (`PROCESSED`/`attempts=2`, **uma** linha de trilha, **um** parceiro), evento no teto →
  `DEAD_LETTER` sem chamada e sem escrita, sonda de chave (200 em `dry_run`), chamadas autenticadas
  contadas por **delta** da auditoria, segredo fora do versionado e ambiente medido antes/depois.
  Dentes: **`OUTBOX_CONSUMER_DENTE_OK`** — 4 mutações nomeadas
  (`sem_validacao_de_envelope`, `sem_incremento_de_tentativas`, `sem_teto_de_tentativas`,
  `mapeamento_trocado`), cada uma com veredito **`DENTE_CUMPRIDO`** (o item que a mutação quebra
  reprovou), e o juiz dos dentes conferido por 4 saídas sintéticas (mutação sem efeito, mutação
  cumprida, ambiente quebrado, âncora quebrada) — sem isso, ambiente quebrado viraria "dente
  cumprido". Runbook: `docs/runbooks/n8n-outbox-consumer.md`.
- **Dedup por chave no consumidor de outbox (`TRE-W3-E02-T02`)** — a fila deixou de poder produzir duas vezes
  o mesmo efeito: a **chave de idempotência** derivada do evento (`outbox:<id>:<event_type>`) é consultada na
  **trilha** antes da decisão (uma consulta por lote) e o evento cuja chave já tem linha `COMPLETED` vira
  **REPLAY** — não chama a porta única, **não** incrementa `attempts`, **não** cria linha de trilha (a linha
  existente é reaproveitada: mesmo `id`, mesmo `completed_at`, mesma resposta) e **não** reescreve o registro no
  CRM (mesma chave = mesmo efeito). Chave com trilha `FAILED`/`REFUSED` **não** autoriza replay — o evento volta
  a ser entregue (falha transitória pode não ter escrito nada; recusa é do evento, não da chave). Artefatos:
  contrato **1.1.0** (bloco `dedup`: critério de replay, ordem da decisão, status final), núcleo com a decisão
  REPLAY, dois SQL novos (`n8n/sql/ler-trilha.sql` — uma consulta por lote, só leitura;
  `n8n/sql/registrar-replay.sql` — finaliza o evento reaproveitando a trilha, com guarda fail-closed
  `EXISTS ... status='COMPLETED'`) e o nó de trilha com **`alwaysOutputData`** (sem ele o ciclo sem chave
  entregue mataria a cadeia). O caminho do replay **não** tem nó de HTTP alcançável — a lente mede que só o
  ramo de entrega alimenta a porta única. Aceite na VPS, sobre cópia própria do working tree: `OUTBOX_CONSUMER_OK
  (**97 itens, 0 falhas**)` exit 0 — lente estrutural **93 itens**, suíte do núcleo **124 itens** — com o ciclo 5
  medindo: os dois eventos de volta à fila com os **IDs originais** → **2 eventos na fila e UMA chamada** à API,
  a trilha com as **mesmas 9 linhas** antes e depois, e o parceiro do CRM com `name` e score da entrega original
  preservados. Dentes: `OUTBOX_CONSUMER_DENTE_OK (6/6)` (`sem_consulta_de_trilha` e
  `guarda_de_sucesso_afrouxada` são os dois novos). Runbook: `docs/runbooks/n8n-outbox-consumer.md` §3, §4.4 e §5.
- **Job diário de RECONCILIAÇÃO PostgreSQL × Odoo (`TRE-W3-E04-T01`, `t_2ee17829`)** — o outro lado do
  caminho de escrita: o que foi entregue no CRM é comparado com o que o PostgreSQL declara, e as
  diferenças saem **nomeadas** em vez de silenciosas. O job é **somente leitura** (não grava em tabela
  nenhuma; o produto é o relatório) e **contract-first**: `n8n/contracts/reconciliation-job.v1.json`
  declara fontes, lote (limite/ordem/janela), leituras do destino (campos, filtro, operador, ordem e os
  parâmetros extras de cada leitura), vínculo da trilha, teto de tentativas, janela de pendência,
  comparações (id/tipo/motivo), observações, veredito, regras de fail-closed, credenciais por id/nome e
  o grafo; o **núcleo** (`n8n/codigo/nucleo-reconciliacao.js`, JS puro sem dependência) deriva do
  contrato até os IDs das leituras e o workflow é **gerado** (`TRE-reconciliation.json`), com a lente
  estrutural medindo contrato × SQL × núcleo × workflow × política. Comparações de **entidade** (`E1`
  ausente no destino, `E2` arquivado, `E4` identidade forte divergente, `I1..I4` ID cruzado) e de
  **fila × trilha** (`P1..P4` janela, entrega, teto, recusa), com duas janelas declaradas (lote e
  fila) na linha-resumo: veredito de base parcial não se lê como veredito da base inteira. Fail-closed
  em três frentes medidas: leitura não medida **não** vira divergência (as comparações de entidade são
  puladas e o pulo é nomeado), porta única fora do ar vira `INDETERMINADO` com a regra nomeada em vez
  de execução abortada (`onError: continueRegularOutput` declarado no contrato) e ausência de medição
  nunca é lida como ausência de problema. Na **porta única** do Odoo, a leitura controlada passou a
  poder ver registros **arquivados** por declaração (política **1.4.0**: `leitura_de_arquivados`;
  parâmetro `incluir_arquivados` exige `active` nos campos pedidos e é recusado nomeadamente em
  operação que não declara) — sem isso "espelho arquivado" seria lido como "espelho ausente" e a
  operação criaria de novo o que existe. Aceite na VPS (trio descartável: postgres + odoo + n8n, banco
  `tre_reconc_*`): **`RECONCILIACAO_OK (96 itens, 0 falhas)`**, exit 0 — lente **150 itens**, suite do
  núcleo **61 itens**, 8 mutações nomeadas com vencedor conferido, **7 estados em execução real**
  (`OK` sem divergência; `E1`; `I1`; `E4`; `E2` com o espelho arquivado; `P1,P2,P3,P4`; porta única
  parada → `INDETERMINADO` com regra nomeada e **nenhuma** divergência), **somente-leitura** medido por
  digest em cada rodada, `sha256` dos 12 artefatos idêntico no fecho, instância do dev intocada e o
  **valor** da chave fora do cofre/workflow/log. A suite do módulo roda dentro do aceite
  (`0 failed, 0 error(s) of 192 tests`). Runbook: `docs/runbooks/n8n-reconciliacao.md`.

- **Observabilidade de sincronizacao** (`TRE-W3-E05-T01`) — a superficie de leitura que o consumidor de
  outbox e a trilha de escrita nao tinham: contrato `n8n/contracts/observabilidade-sync.v1.json` **1.0.0**
  (15 metricas declaradas com unidade, regra de ausencia e limiares `alerta`/`critico`; veredito
  `OK/ATENCAO/CRITICO/INDETERMINADO` com exit `0/1/2/3`; regras de fail-closed; vocabulario da trilha com as
  **lacunas declaradas**), as duas consultas **somente-leitura** (`n8n/sql/observabilidade-sync.sql` — fila com
  idade, dead-letter com motivo, grade declarada direcao x status, sucesso sem prova; e
  `n8n/sql/observabilidade-sync-dead-letters.sql` — a lista que da' o MOTIVO), o nucleo
  `n8n/codigo/observabilidade-sync.js` (nenhum id de metrica e nenhum limiar literal no codigo: tudo vem do
  contrato, com verificacao que reprova se aparecer) e o workflow **DERIVADO**
  `n8n/workflows/TRE-observabilidade-sync.json` (id estavel `TREOBSERVSYNC1`, gatilho de agenda + manual,
  **nasce inativo**), montado por `scripts/n8n/montar_workflow_observabilidade.py` (o Code node embute o nucleo
  versionado byte a byte e os nos Postgres embutem os arquivos SQL).
  Medicao na VPS, sobre copia propria do commit: aceite `OBSERVABILIDADE_SYNC_OK` (**119 itens, 0 falhas**)
  exit 0 — lente estrutural **118 itens**, suite do nucleo **58 itens**, montador `--conferir` OK — com 8 estados
  reais semeados (saudavel, falha transitoria, dead-letter sem/com motivo, PROCESSED sem trilha, fila no teto,
  direcao fora do vocabulario, trilha COMPLETED sem conclusao), cada metrica medida **por dois caminhos
  independentes** (`psql` direto e pelo workflow no n8n descartavel) e o retrato das duas tabelas identico
  antes/depois da rodada (somente-leitura provado em execucao real). Prova de dente:
  `OBSERVABILIDADE_SYNC_DENTE_OK` (**12/12**, baseline nao mutado verde, juiz conferido com 7 saidas sinteticas).
  Runbook: `docs/runbooks/observabilidade-sync.md`.

### Fixed

- **Os dois SQL do dedup por chave ficaram fora do verificador de estrutura (`TRE-W3-E02-T02-D01`, defeito
  `t_a1bed5fa`)** — o bloco do consumidor de outbox em `scripts/verificar_estrutura.sh` listava os `n8n/*` do
  `TRE-W3-E02-T01` e **não** foi estendido quando o T02 acrescentou `n8n/sql/ler-trilha.sql` (consulta da
  trilha pelas chaves do lote) e `n8n/sql/registrar-replay.sql` (estado final do replay, guarda fail-closed
  `EXISTS ... 'COMPLETED'`): `grep -c 'ler-trilha\|registrar-replay'` = **0** no head `a38585d` e
  `git log -S'ler-trilha.sql'` naquele arquivo **vazio** — o verificador (rodado por outras trilhas/CI)
  imprimia `PASS` com os dois fora da árvore versionada. Corrigido **onde o gate vive**: os dois caminhos
  entram na lista, **um por linha** (`grep -c` → **2**; o aceite da classe conta linhas, e os dois na mesma
  linha contariam 1), sem tocar na lista de executáveis (são `.sql`, `644`). A entrega do T02 não mudou —
  nenhum arquivo de `n8n/` ou `scripts/n8n/` foi editado. Medido com **controle do defeito** (script anterior
  + `ler-trilha.sql` ausente → `PASS (0 falhas)`, exit 0: o gate era cego) e com **dois dentes** no script
  corrigido, cada mutação desfeita e remedida: **ausência** (`FALHOU ausente n8n/sql/ler-trilha.sql` +
  `FALHOU (1)` exit 1) e **não versionado** (`git rm --cached …` → `FALHOU nao versionado …` exit 1); verde
  de volta em `PASS (0 falhas)` exit 0. Verificadores do projeto no worktree do fix: estrutura, segredos,
  papéis e contrato de dados, todos exit 0. Cherry-pick isolado do commit `a5c3a1f` (nascido de `a38585d`)
  sobre árvore que contém o T02: **0 conflito** e `PASS (0 falhas)` exit 0; em árvore anterior ao T02 o gate
  reprova por ausência — que é exatamente o comportamento pedido.

- **Âncora de dente do dedup apontava só para a mensagem de sucesso (`TRE-W3-E02-T02`)** — na primeira rodada
  do `--prova-de-dente`, o dente `guarda_de_sucesso_afrouxada` saía `NAO_CONTA (âncora quebrada)`: o item do E7
  dizia uma coisa quando passava e outra quando reprovava, e o juiz do dente casa a âncora nas **duas** linhas
  (`OK` e `FALHOU`) — o dente não media nada. Reproduzido o mutante à mão na VPS (`FALHOU E7 esperava
  DEAD_LETTER/2 com valor_ambiguo ..., medido RETRY/1/recusa_da_api:valor_ambiguo`), alinhadas as duas
  mensagens do item e remedido: `OUTBOX_CONSUMER_DENTE_OK (6/6 dentes cumpridos; baseline verde)`. A mesma
  reprodução mediu o **segundo cinto**: com a guarda do núcleo afrouxada o evento **não** é finalizado nem
  reentregue (a guarda `EXISTS` do `registrar-replay.sql` casa zero linhas e o evento fica `RETRY`) — o dano
  aparece no item, sem sucesso inventado.

- **Prova de dente e lente estrutural do consumidor de outbox (`TRE-W3-E02-T01`, rodada 2)** — o modo
  `--prova-de-dente` do aceite fechava com `OUTBOX_CONSUMER_DENTE_OK` e **exit 0 incondicionalmente**: o
  veredito de cada dente era apenas impresso e o contador de falhas do juiz nunca era lido naquele ramo,
  então um ambiente quebrado (imagem inexistente) imprimia 4x `NAO_CONTA` e saía **verde** (fail-open —
  *verde que não pode ficar vermelho não é medição*). Agora o modo é **fail-closed**: sub-run **não
  mutado** (baseline) verde obrigatório antes de contar dente, vereditos agregados por mutação
  (`DENTE_CUMPRIDO`), e qualquer outro veredito / baseline vermelho / juiz com falta fecha com
  `OUTBOX_CONSUMER_DENTE_FALHOU` + exit 1. O item de integridade deixou de ser **afirmação no registro**:
  o `sha256` dos 5 artefatos é **fixado** nas guardas e **reconferido** no fecho (com juiz próprio),
  medido com adulteração real de um artefato no meio da medição. E o item *"nenhum host literal no
  workflow"* da lente estrutural, que era **código morto** (reprovava apenas o loopback), passou a medir o
  texto inteiro do workflow **e** o parâmetro `url` da porta única (2 mutantes próprios reprovam; o
  versionado passa). Aceite remedido na VPS, sobre cópia própria do commit: `OUTBOX_CONSUMER_OK (83 itens,
  0 falhas)` exit 0 e `OUTBOX_CONSUMER_DENTE_OK (4/4 dentes cumpridos; baseline verde)` exit 0 — e o
  controle do revisor (imagem inexistente) agora fecha `DENTE_FALHOU` + exit 1.

- **Upsert de contato pela API controlada (`TRE-W3-E01-T03`, `t_e6e3b0b3`)** — a operação de escrita
  do **contato comercial** (pessoa), pela qual o consumidor espelha o evento `DECISION_MAKER_FOUND`
  (contrato §6) no espelho operacional: `contato_upsert` entra declarada em `api/politica_api.json`
  (versão da política **1.1.0 → 1.2.0**, de forma aditiva, reusando o vocabulário de escrita do
  E01-T02 — sem segundo mecanismo para o mesmo conceito) para escrever em `res.partner` os campos
  `name`, `email`, `is_company`, `function` e `phone` — e **mais nada** (campo fora da declaração é
  422, inclusive `tf_cnpj`, que é campo de empresa). **A identidade é declarada e é o e-mail**
  (`campos_de_identidade: ["email"]`): o mapa canônico da V1 **não** dá UUID de contato do lado Odoo
  (o UUID de `contacts.id` chega ao parceiro por `odoo_partner_id`, coluna **do lado PostgreSQL**),
  então a operação casa pelo identificador natural que existe nos dois lados e é indexado pelo
  contrato §4. Consequências declaradas: contato **sem e-mail** não entra (422 `identificador_ausente`,
  nunca identidade inventada) e e-mail repetido ⇒ **409 `valor_ambiguo`** com **nada** escrito nem
  alterado (ambiguidade é reportada, nunca resolvida — contrato §5). Semântica de **pessoa** por
  **valor fixo declarado** (`valores_fixos: {is_company: false}`) — o mesmo caminho do E01-T02 para
  empresa, sem código; o valor fixo também é aplicado na atualização, e por isso um parceiro-empresa
  que case por e-mail é reescrito como pessoa (risco residual **medido** e declarado no runbook).
  **Defeito fechado neste card:** o motor só recusava o parâmetro `identificador` quando a lista de
  identidade resultante tinha mais de um campo — numa identidade declarada por lista de **um** elemento
  (exatamente este caso) o parâmetro era **aceito e descartado em silêncio** (medido: HTTP 200 com o
  parâmetro ignorado). Passou a decidir pela **forma da declaração**, não pelo tamanho da lista: agora
  é `400 payload_invalido` nomeado, e o item de teste ficou na suíte pura do motor e no aceite HTTP.
  **Fronteira de compliance declarada (AC9):** `do_not_contact`, `opt_out_email`, `opt_out_whatsapp`,
  `preferred_channel` e `legal_basis` são do schema do PostgreSQL (contrato §9) e **não** existem
  neste espelho — enviá-los é recusa nomeada, nunca "ignorado em silêncio". Aceite na VPS (dupla
  descartável própria, banco `tre_e01_t03_contato`): **`CONTATO_UPSERT_OK (110 itens, 0 falhas)`**,
  exit 0 — inclui a suíte pura do motor `MOTOR_API_OK (90 itens)`, instalação em banco limpo,
  `0 failed, 0 error(s) of 120 tests` (19 testes novos + 101 dos cards anteriores, sem regressão),
  HTTP real por `curl` de fora do processo (criar → atualizar no **mesmo** registro com **1** contato
  após 3 chamadas, `409` ambíguo com contagem conferida antes/depois, dry-run que não escreve, `503`
  fora do ambiente medido com servidor novo em `homologacao`, `422` de identidade/valor fixo/campo/
  chave) e auditoria lida do log do servidor (**15 linhas para 15 chamadas autenticadas**, sem token e
  **sem nenhum e-mail do payload** — dado pessoal fora da trilha). Dentes: **`CONTATO_UPSERT_DENTE_OK
  (3 provas + 2 controles do próprio harness, 0 falhas)`**. Runbook:
  `docs/runbooks/odoo-contato-upsert.md`.
- **A consulta de detalhes rodava uma vez por linha de metrica (`TRE-W3-E05-T01`)** — achado do **proprio aceite
  real**: a primeira rodada mediu `outbox_dead_letter (20)` para **um unico** dead-letter. O n8n executa um no'
  com entrada multipla **uma vez por item** e as 20 linhas de metrica viravam 20 execucoes da consulta de
  detalhes (a lista saia multiplicada e o estado sem detalhe virava 20 linhas vazias). Corrigido na raiz com
  `executeOnce` nos dois nos Postgres, declarado no contrato (`workflow.consulta_uma_vez`) e medido pela lente;
  o aceite passou a exigir `outbox_dead_letter (1)` no relatorio.

- **Rodada saudavel fechava INDETERMINADO (`TRE-W3-E05-T01`)** — `alwaysOutputData` (necessario para a cadeia nao
  parar quando nao ha detalhe) entrega **um item vazio**; o nucleo lia isso como "detalhe com tipo nao
  declarado" e reprovava justamente a rodada em que tudo estava bem (`tipo_de_detalhe_nao_declarado:(vazio)`).
  Corrigido na raiz: linha **totalmente vazia** e' AUSENCIA DE DETALHE (regra declarada no contrato,
  `detalhes.linha_vazia`); linha com QUALQUER campo preenchido continua fechando INDETERMINADO. Coberto por item
  proprio na suite e por dente proprio (`placeholder_vira_indeterminado`).

- **Dois itens do aceite que nao mordiam (`TRE-W3-E05-T01`)** — (a) o item do MOTIVO do dead-letter usava `\(` no
  padrao BRE: em BRE o parentese e' literal **sem** barra e `\(` e' agrupamento — o `grep` nunca casava (a falha
  apareceu no aceite r2 e a causa era o proprio padrao); (b) o dente do placeholder saiu `MUTACAO_SEM_DENTE`
  porque a ancora (`nao vira indeterminado`) tambem existia num item vizinho que continuava OK. Corrigido na
  raiz: ancora virou trecho unico, o juiz do dente julga **FALHOU antes de OK** (item vizinho de texto parecido
  nao pode esconder o dente) e o juiz ganhou controle proprio para esse caso (7 saidas sinteticas).

### Notas de estado

- **As escritas de negócio passam a entrar na política real** a partir do `TRE-W3-E01-T04`: a
  operação `oportunidade_upsert` já está declarada na `1.1.0`. A nota do `E01-T01` ("nenhuma
  operação de escrita entra na política real antes do motor de idempotência") descreve o estado
  daquele card; aqui "não duplicar" vem da **identidade canônica** (o UUID) e a chave de
  idempotência é exigida, validada e registrada na trilha — a deduplicação por chave segue sendo do
  `TRE-W3-E02-T02`.
- **Lacunas declaradas da API (por desenho, não por esquecimento)**: o motor de deduplicação por
  `idempotency_key` é do `TRE-W3-E02-T02` — aqui a chave é exigida, validada e registrada, e a
  garantia de "não duplicar" **das operações de escrita de negócio** vem da **identidade declarada**
  (`campos_de_identidade`, `valores_fixos`), medida por contagem no banco; cache de política, rate limit e
  observabilidade durável são do `TRE-W3-E05-T01`. Duas operações de escrita de negócio já estão
  declaradas na política real (versão 1.2.0): `empresa_upsert` (`TRE-W3-E01-T02`) e `contato_upsert`
  (`TRE-W3-E01-T03`); as demais (opportunity upsert e activity create) são dos cards
  `TRE-W3-E01-T04..T05`, que só precisam **declarar** a operação com o mesmo vocabulário de escrita
  — o mecanismo já está entregue e medido.
- **Módulo segue não publicado em `/opt/tre/repo`** (pendência herdada do E03-T01): a API vive no
  módulo, medido em dupla descartável própria; `homolog` e `prod` seguem sem arquivo e sem container.
- **Revisão independente e homologação abertas**: quem entrega não homologa — o veredito deste card
  é do estágio 6 (perfil `tester`) e a ratificação da versão 19.0/homologação (estágio 7) é do
  Anderson.
- **Observabilidade de sync (`TRE-W3-E05-T01`) — lacunas declaradas, por desenho**: (1) **replay/dedup nao tem
  contagem propria** — `outbox_events`/`sync_events` da V1 nao guardam a marca de entrega reaproveitada; contar
  replay exigiria coluna nova (decisao do dono, nova versao do data contract) e o que pega replay quebrado e'
  `outbox_processado_sem_trilha`; (2) **o alerta nao tem rota** — a entrega vai ate' o veredito com exit code e o
  relatorio (canal de notificacao e' decisao do dono); (3) **o contrato da porta de ingestao**
  (`n8n/contracts/odoo-events-ingest.v1.json`, card `TRE-W3-E03-T01`) **nao esta' na base deste card** (`a38585d`):
  as direcoes `odoo->postgres` e os status dessa porta sao declarados a partir do fluxo documentado (doc 06 §7-8) e
  a lente aceita `--ingest <arquivo>` para conferir o espelho de verdade quando o card E03 estiver no base.
- **Observabilidade de sync — nada nasce ligado (ADR-005)**: o workflow `TREOBSERVSYNC1` nasce **inativo**, nao foi
  publicado em nenhuma instancia e nao escreve em banco (nenhum DDL, nenhum DML). Ligar a agenda e' passo de
  operacao, com limiares calibrados pelo dono (mudar limiar e' **nova versao do contrato**, nunca edicao
  silenciosa).
- **Revisao independente e homologacao abertas (`TRE-W3-E05-T01`)**: quem entrega nao homologa — o veredito deste
  card e' do estagio 6 (perfil `tester`) e a homologacao (estagio 7) e' do Anderson.

## [W3 — Integração · E2E Foundation #001] — 02/10/2026

### Added

- **Aceite E2E Foundation #001** (`TRE-W3-E06-T01`) — o cenario do doc 08 §3 medido **ponta a ponta num
  unico trio descartavel** (postgres + Odoo + n8n), encadeando as cinco portas da onda W3:
  - `scripts/e2e/verificar-e2e-foundation-001.sh` + `docs/runbooks/e2e-foundation-001.md`: organizacao
    canonica na fonte da verdade → evento no outbox → consumidor n8n → `empresa_upsert` pela porta unica
    (com o ID devolvido pelo Odoo de volta no `response_payload` da trilha) → `contato_upsert` →
    `atividade_criar` → reconciliacao (E04) e observabilidade (E05) no MESMO trio → repetir o evento sem
    duplicata (`REPLAY`) → `fail-closed` em evento sem `event_version`;
  - **prova de dente fail-closed**: baseline NAO mutado tem de ficar verde antes das 3 mutacoes nomeadas
    (mutador versionado do `TRE-W3-E02-T02`), e cada dente so' conta se o item declarado reprovar no
    sub-run mutado (`NAO_CONTA`/`MUTACAO_SEM_DENTE`/baseline vermelho fecham `DENTE_FALHOU`).
- **Base consolidada da onda W3** (`TRE-W3-E06-T01`) — as cinco branches da onda entram juntas na base do
  E2E (`E01-T05` + `E02-T02` + `E03-T01` + `E04-T01` + `E05-T01`), com as duas listas de artefatos dos
  cards de n8n no mesmo `scripts/verificar_estrutura.sh`; a base congelada e' a de
  `feature/TRE-W3-E06-T01` (commit do merge registrado em `docs/operations/registro-de-execucoes.md`).

### Notas de estado

- **Escopo declarado do E2E**: cobre os passos 1..3 e 11..19 do doc 08 §3 mais o sentido Odoo -> PG pela
  porta de ingestao. Os passos 4..10 (research, signals, scores, tier, pain, recommendation) sao dos cards
  `W4-*`/`W5-*` e aparecem no aceite como linha `DECLARADO` — nunca como `OK`.
- **Lacunas declaradas do E2E**: cenario unitario (nao mede volume nem concorrencia), imagem `odoo:19.0`
  (nao cobre customizacao de instancia) e reprocesso operacional (tem aceite proprio no `TRE-W3-E02-T02`).
- **Revisao independente e homologacao abertas** (`TRE-W3-E06-T01`): quem entrega nao homologa — o veredito
  deste card e' do estagio 6 (perfil `tester`) e a homologacao (estagio 7) e' do Anderson.
- **Aceite medido (02/10/2026, VPS do TRE)**: `RESULTADO: E2E_FOUNDATION_001_OK (139 itens, 0 falhas)` no
  commit `519d8c5`, com os 4 aceites de origem em `--apenas-codigo` no MESMO run; `--prova-de-dente`
  `E2E_FOUNDATION_001_DENTE_OK (3/3, baseline nao mutado verde, juiz e ancoras conferidos)`. Evidencia em
  `/opt/tre/evid-t_fcbe3d7d-r2/` (aceite.out/dente.out/logs) e no `docs/operations/registro-de-execucoes.md`.

## [W4 — Hermes Sales AI · Scout] — 02/10/2026

### Added

- **Agente Scout v1 — descoberta e ingestão de empresa candidata** (`TRE-W4-E01-T01`) — o primeiro agente
  da onda W4: recebe candidatas da fonte, normaliza e valida o forte de identidade (CNPJ com dígito
  verificador, domínio, LinkedIn), decide o veredito (`CRIADA`, `JA_EXISTE`, `REVISAO_IDENTIDADE`,
  `RECUSADA`, `ERRO`) e ingere **sem nunca dar UPDATE em `organizations`**:
  - `hermes/agents/scout/scout.py`, `hermes/agents/scout/agente-scout-v1.json` (contrato legível por
    máquina do próprio agente) e `hermes/agents/scout/exemplos/candidatas-exemplo.jsonl`;
  - identidade por forte: forte válido casado = mesma entidade (`JA_EXISTE`); **dois fortes distintos**
    casando com organizações diferentes = `REVISAO_IDENTIDADE` para a fila humana
    (`human_approvals.action_type = SCOUT_IDENTITY_REVIEW`); sem forte válido **nunca** cria;
  - ingestão idempotente pela chave de identidade em `sync_events.idempotency_key`
    (`ON CONFLICT DO NOTHING`) numa **transação de duas instruções** — o fechamento do evento é comando
    próprio porque as CTEs de escrita e a instrução principal rodam no **mesmo snapshot** (medido no
    aceite: fechando dentro da CTE o evento fica `PENDING` para sempre) — com a marca `SCOUT_CRIADA`
    conferida linha a linha, porque a porta imprime carimbos `BEGIN`/`COMMIT`;
  - guardas fail-closed: sem porta psql declarada **recusa** em vez de improvisar; `--ambiente prod`
    recusado (exit 4); escrita só nas 4 tabelas declaradas, sem DDL; LLM só com recibo válido do JEV;
  - `--planejar` (sem banco, não exige ambiente) e `--desfazer <correlation_id>` (dry-run por padrão;
    `--confirmo` apaga só o que a rodada criou e registra o `ROLLBACK` em `sync_events`).
- **Verificação do Scout** (`TRE-W4-E01-T01`) — `scripts/agentes/verificar_agente_scout.py` (suíte
  offline, 58 itens, autoteste de 12 mutações) e `scripts/agentes/teste_scout_aceite.sh` (aceite E2E em
  container PostgreSQL descartável na VPS, 37 itens + prova de dente com 3 mutações, cada mutação
  exigindo o **item esperado** e a contagem medida no veredito);
  `docs/architecture/agente-scout-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK) e
  `docs/runbooks/agente-scout.md`.
- **Correções depois da revisão independente da 1ª rodada** (todas fail-closed, cada uma com item próprio
  e mutação que o reprova): a prova de dente rodava **1 de 3** mutações — o `docker exec -i` consumia o
  stdin do laço — e imprimia `3/3` literal; o `--autoteste` morria com `IndexError` onde o diretório
  temporário é `/tmp` (a raiz do repo agora é achada por **marcador**, não pela profundidade do arquivo);
  o `rc` da escrita em `human_approvals` e em `agent_runs` era descartado (o agente afirmava
  `REVISAO_IDENTIDADE`/`COMPLETED` sem nada escrito); e a guarda de escrita recusava candidata legítima
  por causa de `Drop`/`Create`/`Alter` **dentro do nome da empresa** (a guarda agora lê o código SQL).

## [W4 — Hermes Sales AI · Research] — 02/10/2026

### Added

- **Agente Research v1 — pesquisa e enriquecimento da empresa descoberta** (`TRE-W4-E02-T01`) — o segundo
  agente da onda W4 e o produtor que faltava entre *Descoberto* e *Pesquisado* (doc 03 §2, doc 07 §7):
  resolve a empresa **que já existe** pelos identificadores fortes do contrato, grava a execução da
  pesquisa em `research_runs` (com evidência, `input_hash` e `source_count`) e **enriquece as colunas
  vazias** da organização, sem nunca sobrescrever o que já estava preenchido:
  - `hermes/agents/research/research.py`, `hermes/agents/research/agente-research-v1.json` (contrato
    legível por máquina do próprio agente) e `hermes/agents/research/exemplos/pesquisas-exemplo.jsonl`;
  - veredito por identidade: forte válido casado com **uma** organização = `PESQUISADA`; replay da mesma
    entrada = `JA_PESQUISADO` (`IDEMPOTENCIA_REPLAY`); dois casamentos distintos = `REVISAO_IDENTIDADE`
    para a fila humana (`human_approvals.action_type = RESEARCH_IDENTITY_REVIEW`); identidade que não casa
    = `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`) — a pesquisa **não cria** empresa, **não escreve**
    identidade forte, `status`, `data_quality_score` nem `deleted_at`, e não detecta sinais, dor ou
    contato (W4-E03/E04/E05);
  - **não sobrescrever é garantido no SQL** (`COALESCE(NULLIF(coluna,''), valor)` em texto e
    `COALESCE(coluna, valor)` em número) e a guarda de escrita **exige** esse formato: um `UPDATE` por
    atribuição direta é recusado pelo próprio agente (medido por mutação, no offline e no E2E);
  - enriquecimento limitado ao **tipo** do pedido (`COMPANY_PROFILE`, `SIZE_AND_STRUCTURE`, `INDUSTRY`,
    `DIGITAL_PRESENCE`), com `employee_band` **derivada** de `employee_count` pelo vocabulário fechado do
    contrato; achado inválido, fora do tipo ou forte é **descartado com motivo** em
    `structured_output.achados_descartados` — nunca escrito em silêncio;
  - ingestão idempotente por `research:org:<uuid>:<tipo>:<input_hash>` em `sync_events.idempotency_key`
    (`ON CONFLICT (idempotency_key) DO NOTHING`), em **três comandos próprios** (claim, enriquecimento e
    fechamento) porque as CTEs de escrita e a instrução principal rodam no **mesmo snapshot** — medido no
    aceite com a chave já reivindicada e o `research_run` ausente (replay silencioso, sem `UNIQUE`
    violation);
  - guardas fail-closed: `--ambiente prod` recusado (exit 4), escrita só nas 5 tabelas declaradas, `DELETE`
    de organização bloqueado, LLM só com recibo válido do JEV, nenhum cliente HTTP no código;
  - a regra de identidade é **importada** do produtor (Scout), não reescrita;
  - `--planejar` (sem banco) e `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo` restaura os
    valores anteriores com o tipo da coluna, apaga os `research_runs`/`sync_events` da rodada e registra o
    `ROLLBACK`, sem tocar `agent_runs` nem `human_approvals`).
- **Verificação do Research** (`TRE-W4-E02-T01`) — `scripts/agentes/verificar_agente_research.py` (suíte
  offline, **65 itens**, autoteste de **15 mutações** com guarda da própria prova) e `scripts/agentes/teste_research_aceite.sh` (aceite
  E2E em container PostgreSQL descartável na VPS, **55 itens** + prova de dente com 4 mutações, cada uma
  exigindo o **item esperado**); `docs/architecture/agente-research-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK)
  e `docs/runbooks/agente-research.md`.
- **Portão de estrutura** (`TRE-W4-E02-T01`) — `scripts/verificar_estrutura.sh` passa a cobrar os 7
  artefatos do agente Research (versionados no git, aceite executável).

## [W4 — Hermes Sales AI · Signal Detector] — 02/10/2026

### Added

- **Agente Signal Detector v1 — detecção de sinais** (`TRE-W4-E03-T01`) — o terceiro agente da onda W4
  fecha o elo que faltava da cadeia do doc 06 (`empresa → pesquisa → signal → hipótese → contato`):
  recebe observações da fonte, resolve a empresa **que já existe** pelos identificadores fortes do
  contrato, valida o tipo contra o vocabulário **fechado** de 18 valores (`vocabularies.signal_type`) e
  grava o **fato datado com evidência** em `signals`:
  - `hermes/agents/signal/signal.py`, `hermes/agents/signal/agente-signal-v1.json` (contrato legível por
    máquina) e `hermes/agents/signal/exemplos/observacoes-exemplo.jsonl`;
  - vereditos `DETECTADO`, `JA_DETECTADO`, `REVISAO_IDENTIDADE`, `RECUSADA`, `ERRO`; **nunca cria
    empresa**: identidade que não casa é `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`), identidade ambígua
    (dois fortes casando com organizações distintas) vai para a fila humana
    (`human_approvals.action_type = SIGNAL_IDENTITY_REVIEW`);
  - `signal_category` é **derivada** do tipo por tabela declarada (coberta uma vez por tipo, conferida
    pela suíte) — categoria declarada pela fonte é descartada com motivo, como no `employee_band` do
    Research;
  - **nenhum score é escrito**: `buying_signal_points`, `relevance_score`, `decay_factor` e `expires_at`
    ficam fora da escrita (o Buying Signal Score é `TRE-W5-E03-T01`) e a guarda **recusa** quem tentar;
  - **nenhuma coluna de `organizations` é tocada** (o sinal é aditivo): a guarda recusa escrita em
    empresa **pelo motivo de desenho**, com mensagem que nomeia a regra;
  - idempotência na **entrada** (`signal:org:<uuid>:<tipo>:<input_hash>` em
    `sync_events.idempotency_key` UNIQUE) numa transação de **dois comandos de escrita**: claim +
    `INSERT` do sinal ancorado no claim, e fechamento (`UPDATE`) ancorado no sinal **desta rodada** — o
    replay não insere e não marca sucesso; a **fila humana tem o mesmo claim** (`signal:revisao:…`),
    então a mesma ambiguidade reapresentada não abre pedido duplicado;
  - vínculo lógico com o `research_run` que motivou a detecção (`signals.research_run_id`, sem FK no
    contrato) é **conferido por existência**: vínculo quebrado é descartado com motivo e o sinal segue;
  - evidência conservada: fontes (a primeira é a primária em `source_type`/`source_url`), trechos,
    `input_hash` e **todo descarte com campo, motivo e valor** em `signals.evidence` e no
    `sync_events.request_payload`;
  - guardas fail-closed: `--ambiente prod` recusado (exit 4), escrita só nas 4 tabelas declaradas,
    `DELETE` só no desfazer, LLM só com recibo válido do JEV, nenhum cliente HTTP no código, ambiente
    não declarado recusado; `--planejar` não abre conexão;
  - `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo` apaga os sinais da rodada e os
    `sync_events` deles e registra o `ROLLBACK`, sem tocar `organizations`, `research_runs`, `agent_runs`
    nem `human_approvals`).
- **Verificação do Signal Detector** (`TRE-W4-E03-T01`) — `scripts/agentes/verificar_agente_signal.py`
  (suíte offline, **75 itens**, autoteste de **21 mutações** com guarda da própria prova) e
  `scripts/agentes/teste_signal_aceite.sh` (aceite E2E em container PostgreSQL descartável na VPS,
  **64 itens** + prova de dente com 4 mutações, cada uma exigindo o **item esperado**);
  `docs/architecture/agente-signal-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK) e
  `docs/runbooks/agente-signal.md`.
- **Portão de estrutura** (`TRE-W4-E03-T01`) — `scripts/verificar_estrutura.sh` passa a cobrar os 7
  artefatos do agente Signal Detector (versionados no git, aceite executável).

### Fixed

- **`sync_events.entity_type/entity_id` do sinal apontavam para a empresa** (`TRE-W4-E03-T01`) — defeito
  medido no aceite E2E: com `entity_id = organization_id` a consulta do desfazer (`e.entity_id = s.id`)
  não achava nada e o `--desfazer --confirmo` **não apagava sinal nenhum** (4 itens reprovados). Corrigido
  para `entity_type='signal'`/`entity_id=<sinal>` (a empresa fica no payload) e coberto por item próprio
  (`sync-event-do-sinal-aponta-o-sinal`) mais mutação que o reprova.

## [W4 — Hermes Sales AI · Pain Hypothesis] — 02/10/2026

### Added

- **Agente Pain Hypothesis v1 — hipótese de dor com lastro em evidência** (`TRE-W4-E04-T01`) — o quarto
  agente da onda W4 fecha o penúltimo elo da cadeia do doc 06 (`empresa → pesquisa → signal → hipótese →
  contato`) e o passo 9 do E2E #001 do doc 08 §3: recebe hipóteses da fonte, resolve a empresa **que já
  existe** pelos identificadores fortes do contrato, **confere que o lastro declarado existe de verdade e
  é da MESMA empresa** e grava a inferência marcada como inferência em `pain_hypotheses`:
  - `hermes/agents/pain_hypothesis/pain_hypothesis.py`, `hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json`
    (contrato legível por máquina) e `hermes/agents/pain_hypothesis/exemplos/hipoteses-exemplo.jsonl`;
  - vereditos `REGISTRADA`, `JA_REGISTRADA`, `REVISAO_IDENTIDADE`, `RECUSADA`, `ERRO`; **nunca cria
    empresa**: identidade que não casa é `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`) e identidade ambígua
    vai para a fila humana (`human_approvals.action_type = PAIN_IDENTITY_REVIEW`);
  - **hipótese sem lastro não é gravada**: ≥1 evidência declarada tem de existir no banco
    (`SINAL → signals.id`, `PESQUISA → research_runs.id`) **e** ser da empresa resolvida — evidência
    inexistente (`EVIDENCIA_NAO_ENCONTRADA`) e evidência **de outra empresa**
    (`EVIDENCIA_DE_OUTRA_ORGANIZACAO`, o caso que engana: o id existe) são descartadas com motivo e, sem
    nenhuma válida, o veredito é `RECUSADA` (`SEM_EVIDENCIA_VALIDA`);
  - `pain_category` vem do vocabulário da baseline (doc 01 §5: `FINANCEIRO`, `COMERCIAL`, `ATENDIMENTO`,
    `OPERACOES`, `DOCUMENTOS`), declarado no contrato **deste** agente — o Data Contract V1.0 fecha apenas
    `pain_hypotheses.status`, e não se inventa vocabulário dentro de contrato congelado;
  - **`status` é sempre o inicial do contrato** (`HYPOTHESIS`) e **nenhum impacto é calculado**:
    `business_impact_score`, `estimated_impact_description` e `validated_at` ficam fora da escrita (não há
    fórmula de impacto homologada e validação é ato humano) e a guarda **recusa** quem tentar;
  - `evidence` conserva o lastro com o **fato de origem** (`signal_type`/`signal_category`/`event_date`
    ou `research_type`/`status`), a evidência primária, o `input_hash`, o `correlation_id` e **todo
    descarte com campo, motivo e valor**; `evidence.inferencia = true` cumpre o "inferência marcada como
    inferência" do doc 12 §8;
  - **nenhuma coluna de `organizations`, `signals` ou `research_runs` é tocada** (o registro é aditivo): a
    guarda recusa escrita em empresa **pelo motivo de desenho**, com mensagem que nomeia a regra;
  - idempotência na **entrada declarada** (`pain:org:<uuid>:<input_hash>` em `sync_events.idempotency_key`
    UNIQUE) numa transação de **dois comandos de escrita**: claim + `INSERT` ancorado no claim, e
    fechamento ancorado na hipótese **desta rodada** — o replay não insere e não marca sucesso; a fila
    humana tem o mesmo claim (`pain:revisao:…`);
  - o vínculo de origem (`pain_hypotheses.research_run_id`, **FK de verdade** no DDL — ao contrário do
    vínculo lógico do sinal) é conferido por existência **e dono** antes de ser escrito: vínculo quebrado é
    descartado com motivo e a hipótese segue sem ele (escrever um id inexistente derrubaria o `INSERT`);
  - guardas fail-closed: `--ambiente prod` recusado (exit 4), escrita só nas 4 tabelas declaradas,
    `DELETE` só no desfazer, LLM só com recibo válido do JEV, nenhum cliente HTTP no código, ambiente não
    declarado recusado; `--planejar` não abre conexão;
  - `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo` apaga as hipóteses da rodada e os
    `sync_events` delas e registra o `ROLLBACK`, sem tocar `organizations`, `research_runs`, `signals`,
    `agent_runs` nem `human_approvals`).
- **Verificação do Pain Hypothesis** (`TRE-W4-E04-T01`) — `scripts/agentes/verificar_agente_pain_hypothesis.py`
  (suíte offline, **85 itens**, autoteste de **27 mutações** com guarda da própria prova) e
  `scripts/agentes/teste_pain_hypothesis_aceite.sh` (aceite E2E em container PostgreSQL descartável na VPS,
  **85 itens** + prova de dente com 5 mutações, cada uma exigindo o **item esperado**);
  `docs/architecture/agente-pain-hypothesis-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK) e
  `docs/runbooks/agente-pain-hypothesis.md`.
- **Portão de estrutura** (`TRE-W4-E04-T01`) — `scripts/verificar_estrutura.sh` passa a cobrar os 7
  artefatos do agente Pain Hypothesis (versionados no git, aceite executável).

## [W4 — Hermes Sales AI · Contact Research] — 02/10/2026

### Added

- **Agente Contact Research v1 — contato comercial da empresa já pesquisada** (`TRE-W4-E05-T01`) — o
  terceiro agente da onda W4 e o produtor do `contacts`: resolve a empresa **que já existe** (nunca
  cria organização), casa o contato pela **identidade de e-mail** (`lower(email)` nos dois lados),
  cria o que não existe e **enriquece** o que existe sem sobrescrever coluna preenchida:
  - `hermes/agents/contact_research/contact_research.py`, `hermes/agents/contact_research/agente-contact-research-v1.json`
    (contrato legível por máquina do próprio agente) e `hermes/agents/contact_research/exemplos/contatos-exemplo.jsonl`;
  - veredito por identidade: empresa casando **uma** vez = cria/enriquece (`IDENTIFICADO`); replay da
    mesma entrada = `JA_IDENTIFICADO`; dois casamentos distintos = `REVISAO_IDENTIDADE` para a fila
    humana (`human_approvals.action_type = CONTACT_IDENTITY_REVIEW`); identidade que não casa =
    `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`) — o agente **não cria** empresa, **não escreve** o
    e-mail (identidade), `odoo_partner_id`, `do_not_contact`, `opt_out_email`, `opt_out_whatsapp` nem
    scores, e não detecta sinais, dores ou recomendações (W4-E03/E04);
  - **não sobrescrever é garantido no SQL** (`COALESCE(NULLIF(coluna,''), valor)`) e a guarda de
    escrita **exige** esse formato; `UPDATE` por atribuição direta é recusado pelo próprio agente
    (medido por mutação, no offline e no E2E);
  - identidade do contato sem diferença de caixa/espaço; achado inválido, papel de decisão fora do
    vocabulário fechado ou campo fora do DDL do `contacts` é **descartado com motivo** em
    `output.descartados` — nunca escrito em silêncio;
  - ingestão idempotente por `contact:<correlation>:<sha256 do pedido>` em
    `sync_events.idempotency_key` (`ON CONFLICT (idempotency_key) DO NOTHING`), com **toda** instrução
    da rodada ancorada no `sync_event` daquela rodada (`... WHERE id = <id> AND status = 'PENDING'`) e
    `antes`/`depois` gravados no `request_payload` — é dele que o `--desfazer` tira a restauração;
  - `DECISION_MAKER_FOUND` é **medido, não emitido** na v1 (o consumidor versionado
    `n8n/contracts/outbox-consumer.v1.json` não cobre o evento): a elegibilidade fica como evidência em
    `agent_runs.output.evento_de_espelho` e a fila de outbox fica **vazia** — lacuna declarada no doc
    de arquitetura (§12);
  - guardas fail-closed: `--ambiente prod` recusado (exit 4), escrita só nas 4 tabelas declaradas,
    `DELETE` de contato só no desfazer, LLM só com recibo válido do JEV, nenhum cliente HTTP no código;
  - `--planejar` (sem banco) e `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo`
    restaura as colunas enriquecidas, apaga os contatos **criados** pela rodada e os `sync_events` da
    rodada, registra o `ROLLBACK` e **recusa** a rodada cujo contato já foi espelhado no CRM).
- **Verificação do Contact Research** (`TRE-W4-E05-T01`) — `scripts/agentes/verificar_agente_contact_research.py`
  (suíte offline, **60 itens**, autoteste de **25 mutações** com guarda da própria prova) e
  `scripts/agentes/teste_contact_research_aceite.sh` (aceite E2E em container PostgreSQL descartável na
  VPS, **65 itens** + prova de dente com 4 mutações, cada uma exigindo o **item esperado**);
  `docs/architecture/agente-contact-research-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK) e
  `docs/runbooks/agente-contact-research.md`.
- **Portão de estrutura** (`TRE-W4-E05-T01`) — `scripts/verificar_estrutura.sh` passa a cobrar os 7
  artefatos do agente Contact Research (versionados no git, aceite executável).

## [W4 — E2E Sales Intelligence · a cadeia dos cinco agentes] — 02/10/2026

### Added

- **Aceite E2E da cadeia Sales Intelligence** (`TRE-W4-E06-T01`) — a onda W4 medida **de ponta a
  ponta**, na ordem real e num único banco descartável: Scout → Research → Signal → Pain Hypothesis →
  Contact Research, com o que cada agente escreve sendo exatamente o que o próximo resolve:
  - `scripts/e2e/verificar-e2e-sales-intelligence.sh` (**76 itens** no cenário + **5 itens** no
    passo 0, cada linha com o VALOR medido), `docs/architecture/e2e-sales-intelligence.md`
    (ACCEPTANCE/TEST/ROLLBACK/RISK) e `docs/runbooks/e2e-sales-intelligence.md`;
  - **passo 0 — regressão das cinco suítes offline** (58 + 65 + 75 + 85 + 60 itens) no **mesmo commit**
    do aceite: o veredito diz em que commit a cadeia foi medida, e contrato de agente quebrado reprova
    antes de qualquer banco;
  - **o encadeamento é medido, não narrado**: as fontes do Signal e do Pain são **geradas** dos
    relatórios de Research/Signal, e o `research_run_id`/`signal_id` que elas declaram saem do
    **relatório da rodada** (não da fixture) — se um agente parar de gravar o vínculo, o item do
    próximo reprova (`pesquisa-rodada1-run-aponta-a-empresa-do-scout`,
    `cadeia-sinal-vincula-a-pesquisa-da-mesma-empresa`, `cadeia-hipotese-lastro-de-pesquisa`);
  - **replay** das cinco rodadas com as **mesmas fontes** = zero duplicata nas cinco tabelas de negócio
    (assinatura antes/depois); a fila humana é **re-reportada** (2 `PENDING`) e o contato ambíguo segue
    **não escrito** — a idempotência vale para o dado de negócio, não para o pedido de revisão;
  - **desfazer na ordem inversa** (contato → hipótese → sinal → pesquisa → scout) devolve o banco ao
    estado inicial (`0|0|0|0|0`), **preserva a fila humana** e registra os 5 `ROLLBACK`; o desfazer do
    Research restaura a coluna que ele enriqueceu e deixa intacto o que o Scout escreveu;
  - guardas: `--ambiente prod` recusado **nos cinco** (exit 4) sem escrever nada; `--planejar` sem porta
    de escrita; container descartável próprio (`pg-e2e-si-acc`) e os containers do TRE **intactos**;
  - **prova de dente, 5/5**: uma mutação por agente, em cópia do código, cada uma exigindo o **item
    esperado** — `scout-escreve-empresa-sem-identidade`, `pesquisa-run-sem-organizacao`,
    `sinal-anexa-run-inexistente`, `hipotese-aceita-lastro-de-outra-empresa`,
    `contato-sem-idempotencia`;
  - **defeito da prova, corrigido nesta rodada (medido, não suposto)**: as mutações óbvias de "duplicar
    no replay" (Scout) e "sobrescrever coluna" (Research) ficaram **inertes/verdes** — as duas
    propriedades têm **duas camadas independentes** (claim por identidade + resolução na base;
    escolha de coluna vazia + `COALESCE(NULLIF(coluna,''), valor)` exigido pela guarda). O dente do E2E
    passou a mirar o que **só a cadeia** mede: o vínculo entre o que um agente escreve e o que o
    próximo resolve.

## [W5 — Scoring · Tiering v1] — 02/10/2026

**Card:** TRE-W5-E06-T01 (baseline V1.1.0, base `feature/TRE-W5-E05-T01`) ·
**O que é:** classifica a empresa na **faixa de tier** do Data Contract — lê o **último score
`PRIORITY`** já gravado em `sales_intelligence.scores`, aplica as faixas de `scores.tiers`
(`A+` 90–100 · `A` 80–89,99 · `B` 65–79,99 · `C` 50–64,99 · `Nurture` < 50) e grava a classificação no
**registro auditado** da rodada.

### Added

- **Tiering v1** (`hermes/scores/tiering/tiering.py` + `score-tiering-v1.json`) — as **faixas são lidas
  do Data Contract** (`scores.tiers`) e **nenhum limite nem nome de faixa existe em forma executável no
  código** (item `C3` da suíte, por AST, reprova se aparecer); a cobertura da escala (0 a 100, sem
  lacuna e sem sobreposição) é conferida a cada rodada e um contrato que não represente a escala
  **RECUSA** (`CONTRATO_INCOERENTE`) em vez de classificar com regra própria.
- **Fail-closed na ausência e no vencimento:** empresa **sem `PRIORITY` RECUSA** (`SEM_PRIORITY`) e não
  registra nada — `Nurture` é a faixa dos scores **baixos**, nunca o rótulo de quem não tem evidência;
  `PRIORITY` com `valid_until` no passado RECUSA (`PRIORITY_VENCIDO`), lido do banco (política de
  validade nascida no E05).
- **Persistência sem mudar o contrato:** o tier vive em `sync_events` (`operation='TIER'`,
  `source_version='tiering-v1'`, `idempotency_key` única e `request_payload` com tier, faixa, faixas
  vigentes, escala e a identidade do `PRIORITY` lido) + `agent_runs` (auditoria, inclusive da recusa).
  **Nada em `scores`**: tier não é score e criar coluna/`score_type` novo exige versão nova do contrato
  (governança §10 + ADR-0004) — a guarda de escrita **recusa** qualquer escrita em `scores`. Mesma
  decisão do precedente `entity_match_confidence` (TRE-W1-E04-T02).
- **Suíte offline** (`scripts/scores/verificar_score_tiering.py`) — **26 itens / 0 falhas** e autoteste
  com **10/10 mutações** detectadas, cada uma pelo item esperado.
- **Aceite E2E** (`scripts/scores/teste_tiering_aceite.sh`) — PostgreSQL descartável `pg-tier-acc`
  (removido pelo próprio aceite): `ACEITE_TIERING_001_OK (53 OK / 0 FALHOU)` com **6 dentes**, cada um
  reprovando o item esperado (ausência virando registro, vencimento desligado, fronteira aberta,
  idempotência desligada, leitura virando o primeiro score, operation trocada).
- **Docs do card:** `docs/architecture/score-tiering-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK) e
  `docs/runbooks/score-tiering.md`; portão da W5 (`scripts/verificar_estrutura.sh`) cobre os artefatos.

### Changed

- **Portão de estrutura da W5** (`scripts/verificar_estrutura.sh`) passa a exigir os artefatos do
  tiering (código, contrato do card, suíte, aceite executável, arquitetura e runbook).

### Lacunas declaradas (proposta a homologar — decisão do dono)

- O Data Contract V1.0 **não define casa física** para o tier: proposto `organizations.tier` (contrato
  1.1) ou `score_type='TIER'`; **não feito por conta própria**.
- A política de ausência (**sem PRIORITY ⇒ sem tier**) é proposta deste card; a alternativa (`Nurture`
  como default) foi recusada por inverter o sentido do dado.
- O tier reflete o último `PRIORITY` e não vence por si — quem vence é o `PRIORITY` (30 dias, E05).

## [W5 — Scoring · Priority Score v1] — 02/10/2026

**Card:** TRE-W5-E05-T01 (baseline V1.1.0, base `63d711c` + branch dos quatro componentes) ·
**O que é:** o **agregador** da onda — lê o último score de cada componente já gravado em
`sales_intelligence.scores` (ICP, AUTOMATION_FIT, BUYING_SIGNAL, DATA_QUALITY) e grava a linha
`score_type='PRIORITY'`, `score_version='priority-v1'`.

### Added

- **Priority Score v1** (`hermes/scores/priority/priority_score.py`) — `PRIORITY = 0.35*ICP +
  0.30*AUTOMATION_FIT + 0.25*BUYING_SIGNAL + 0.10*DATA_QUALITY`. Os **pesos são lidos do Data
  Contract** (`scores.priority_weights`, que somam 1,00) e **nenhum peso existe em forma executável
  no código** — item próprio da suíte reprova se aparecer um. Cada linha é reconstruível:
  `inputs` guarda a identidade dos quatro componentes usados (id, versão, valor, `calculated_at`,
  `valid_until`) e `explanation` guarda peso, valor e **parcela** de cada um.
- **Fail-closed na ausência (sem renormalização)** — componente ausente ou **vencido** não pontua e
  o score é **RECUSADO** (`SEM_LASTRO_COMPLETO` + motivo nominal por componente). Ler ausência como
  zero puniria a empresa por um score que ninguém calculou; renormalizar criaria um número que não é
  a fórmula do contrato (declarado como `priority-v2`, decisão do dono).
- **Política de validade do score nasce aqui** (lacuna declarada pelos cards irmãos): componente com
  `valid_until` no passado é lido do banco e tratado como ausente (`COMPONENTE_VENCIDO`), e o próprio
  PRIORITY vence em **30 dias** (`valid_until = calculated_at + 30d`).
- **Idempotência pela ENTRADA** — chave `score:PRIORITY:<org>:<entrada_hash>` em
  `sync_events.idempotency_key` (UNIQUE), com o hash sobre a **identidade dos componentes** (nunca o
  relógio da rodada, defeito medido no card irmão): replay não duplica; componente novo grava **linha
  nova** e preserva a anterior (score é histórico).
- **Guarda de escrita própria** — só `scores`/`agent_runs`/`sync_events`; `UPDATE` em `scores`,
  `INSERT` com outro `score_type`, DDL e escrita nas tabelas de entrada (`signals`, `organizations`,
  `pain_hypotheses`, `research_runs`) são **recusados** por `validar_sql` (porta própria: guarda de um
  agente não vale como guarda de outro).
- **A recusa é auditada** — `RECUSADA` grava `agent_runs` com status `REJECTED` e o relatório passou a
  imprimir o **motivo** na linha do veredito (antes só os motivos nominais apareciam).
- `hermes/scores/priority/score-priority-v1.json` (contrato legível por máquina),
  `docs/architecture/score-priority-v1.md` (**ACCEPTANCE, TEST, ROLLBACK, RISK** — campos exigidos
  pelo doc 11 §2 e não detalhados lá) e `docs/runbooks/score-priority.md` (operação item a item).

### Fixed

- **Defeito do próprio aceite (medido, não suposto):** o dente `sem-soma-de-um-componente` saía
  "não reprovou" com o item reprovando — o `grep` sem `-F` interpretava `0,35*94` como expressão
  regular (`5*` = "zero ou mais 5"), então a linha de reprovação **nunca casava**. Passou a `grep -qF`.
- **Defeito do próprio aceite:** o item `A2` media "nenhum componente alterado" com
  `score_value = ROUND(score_value,2)` (sempre verdadeiro em `NUMERIC(5,2)`). Passou a comparar uma
  **impressão digital md5** (tipo+valor+versão) dos componentes antes e depois da rodada.
- **Defeito medido pela mutação:** a linha de resumo do cálculo assumia os quatro componentes
  presentes (`detalhe[t]["score_value"]`) e estourava `KeyError` em qualquer contrato sem cobertura
  total — passou a tratar componente ausente (`-`).

### Notas de estado

- **Medido por execução real (não narrado):** suíte offline **35 itens / 0 falhas** + autoteste
  **12/12 mutações detectadas, cada uma pelo item esperado**; aceite E2E em PostgreSQL descartável na
  VPS (`pg-priority-acc`, `postgres:16`, migration 0001 aplicada do zero) → **`ACEITE_PRIORITY_001_OK`
  (51 itens, 0 falhas)** com **dente 5/5**; portão de estrutura **PASS (0 falhas)**.
- **Valores conferidos no banco:** `0,35*94 + 0,30*76 + 0,25*83 + 0,10*100 = 86,45`; componente novo
  (ICP 100) ⇒ **88,55** em linha nova com o 86,45 preservado; falta DATA_QUALITY ⇒ **RECUSADA sem
  escrever**; DATA_QUALITY vencido ⇒ **RECUSADA** com `COMPONENTE_VENCIDO` e, dentro da validade,
  volta a calcular; `prod` recusado **exit 4** com **0** escrita; `--planejar` **não abre conexão**.
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6**
  (revisão independente, perfil `tester`) e a **homologação (estágio 7)** é do Anderson. A política de
  cobertura (1,00, sem renormalização) e a de validade (30 dias) são **propostas** deste card.
- **Fora do card**: tiering `A+/A/B/C/Nurture` (W5-E06-T01), Next Best Action (W5-E07-T01), evento de
  outbox `PRIORITY_SCORE_CHANGED`/`COMPANY_QUALIFIED` (W3) e o aceite E2E da cadeia W5 (W5-E08).

## [W5 — Scoring · ICP Score V1] — 02/10/2026

### Added

- **Agente ICP Score v1 — fit estrutural da organização com o cliente desejado** (`TRE-W5-E01-T01`) — o
  primeiro score da onda W5: lê a organização **no banco** (`sales_intelligence.organizations`), calcula o
  score `ICP` (0–100) e grava em `scores` com `score_type='ICP'`, `score_version` do modelo, `inputs` (o que
  foi lido) e `explanation` (como o número saiu). A fórmula V1 nasce aqui como **proposta a homologar** — o
  baseline (doc 03 §3) nomeia o score e não define fórmula; o Data Contract dá só o contexto de negócio
  (`scores.icp_context`: faixa 70–1.000, sweet spot 150–700, cinco ICPs):
  - `hermes/agents/icp_score/icp_score.py`, `hermes/agents/icp_score/agente-icp-score-v1.json` (contrato
    legível por máquina: pesos, faixas, vocabulário de segmento e motivos) e
    `hermes/agents/icp_score/exemplos/organizacoes-exemplo.jsonl`;
  - **modelo V1** (`icp-v1.0.0`): `0,45 * segmento + 0,35 * porte + 0,20 * modelo_b2b`, pesos somando 1,00
    **lidos do contrato** (nenhum peso em forma executável no código — item próprio da suíte reprova se um
    aparecer); porte usa `employee_band` ou deriva de `employee_count` pela mesma regra do Scout; segmento
    casa o `industry_code + industry_name` contra os cinco ICPs do contrato por **vocabulário declarado**,
    com fronteira de palavra (`'descarga'` não é `'carga'`) e **ambiguidade registrada** em
    `segmentos_casados` quando mais de um ICP casa;
  - **ausência de dado não vira fit**: componente sem dado reconhecido pontua 0 **e registra o motivo**
    (`SEGMENTO_NAO_INFORMADO`, `PORTE_ABAIXO_DO_ICP`, `MODELO_B2C_FORA_DO_ICP`, …) — quem mede dado faltante
    é o Data Quality Score (W5-E04);
  - **idempotência com histórico**: chave `icp:score:<org>:<modelo>:<fingerprint[:16]>`, onde o fingerprint é
    o sha256 dos campos que mudam o score (indústria, `employee_count`, `employee_band`, faixa efetiva,
    `business_model`) — mesmos dados ⇒ replay sem linha nova; dado alterado ⇒ score novo e o anterior
    preservado (score é histórico, não mutável); `agent_runs` sem `model`/tokens/custo (v1 não chama LLM) e
    `sync_events` fechando só quando o score da rodada existe (mesma armadilha de snapshot medida na W4);
  - `--planejar` (calcula da fonte, sem abrir conexão) e `--desfazer <correlation_id>` (dry-run por padrão;
    `--confirmo` apaga só os scores da rodada, preserva `agent_runs` e registra o `ROLLBACK`); guardas
    fail-closed: sem porta psql **recusa**, `--ambiente prod` recusado (exit 4) e escrita só em
    `scores`/`agent_runs`/`sync_events` com `UPDATE` em `scores` proibido (score não se reescreve).
- **Verificação do ICP Score** (`TRE-W5-E01-T01`) — `scripts/agentes/verificar_agente_icp_score.py` (suíte
  offline, **67 itens, autoteste de 21 mutações**) e `scripts/agentes/teste_icp_score_aceite.sh` (aceite em
  container PostgreSQL descartável `pg-icp-acc` na VPS, **50 itens** + prova de dente com 9 mutações, cada
  uma exigindo o **item esperado**); `docs/architecture/agente-icp-score-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK)
  e `docs/runbooks/agente-icp-score.md`.
- **Valores medidos no aceite** (não narrados): distribuidora B2B no sweet spot **100,00**; logística 900
  colaboradores **86,00**; consultoria/tema tech B2B2C **94,00**; varejo B2C de 30 colaboradores **0,00**;
  organização sem dado **0,00 com os três motivos**; porte derivado do `employee_count` sem `employee_band`
  **100,00**; organização apagada e uuid inexistente **RECUSADA sem escrever**; fonte **mentindo** todos os
  campos de score da empresa que está no banco ⇒ replay e score intacto (a fonte escolhe o sujeito, não o
  dado); mudança de `employee_band` no banco ⇒ score novo com o anterior preservado (2 linhas).

### Fixed

- **Defeitos da PRÓPRIA prova, encontrados e corrigidos nesta rodada** (o agente não apresentou defeito no
  que foi medido):
  1. **mutação inerte por defesa em camadas**: liberar `prod` na lista de ambientes permitidos **não** muda
     nada — a checagem explícita de `prod` recusa antes. O dente passou a mirar a **ligação** da guarda no
     `main` (guarda que existe e não é chamada), que é o defeito que o aceite tem de pegar;
  2. **item que lia o repositório, não o código sob teste**: a checagem de raiz por marcador lia o arquivo do
     repo, então a mutação (que vive numa cópia) passava verde; agora o texto analisado é o do **módulo
     carregado**;
  3. **mutações com alvo mal escolhido**: `sem-faixa-derivada` e `ausencia-de-porte-vira-fit` não podiam
     reprovar itens que usam `employee_band` do banco; as expectativas foram corrigidas para os itens que de
     fato dependem da regra mutada.
- **Itens do aceite que mediam a coisa errada**: o item de `inputs`/`explanation` excluía a organização sem
  dado (`origem_do_porte='ausente'`) e o item dos três motivos comparava a lista **ordenada
  alfabeticamente** em vez da ordem dos componentes — os dois passaram a medir o que declaram.

### Notas de estado

- **Portão de estrutura:** `scripts/verificar_estrutura.sh` → **PASS (0 falhas)** com os artefatos do card
  versionados e o aceite executável, o mesmo portão que já cobria Scout, Research, Signal, Pain e
  Contact Research.
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6** (revisão
  independente, perfil `tester`) e a homologação (**estágio 7**) é do Anderson. A fórmula V1 é **proposta**;
  se homologada, o peso passa a ser parte do contrato de dados (nova versão 1.1), decisão do dono.
- **Fora do card**: tier, Priority Score, Next Best Action, `valid_until`, evento `COMPANY_QUALIFIED`,
  espelhamento no Odoo (W3) e o aceite E2E da cadeia W5 (W5-E08). O aceite E2E da W4 continua medindo
  `scores` **vazio** — ele roda no banco descartável dele e nenhum agente da W4 escreve score.
## [W5 — Scoring · Buying Signal Score v1] — 02/10/2026

### Added

- **Buying Signal Score v1 — o score `BUYING_SIGNAL`** (`TRE-W5-E03-T01`) — primeiro componente da W5:
  transforma os `signals` do Signal Detector no número que o Data Contract §8 reserva desde o W0
  (peso 0,25 do Priority Score) e que **nenhum** componente calculava. Fórmula V1 congelada como
  `buying-signal-v1`: `pontos = peso_do_tipo × confiança × decaimento` (meia-vida por categoria),
  agregada por **saturação** (`1 − ∏(1 − pontos)`) com **limite de 10 sinais** por empresa:
  - `hermes/agents/buying_signal/buying_signal_score.py` — o componente (fórmula pura + SQL com
    guarda de escrita + CLI `--planejar` / `--desfazer`), com `score_version` obrigatória, `inputs`
    e `explanation` persistidos para reconstruir qualquer número, e `valid_until` de 30 dias;
  - `hermes/agents/buying_signal/agente-buying-signal-v1.json` — contrato legível por máquina
    (pesos por tipo, meia-vidas, limites, garantias, idempotência, lacunas);
  - `docs/architecture/buying-signal-score-v1.md` — contrato com **ACCEPTANCE, TEST, ROLLBACK e RISK**
    (campos exigidos pelo doc 11 §2 e não detalhados lá) e `docs/runbooks/buying-signal-score.md`.
- **Guarda de escrita do score** — o componente escreve **apenas** em `scores`, `agent_runs` e
  `sync_events`: `INSERT`/`UPDATE`/`DELETE` em `signals` ou `organizations` é **recusado**, e
  `UPDATE` em `scores` é recusado **sempre** (score é histórico, não mutável). `DELETE` só no
  `--desfazer` explícito, com `--confirmo`.
- **Idempotência pela ENTRADA** — chave `score:BUYING_SIGNAL:<org>:<entrada_hash>` em
  `sync_events.idempotency_key` (UNIQUE), com claim + INSERT ancorado + fechamento numa única
  transação; a prova da gravação é o `COUNT` do score da rodada lido dentro da própria transação.

### Fixed

- **Dois defeitos medidos no aceite E2E** (não supostos — as duas suítes offline os cobrem agora):
  1. **datas do psql** — o texto vem `2026-09-23 20:04:11+00` (fuso sem minutos) e a validação ISO
     recusava a forma: **todos** os sinais eram descartados com `DATA_AUSENTE` e o score saía `0,00`.
  2. **confiança `NULL` virava `''`** — o JSONB da leitura (`COALESCE(confidence::text,'')`) devolve
     string vazia, e vazio era tratado como valor: o sinal era descartado com
     `CONFIANCA_FORA_DA_FAIXA` em vez de usar a confiança padrão (0,5).
- **Hash de idempotência não pode carregar os pontos** — o `entrada_hash` incluía `pontos`, que
  dependem da idade do sinal e mudam a cada segundo: a chave mudava a cada rodada e **não havia
  idempotência nenhuma** (o replay gravava linha nova). O material passou a usar a **identidade**
  dos sinais (id, tipo) e os parâmetros da fórmula.
- **Porta de banco própria** — a porta importada do detector aplicava a guarda **dele** (recusava
  `scores`, corretamente do ponto de vista daquele agente): o componente passou a ter a sua porta
  com o **mesmo transporte** psql e a guarda **deste** card. Guarda de um agente não vale como
  guarda de outro.
## [W5 — Hermes Sales AI · Automation Fit Score v1] — 02/10/2026

### Added

- **Automation Fit Score v1** (`TRE-W5-E02-T01`) — primeiro componente da onda W5 e primeiro **produtor de
  score** (leitor da onda W4, escritor da tabela `scores`):
  - `hermes/agents/automation_fit/automation_fit.py` — componente `automation_fit/1.0.0`, score
    `AUTOMATION_FIT` / `automation-fit-v1`. **Fórmula declarada** (não vem do baseline — decisão de
    negócio registrada): `100 × Σ(peso × componente) ÷ cobertura`, com `porte` 0,25, `pressao_operacional`
    0,30 (soma dos sinais de pressão, capada em 1,00), `prontidao_tecnologica` 0,20 (sinais de
    tecnologia), `dispersao_de_processos` 0,10 (`unit_count`) e `dor_quantificada` 0,15 (impacto medido
    das hipóteses). **Cobertura mínima 0,40**: abaixo dela `RECUSADA`/`SEM_LASTRO` e nada é escrito;
    **componente ausente não vota** (a normalização é sobre os presentes e a `cobertura` vai explícita na
    explicação). Porte e impacto **nunca vêm da fonte**: são medidos no banco (o pedido só diz QUEM medir);
    campo derivado declarado na fonte é descartado com `CAMPO_NAO_DECLARADO`.
  - `hermes/agents/automation_fit/agente-automation-fit-v1.json` — contrato legível por máquina (pesos,
    vocabulário de sinais, faixas de porte, colunas de escrita, tabelas de leitura/escrita).
  - **Histórico + idempotência**: `input_hash` canônico do que entra na conta (porte/contagem + sinais de
    tipo válido + hipóteses com impacto medido) e `idempotency_key` `score:AUTOMATION_FIT:org:<id>:<hash>`
    com `ON CONFLICT DO NOTHING` + fechamento ancorado em `EXISTS (SELECT 1 FROM scores WHERE id = ...)`:
    **mesmo estado ⇒ `JA_CALCULADO` (zero duplicata); estado novo ⇒ LINHA NOVA** (score é histórico, doc 12
    §8 — nunca `UPDATE`).
  - **Guarda de escrita**: recusa DDL, `UPDATE`/`DELETE` em `scores` fora do `--desfazer` e escrita em
    qualquer tabela que não seja `scores`/`agent_runs`/`sync_events`/`human_approvals` — em especial
    `organizations` (inclusive `data_quality_score`, que é o card W5-E04), `signals`, `pain_hypotheses` e
    `research_runs`, com motivo explícito de "tabela de outro agente".
  - **Fila humana**: identidade ambígua (dois identificadores fortes de empresas diferentes, ou mais de uma
    empresa casada) vai para `human_approvals` (`AUTOMATION_FIT_IDENTITY_REVIEW`) **sem escrever score**.
  - `scripts/agentes/verificar_agente_automation_fit.py` — suíte offline (54 itens, sem banco e sem rede)
    com `--autoteste` por mutação: **22/22 mutações** reprovadas, cada uma pelo item esperado.
  - `scripts/agentes/teste_automation_fit_aceite.sh` — aceite E2E em PostgreSQL descartável próprio
    (`pg-automation-fit-acc`): **76 itens** (4 rodadas: lote completo, replay, estado novo, replay do estado
    novo; guardas de `prod`/`--planejar`; desfazer dry-run/`--confirmo`) + **prova de dente 5/5**.
  - `docs/architecture/agente-automation-fit-v1.md` (contrato, ACCEPTANCE/TEST/ROLLBACK/RISK) e
    `docs/runbooks/agente-automation-fit.md` (operação item a item); bloco do card em
    `scripts/verificar_estrutura.sh`.
  - **Poder de discriminação medido** (não declarado): no estado das quatro empresas do aceite o score
    separa **4 faixas** de 20 pontos (76,00 · 48,50 · 83,00 · 30,00), margem de 53 pontos e desvio médio de
    20 pontos da constante 50 — um estimador que empata com a constante seria ruído calibrado, não score.
## [W5 — Hermes Sales AI · Score Data Quality] — 02/10/2026

**Card:** TRE-W5-E04-T01 (baseline V1.1.0, commit-base `63d711c`) · **Donos do dado:** coluna
`sales_intelligence.organizations.data_quality_score` e a tabela `scores` (`score_type =
'DATA_QUALITY'`, `score_version = 'v1.0'`).

**O que entra**

- `hermes/scores/data_quality/data_quality.py` — o **primeiro score** do TRE: módulo somente-leitura
  contra o banco que mede a qualidade do cadastro de **uma organização por vez** (completude,
  validade, confiabilidade e atualidade) e grava a medição datada em `scores` + o espelho em
  `organizations.data_quality_score`. Identidade só por **identificador forte** do Contrato de Dados
  V1.0 (`cnpj` → `domain` → `linkedin_url`); qualquer ambiguidade é **fila humana**, não palpite.
- `hermes/scores/data_quality/score-data-quality-v1.json` — o contrato do score (pesos, cortes de
  atualidade, vocabulário de fontes, colunas escritas). **É a fonte da verdade**: o módulo confere
  contrato × código no `__init__` e **recusa carregar** se divergirem (provado por dente de carga).
- `scripts/scores/verificar_score_data_quality.py` — suite de bancada: **25 itens** e **15 dentes**
  (mutação em cópia do código, cada uma exigindo o item que ela tem de reprovar).
- `scripts/scores/teste_data_quality_aceite.sh` — aceite E2E em PostgreSQL descartável
  (`pg-dq-acc`), com prova de dente própria.
- `docs/architecture/score-data-quality-v1.md` + `docs/runbooks/score-data-quality.md` — o par
  arquitetura/runbook do score; o gate de estrutura passa a exigir os sete artefatos versionados.

**Decisões que o card fixa**

- **escrever é exceção, e é declarada**: a `GuardaDeEscrita` recusa qualquer SQL que não caia numa
  lista de tabelas permitidas e **recusa `UPDATE` sem `WHERE` por projeto**; a única coluna de
  `organizations` que o score toca é o próprio espelho — `updated_at` **não** é tocado (medido por
  foto antes/depois, não por promessa);
- **idempotência mora no SQL**, não na prosa: o `INSERT` só grava `WHERE NOT EXISTS` da mesma
  assinatura (`inputs_sha256`), então replay é `JA_EXISTE` e **zero duplicata**;
- **a medição é datada e reproduzível**: o mesmo estado no mesmo `--referencia` dá o mesmo valor;
  estado novo (campo que entra na conta) é **medição nova**, mesmo com valor igual;
- **prod é recusado em duas camadas** (`AMBIENTES_PERMITIDOS` + recusa explícita de `prod`), com
  exit 4 e **sem escrever nada** — `--planejar` mede e não escreve.

**Medição**

- bancada: `python3 scripts/scores/verificar_score_data_quality.py --autoteste` → `DQ_SUITE_OK`
  (25 itens, 0 falhas; 15/15 dentes reprovando o item esperado);
- aceite E2E (`--prova-de-dente`) no clone `/opt/tre/w5e04t01-si-r4`, container descartável
  `pg-dq-acc` → `ACEITE_DATA_QUALITY_001_OK (35 itens, 0 falhas, 0 dentes reprovados)`, exit 0;
  evidência `/opt/tre/evid-w5e04t01-dq.out` (sha256 `aa7f2c0c…`) e o detalhe em
  `docs/operations/registro-de-execucoes.md`.

**Defeitos que a própria execução pegou (consertados na raiz, não remendados)**

- **identidade:** o SQL comparava a forma **bruta** da coluna com o valor **normalizado** — CNPJ gravado
  com pontuação nunca casava e a empresa ficava sem medição. Agora o SQL **pré-filtra** (superset) e o
  **módulo de identidade decide** normalizando a forma guardada, como o Scout faz: score e produtor não
  podem discordar sobre quem é quem;
- **o aceite era um falso verde:** a rodada roda dentro de substituição de comando, os contadores de shell
  morriam no subshell e o veredito imprimia `0 itens, 0 falhas` com **exit 0** mesmo com item reprovado —
  contagem por **arquivo**, veredito vazio **reprova** e o que caiu é **nomeado**.

**Dentes que ficaram inertes (medido, não suposto)**

- mutar **uma** das duas camadas da recusa de `prod` não muda nada (a outra camada segura) — o dente
  passou a mirar a função inteira devolvendo o ambiente sem conferir;
- tirar o `--referencia` do JSON canônico não muda o hash, porque a referência **também** entra em
  `inputs` (defesa em profundidade) — o dente passou a fixar o canônico por inteiro;
- as mutações que quebram o contrato de pesos/colunas **nem carregam**: o `__init__` recusa antes de
  qualquer medição, e isso virou dente de **carga** (recusar é o comportamento esperado).

## [W5 — Scoring + NBA · Next Best Action v1] — 02/10/2026

**Card:** TRE-W5-E07-T01 (baseline V1.1.0, base `feature/TRE-W5-E06-T01`) ·
**O que é:** lê a **evidência já existente** (o registro `TIER` do card irmão, pesquisa, hipóteses de
dor, sinais, contatos, interações) e decide o **próximo passo** da empresa por uma **tabela de decisão
declarada**, gravando a recomendação em `sales_intelligence.recommendations`.

### Added

- **Next Best Action v1** (`hermes/scores/nba/nba.py`) — o **vocabulário das ações** é **lido do Data
  Contract** (`vocabularies.next_best_action`: RESEARCH_MORE, FIND_DECISION_MAKER, SEND_EMAIL,
  PREPARE_LINKEDIN, WAIT, FOLLOW_UP, CREATE_MEETING, NURTURE, DISQUALIFY) e **nenhuma ação, papel de
  decisão ou id de regra existe em forma executável no código** (itens `C1`..`C3` por AST); a
  **tabela de decisão** vive na política do card (`politica-nba-v1.json`, 12 regras, ordem = decisão):
  compliance → qualificação → tier → estado do funil (pesquisa, resposta, abordagem, decisor).
- **Persistência na casa que o contrato já governa:** `recommendations`
  (`recommendation_type='NEXT_BEST_ACTION'`, `action` do vocabulário, `status='OPEN'`, `priority`/
  `due_at`/`expires_at` da regra, `contact_id` do melhor decisor alcançável) + `agent_runs`
  (auditoria, **inclusive da recusa e da abstenção**). **Nenhuma coluna nova**, nenhum DDL, nenhum
  evento de outbox (o `NEXT_BEST_ACTION_CHANGED` é do caminho de integração, W3/W6) e **nada
  executado** — executar a ação exige o aval humano (doc 12 §4).
- **Idempotência por conteúdo:** o `id` da recomendação é `uuid5(nba-v1, org:entrada_hash)` — mesma
  entrada, mesmo id (replay `JA_RECOMENDADA`, nada duplicado); evidência nova gera recomendação nova e
  a anterior volta para `SUPERSEDED` (doc 12 §5), com o histórico preservado. Relógio e
  `correlation_id` **não** entram no hash; contadores entram como o resultado da comparação.
- **Fail-closed:** empresa **sem registro `TIER` RECUSA** (`SEM_TIER`) sem gravar — o próximo passo
  depende da prioridade já decidida e o componente não adivinha tier; **nenhuma regra casando ABSTEM**
  (`SEM_REGRA`) e nada é gravado; política/contrato incoerente **RECUSA com exit 3**; `prod` recusado
  com exit 4 (ADR-005); `--planejar`/`--regras` não abrem conexão.
- **Suíte offline** (`scripts/scores/verificar_nba.py`) — **69 itens / 0 falhas** e autoteste com
  **7/7 mutações** detectadas, cada uma pelo item esperado.
- **Aceite E2E** (`scripts/scores/teste_nba_aceite.sh`) — PostgreSQL descartável `pg-nba-acc`
  (removido pelo próprio aceite): `ACEITE_NBA_001_OK (79 OK / 0 FALHOU)` com **6 dentes**, cada um
  reprovando o item esperado (supersessão desligada, id não-determinístico, checagem de tier
  desligada, primeira regra vencendo sempre, ordem invertida, contato bloqueado ignorado).
- **Docs do card:** `docs/architecture/next-best-action-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK) e
  `docs/runbooks/next-best-action.md`; portão da W5 (`scripts/verificar_estrutura.sh`) cobre os
  artefatos.

### Changed

- **Portão de estrutura da W5** (`scripts/verificar_estrutura.sh`) passa a exigir os artefatos do NBA
  (código, contrato do card, política, exemplos, suíte, aceite executável, arquitetura e runbook).

### Lacunas declaradas (proposta a homologar — decisão do dono)

- A **tabela de decisão** (regras, ordem, prazos) é proposta deste card: o contrato define o
  **vocabulário** das ações, não a política de escolha.
- `confidence` fica **NULL** declarado: a regra é determinística e um número ali fingiria calibração
  (a calibração depende do feedback de resposta, W6+).
- O vínculo resposta↔contato que respondeu depende da classificação de resposta do W6; aqui o
  sentimento vem de `interactions.sentiment`.
- `EXPIRED`/`APPROVED`/`EXECUTED` são do workflow humano (doc 12 §4/§5): aqui gravam-se `due_at` e
  `expires_at`.
