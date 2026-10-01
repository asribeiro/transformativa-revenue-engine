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
- **CRM básico configurado no dev (`TRE-W2-E02-T01`)** — funil comercial do módulo `crm` derivado
  **literalmente** da §7.1 do Data Contract V1.0 (congelado) e declarado de forma versionada em
  `odoo/crm/funil-transformativa.yaml` (11 etapas, ordem 10..110, `Won` com `is_won`, ramo lateral
  `Nurture` em pipeline próprio, `Lost` deixado no nativo do Odoo — **nenhuma etapa inventada**) +
  `scripts/provision/{configurar-crm-dev.sh,verificar-crm-dev.sh,reverter-crm-dev.sh,aplicar_funil_crm.py,desfazer_funil_crm.py,repor_etapas_padrao_crm.py}`
  + runbook `docs/runbooks/odoo-crm-dev.md`. Medido na VPS Contabo `vmi3619453`, 01/10/2026: módulo `crm`
  instalado em `odoo_dev`; pipeline `Sales` com `Descoberto 10 … Negociação 100` + `Won 110`; time/etapa
  `Nurture` fora do funil; etapas do módulo `New`/`Qualified`/`Proposition` reconciliadas e `Won` **adotada**;
  `crm.lead` com 0 oportunidades antes e depois (nenhum dado de negócio tocado).
- **Aceite do CRM item a item**: `bash verificar-crm-dev.sh` → `RESULTADO: CRM_DEV_OK (29 itens, 0 falhas)`,
  exit 0 — confronto **banco × declaração** etapa por etapa, pipeline único, etapa de ganho única e última,
  ramo lateral separado, ausência de etapa órfã, campos mínimos 10/10 em `crm.lead`, banco do Odoo ainda
  separado do `sales_intelligence`, `pg-sales-dev` de pé e Odoo **só em loopback**.
- **Dentes do aceite do CRM** (provas negativas medidas, com exit code): declaração mutada → `CRM_DEV_FALHOU
  (30 itens, 3 falhas)`, exit 1; **etapa intrusa plantada no banco vivo** → `CRM_DEV_FALHOU (29 itens, 1 falha)`,
  exit 1, e o configurador a removeu; rollback **padrão** → `CRM_DEV_FALHOU (29 itens, 15 falhas)`, exit 1;
  rollback **total** (módulo desinstalado) → `CRM_DEV_FALHOU (13 itens, 3 falhas)`, exit 1 — com o aceite
  voltando a 29/29 em cada reconfiguração.

### Security

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
- **Oito defeitos encontrados executando o CRM (`TRE-W2-E02-T01`)**, todos consertados nesta execução:
  (1) `docker exec` em container parado (o configurador parava o serviço antes do `odoo shell`);
  (2) o desenho previa **arquivar** etapa extra, mas no Odoo 19 `crm.stage` **não tem** `active` — a
  reconciliação passou a **remover**, com guarda fail-closed para etapa com oportunidade;
  (3) ler `etapa.name` depois do `unlink()` abortava a transação — o nome é guardado antes;
  (4) **`NULL || '…'` colapsa a linha do `psql`**: etapa criada fora do módulo fica com `is_won` **NULL**
  (não `false`), a linha saía **vazia** e o comparador **descartava a etapa em silêncio** — uma etapa
  intrusa no pipeline passava como aceite (achado pela prova negativa); todo campo entrou em `coalesce` e
  linha vazia/malformada agora **reprova**;
  (5) `rpad(jsonb, integer) does not exist` — `crm_stage.name`/`crm_team.name` são **JSONB** (traduzíveis);
  (6) item de exposição olhava a linha inteira do `ss` (a 5ª coluna é o *peer*, sempre `0.0.0.0:*`) → falso
  "porta pública", e exigia "UFW só com 22/tcp", fato que o card de TLS muda legitimamente — passou a medir
  o **bind** da porta do Odoo e a **inexistência de regra** de UFW para ela;
  (7) o rollback repunha o padrão com `-u crm`, que **não repõe** dado `noupdate` apagado — o pipeline
  ficava **vazio** depois do rollback; a reposição é explícita e conferida (`repor_etapas_padrao_crm.py`);
  (8) `odoo-dev` é compartilhado e um reinício de container por outro card mata o `docker exec` no meio
  (exit **137**) — cada passo de ORM sobe o serviço, confere `HTTP 200` e tem até 3 tentativas.

### Notas de estado

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
