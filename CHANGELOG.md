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

- **Publicação versionada da cópia operacional** (`t_091cfea9`, DEFEITO F3 do `TRE-W1-E06-T01`) —
  `deploy/publicar.sh` passa a ser o **único** caminho de escrita em `/opt/tre/repo`: publica um
  **commit** (nunca a árvore de trabalho) por `git archive` → staging → `rsync -a --delete`, com o modo
  vindo do índice do git; grava o commit em uso em `/opt/tre/repo/.publicado` (+ `.publicado.manifest`
  com modo/sha256 de cada arquivo), recusa árvore suja (só passa com `--permitir-arvore-suja`, que
  registra o desvio), aceita **uma publicação por vez** (`/opt/tre/.publicacao.lock`), avisa quando outro
  card publicou antes, mantém o histórico em `/opt/tre/.publicacoes.log` e confere depois do `rsync` que
  o `digest` da cópia é o do commit (senão falha com exit 6). Antes de publicar, confere os `ExecStart=`
  dos units contra o modo do commit e **conta/registra** os alvos sem bit (`AVISO modo`; `--exigir-modos`
  vira exit 4) — a publicação deixa de recriar o `203/EXEC` por acidente. `deploy/publicar.sh
  --conferir` compara a cópia com o commit registrado arquivo a arquivo **e modo a modo**, devolvendo
  `PUBLICACAO_DIVERGENTE` (exit 5) com o diff quando alguém reescreveu a cópia por fora do caminho único;
  `docs/runbooks/publicacao-da-copia-operacional.md` é o runbook (comando, guardas, rollback do código
  publicado e o que o caminho não faz).

- **Enforcement do caminho único da cópia operacional — trava de imutabilidade, artefato do commit e
  watchdog de 2 min** (`t_daca4bda`, recorrência do defeito do `t_091cfea9`) — o `deploy/publicar.sh`
  **detectava** a escrita ad-hoc (`--conferir`, exit 5), mas ninguém o rodava: bastou um card em execução
  ressincronizar a cópia por `tar` ad-hoc para o `.publicado` continuar dizendo o commit consertado
  enquanto a árvore em disco voltava ao estado **pré-correção**. Agora:
  (i) **`chattr +i`** na cópia publicada — escrita ad-hoc falha com `Operation not permitted` em vez de
  sobrescrever em silêncio (`--travar`/`--destravar`; `--sem-trava` só para ensaio; `deploy/publicar.sh` é
  o único que desarma, e só durante a troca);
  (ii) **artefato do commit** em `/opt/tre/.publicacao-artefato` (`commit.tar` com o modo do git +
  `modos.txt` + `manifesto` + `commit`/`digest`, `root:root` 700 **fora** da cópia) — fail-closed: se o
  artefato gravado não conferir com o commit, a publicação para antes de trocar qualquer coisa;
  (iii) **`deploy/watchdog-publicacao.sh`** (novo; roda na VPS, sem git e sem o checkout) confere a cópia
  contra o manifesto do commit registrado a cada 2 min
  (`deploy/systemd/tre-publicacao-watchdog.{service,timer}`), **atribui** a divergência (alterado /
  plantado / removido, com mtime e se é posterior à publicação), grava `/opt/tre/.publicacao-ALERTA` e
  `/opt/tre/.publicacao-divergencias.log` e, com `--reparar`, **restaura a cópia a partir do artefato** e
  rearma a trava (registrado em `.publicacoes.log` como `card=watchdog-reparo`);
  (iv) `deploy/instalar-watchdog-publicacao.sh` instala os **bytes da cópia publicada** em
  `/usr/local/lib/tre` (sha256 conferido dos dois lados) — o watchdog sobrevive à cópia quebrada;
  (v) **guarda de produção:** o destino compartilhado é produção (alvo do `ExecStart=` dos timers), então
  **substituir** o commit que está no ar exige `--producao` declarado (`TRE_PUBLICAR_PRODUCAO=1`) — sem
  isso a publicação para com `PUBLICACAO_FALHOU` (exit 2) **antes de escrever qualquer coisa**; publicar o
  mesmo commit (reparo) ou em destino de ensaio passa direto, e a declaração fica em `.publicado`
  (`producao_declarado`);
  (vi) **publicação com UMA conexão SSH** (`ControlMaster`, `ControlPersist=30` em `R()`): a publicação
  faz ~25 chamadas remotas e, com uma conexão TCP por chamada, a rodada de publicações de 22:2x–22:4xZ
  fez a VPS responder `Connection refused` na porta 22 **para o IP de origem inteiro** (todos os cards)
  por ~12 min, com o host de pé e **sem reboot** — assinatura de penalidade por fonte
  (`PerSourcePenalties`)/`fail2ban`, agravada pelas retentativas;
  (vii) **idade do lock deixou de ser inventada:** com o `stat -c %Y` ilegível, `AGORA - 0` virava
  "~56 anos" (`idade 1790808317s`, medido pelo card `t_c7281fce`) e a publicação **derrubava o lock vivo**
  de outra (fail-open). Agora a idade sai do mtime e, se não for medível, do `inicio` que o próprio lock
  grava; **sem idade confiável não derruba o lock** (`PUBLICACAO_FALHOU`, exit 3, nada escrito);
  (viii) **`deploy/verificar-enforcement.sh`** — verificador com dente: tenta o caminho ad-hoc em
  destino isolado e **exige que falhe** (4 caminhos recusados, conteúdo intacto, sabotagem reprovada pelo
  detector com exit 5, reparo restaurando e rearmando) e **reprova quando o guard está desligado**
  (`TRE_ENF_SEM_TRAVA=1` -> `VERIFICADOR_ENFORCEMENT_FALHOU … falhas=7`, exit 1) — verificador que passa
  por construção não vale (D04 do TRE-W0-E04-T01);
  (ix) **lock registra `destino=`** e o watchdog só se cala para lock de publicação **para o destino que
  ele vigia** — antes, um card publicando em destino isolado com o lock padrão cegava a conferência da
  produção (medido: 2 ciclos com a cópia real divergente);
  (x) **cópia correta e destravada é rearmada no ciclo** (`trava=rearmada`) — publicação por versão antiga
  do `publicar.sh` deixava a janela aberta para o ad-hoc.
  Runbook `docs/runbooks/publicacao-da-copia-operacional.md` revisão 1.1 (§5 enforcement, §9 destino
  isolado).

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

- **A rotina de backup cobria zero ambientes e saía `BACKUP_OK`; o verificador aprovava sem backup nenhum**
  (`TRE-W1-E06-T01-F2`, card `t_1b2ab418`; era o achado "trio `TRE_PG_*`") — `backup-tre.sh` procurava
  `pg-dev`/`pg-homolog`/`pg-prod` e lia o trio de variáveis globais que o `EnvironmentFile` do unit não
  declara, embora o dev real seja `pg-sales-dev` (`sales_ai`), declarado em `deploy/environments/dev.env`
  — arquivo que nenhum timer lia. Medido no defeito: `PULADO` nos três ambientes, exit 0, **nenhum artefato**;
  o `tre-backup-verify.service` também aprovava (`VERIFICACAO_OK`) porque procurava o mesmo prefixo
  `tre_dev_*` que a rotina nunca produzia. Corrigido com a resolução do trio **por ambiente**
  (`scripts/backup/lib-ambiente.sh`, novo, usado pela rotina e pela verificação): variável por ambiente
  (`TRE_PG_SERVICO_<AMBIENTE>`) → `$TRE_ENV_DIR/<ambiente>.env` → variável global **só** em chamada de um
  ambiente → convenção `pg-<ambiente>`; `TRE_ENV_DIR=/opt/tre/repo/deploy/environments` no `backup.env`.
  Ambiente **declarado** cujo container não existe agora **falha** (exit 1, `BACKUP_FALHOU`), `todos` sem
  nenhum ambiente coberto devolve `BACKUP_SEM_AMBIENTE` (nunca `BACKUP_OK`), e a verificação reprova
  ambiente provisionado sem backup. Prova medida **sob o usuário do timer**:
  `systemctl start tre-backup.service` → `Result=success`, `ExecMainStatus=0`, `RESULTADO: BACKUP_OK
  (todos; 1 coberto, 2 pulados)` e artefato `tre_dev_*` com `servico: pg-sales-dev`/`externo: enviado`;
  `tre-backup-verify.service` → `RESTORE_OK (11 itens)` restaurado do artefato que a rotina acabou de
  produzir + `VERIFICACAO_OK`. Teste hermético versionado `scripts/backup/teste-rotina-ambiente.sh`
  (`TESTE_OK`, 55 itens, 0 falhas) com regressão contra os scripts anteriores (antes: `BACKUP_OK` com
  **0 artefatos**; depois: artefato criado). Detalhes e evidência em
  `docs/runbooks/backup-restore-rollback.md` §7f.
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
  do `EnvironmentFile`), não o bit. **Resolvido em 30/09/2026 pelo próprio `t_1b2ab418`** (resolução do trio
  por ambiente, `scripts/backup/lib-ambiente.sh`; evidência em `docs/runbooks/backup-restore-rollback.md`
  §7f): sob `tre-deploy`, `tre-backup.service` → `Result=success` + `RESULTADO: BACKUP_OK` + artefato
  `tre_dev_*`, e `tre-backup-verify.service` → `RESTORE_OK`/`VERIFICACAO_OK`. **Qualificação medida (30/09 20:02–20:13 UTC):** a cópia operacional foi
  revertida para `644` duas vezes por publicação de árvore **anterior** à correção (`/opt/tre/.publicacoes.log`)
  — o bit no git e a guarda são duráveis, a cópia operacional
  depende do caminho versionado de publicação (ACHADO ABERTO 3). Depois da publicação versionada de
  20:12:35Z os dois critérios da cópia foram remedidos com horário (`test -x` exit 0; `systemctl start` →
  `Result=success`, `ExecMainStatus=0`) — runbook §7d/§8. **Correção de atribuição (medida pelo card
  `t_091cfea9`):** as publicações de ensaio de 20:03:59Z/20:06:19Z foram para o destino **isolado**
  `/opt/tre/.teste-publicacao`, não para `/opt/tre/repo` (o campo `destino=` só passou a ser gravado no log
  depois delas) — o revert da cópia operacional medido ali é de sincronização por `tar` ad-hoc, não delas.
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
- **Cópia operacional `/opt/tre/repo` reescrita por qualquer card — sem dono, sem modo e sem registro**
  (`t_091cfea9`, o achado F3 do card `TRE-W1-E06-T01`) — cada card publicava o seu pedaço com
  `tar -cz … | ssh … 'tar -xz -C /opt/tre/repo'`: quem sincronizava por último mandava (o driver recém
  instalado voltou de `sha256 d29c9c97…` para `9f24572a…` no meio de uma rodada), o modo vinha do
  *checkout* e não do git (foi o que devolveu `644` para `scripts/backup/*.sh` e produziu o `203/EXEC`) e
  a cópia não tinha `.git` nem registro — na medição de 30/09 ela tinha **122 arquivos** de **300**
  versionados, sem ninguém saber qual commit estava no ar. Corrigido com **um caminho único de
  publicação** (`deploy/publicar.sh`, ver o `Added` acima): commit explícito, modo do índice do git,
  `.publicado` com o commit em uso, `--conferir` que reprova a cópia divergente e histórico em
  `/opt/tre/.publicacoes.log`. Evidência medida em `docs/runbooks/backup-restore-rollback.md` §7e.
- **Recorrência: a cópia operacional foi reescrita por fora do caminho único e reverteu o commit
  publicado** (`t_daca4bda`; mesmo defeito do `t_091cfea9`, um dia depois) — o card `t_c7281fce`, **em
  execução**, ressincronizou `/opt/tre/repo` da própria árvore de trabalho por `tar` ad-hoc (os `mtime`
  gravados na cópia batem ao segundo com os arquivos do worktree dele, com `mtime` preservado; o
  `.publicacoes.log` não tem entrada dele e o `.publicado` continuou apontando para `c7972ca`, o commit
  consertado) e o `tre-backup.service` voltou a executar a rotina **pré-correção**, imprimindo
  `BACKUP_OK` cobrindo **zero** ambientes. Causa raiz medida, não inferida: o caminho único existia mas
  **não tinha enforcement** — o `--conferir` só reprovava quando alguém lembrava de rodar, e nada impedia
  a escrita. Corrigido com trava de imutabilidade, artefato do commit e watchdog (detalhes no `Added`
  acima), tudo medido no destino real: com a trava armada, os quatro caminhos ad-hoc do defeito (append,
  `sed -i`, `tar -xz` de outra árvore, arquivo novo plantado) são **bloqueados** com `Operation not
  permitted`; removida a trava na mão, a divergência injetada (conteúdo pré-correção com `mtime`
  preservado + `scripts/db/teste_isolamento_clientes.sh` plantado) foi **detectada, atribuída pelos
  `mtime` e restaurada** pelo watchdog, e a cópia voltou a bater com o commit registrado. Decisão de
  processo registrada no runbook: `/opt/tre/repo` é **produção**, bancada de teste é destino isolado
  (`TRE_PUBLICAR_DESTINO`).

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
- **A rotina automática de backup NÃO estava funcionando** — dois achados abertos medidos no mesmo card:
  o unit `tre-backup.service` falhava com `203/EXEC` (scripts de `scripts/backup/` estavam `100644` no git)
  e, mesmo executando, `backup-tre.sh todos` **pulava os três ambientes** (procurava `pg-dev`, o dev real é
  `pg-sales-dev`) e saía `BACKUP_OK` sem gerar artefato. **Os dois foram resolvidos em 30/09/2026**
  (`6a580ee`/`fix/TRE-W1-E06-T01-D01` e `9b464ed`/`fix/TRE-W1-E06-T01-F2`): sob o usuário do timer,
  `tre-backup.service` sai `BACKUP_OK` com artefato `tre_dev_*` e `tre-backup-verify.service` restaura de
  verdade (`RESTORE_OK`, 11 itens). Detalhes em `docs/runbooks/backup-restore-rollback.md` §7c/§7f/§8.
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
- **Backup do Odoo no MESMO artefato do ambiente (`TRE-W2-E01-T01-F01`, card `t_a5afde31`)** — o
  artefato diário deixa de ser "só o trio": passa a levar o Odoo do ambiente junto
  (`odoo_dev.dump` + `.sha256`, `odoo-contagens.txt` por tabela, `odoo-filestore.tar.gz` do volume
  `odoo-data-dev` e `odoo-manifest.txt` com o **digest da imagem** do Odoo), tudo em
  `scripts/backup/{lib-ambiente.sh,backup-tre.sh}` e declarado no par não-secreto
  `deploy/environments/dev.env` (`TRE_ODOO_PG_SERVICO`/`_USER`/`_DB`/`TRE_ODOO_FILESTORE`/
  `TRE_ODOO_IMAGEM`). O Odoo é resolvido **por ambiente**, na mesma precedência do trio (variável por
  ambiente → arquivo do ambiente → nada): ambiente que não declara Odoo é **pulado com a ausência
  declarada no manifesto**, ambiente que declara e não tem container (ou cujo dump/filestore não sai)
  é **falha** — nunca `BACKUP_OK`.
- **`scripts/backup/verificar-odoo.sh` — a prova de restore do Odoo** (alvo descartável): confere
  `sha256`/nº de arquivos/**digest da imagem** contra o manifesto, restaura o dump num PostgreSQL
  descartável **sem porta publicada**, compara **tabela por tabela linha a linha**, exige o módulo
  `base` instalado, desempacota o filestore (exige `filestore/odoo_dev` e a mesma contagem de
  arquivos), sobe um **Odoo descartável** contra o banco restaurado e só aceita com `/web/login` em
  **HTTP 200** + JSON-RPC respondendo — e confere ao fim que `odoo-dev`/`pg-odoo-dev`/`pg-sales-dev`
  continuam `running`. `verificar-ultimo-backup.sh` encadeia esse verificador quando o artefato mais
  recente do ambiente traz o Odoo; artefato **pela metade** (trio sem Odoo, com o ambiente declarando
  Odoo) é **falha** na verificação.

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
- **Dois defeitos do verificador do Odoo, achados rodando (01/10/2026):**
  (6) o `/entrypoint.sh` da imagem faz `exec odoo "$@" "${DB_ARGS[@]}"` e os argumentos que ele monta
  (com `HOST` default **`db`**) entram **depois** dos informados — o Odoo descartável subia procurando
  um host `db` (`Database connection failure: could not translate host name "db"`) e o verificador
  reprovava um backup **bom**; conserto: `--entrypoint /usr/bin/odoo`;
  (7) o teste de identidade do Odoo pedia `GET` em `/web/webclient/version_info` e o endpoint é
  JSON-RPC (**415 Unsupported Media Type**) — mesmo efeito de reprovar quem responde; conserto: `POST`
  `Content-Type: application/json`, mantendo o `/web/login` **HTTP 200** como critério principal.
- **`ls *.dump | head -1` deixou de ser o seletor de dump** (`verificar-backup.sh`, `restore-tre.sh`,
  `teste-backup-restore.sh`): com o Odoo no mesmo artefato há **dois** `*.dump` e `odoo_dev.dump` vem
  primeiro em ordem alfabética — o verificador do trio compararia o banco errado. Quem escolhe agora é
  o **manifesto** (`banco:`). Medido: `verificar-backup.sh` num artefato com dois dumps →
  `RESTORE_OK (11 itens, 0 falhas)`.
- **Rodada 2 do `TRE-W2-E01-T01-F01` (card `t_a5afde31`, 01/10/2026 — o que a revisão independente
  reprovou):** o artefato de backup passou a nascer com o dono do **usuário de serviço**
  (`TRE_BACKUP_DONO`, padrão `tre-deploy` quando existe na máquina; rodando como `root` a rotina
  aplica o `chown` antes da retenção; declarar um usuário inexistente é **falha**), porque a execução
  manual do operador como `root` gerava `root:root 700` e o verificador do timer (`tre-deploy`) não
  conseguia ler o artefato — acusava "backup pela metade"/"sem Odoo" (defeito de **conteúdo**, falso)
  para um artefato íntegro. Junto: a retenção passou a **conferir o exit do `rm`** e a reportar
  `NAO consegui remover …` (`BACKUP_FALHOU`) em vez de contar como removido o que continua no disco; os
  três verificadores passaram a distinguir **ilegível por permissão** de **ausente/pela metade**; o
  `pg_restore.err` deixou de ser gravado **dentro** do artefato verificado (arquivo temporário); e
  destino sem escrita falha com o diagnóstico certo (antes: `No such file or directory` no meio do
  dump). Medido no mesmo estado entregue: rotina a mão por `root` → artefato `tre-deploy:tre-deploy`
  (`executado_por: root`) verificado por `tre-deploy` → `VERIFICACAO_OK (3 itens)`; units →
  `Result=success`; negativos de conteúdo continuam reprovando (dump truncado 10 falhas, filestore
  ausente 4); hermético `TESTE_OK (84 itens, 0 falhas)`. Runbook §7h.

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
  **Atualização 01/10/2026 (`t_a5afde31`):** a branch `feature/TRE-W2-E01-T01-F01` foi criada **da**
  `fix/t_daca4bda-enforcement` e trouxe o `develop` para dentro (`git merge origin/develop`), então a
  publicação dela **não apaga o enforcement** — é o caminho para o Odoo do dev voltar a rodar do par
  versionado. Publicação por `deploy/publicar.sh` e evidência em
  `docs/runbooks/backup-restore-rollback.md` §7g e `docs/operations/registro-de-execucoes.md`.
- **Rotina de backup cobre o Odoo do dev desde 01/10/2026** (`t_a5afde31`): `backup-tre.sh dev` grava
  num **único** artefato o dump do trio **e** o par do Odoo (banco + filestore), e o timer de
  domingo (`tre-backup-verify.timer`) verifica os dois. Ambiente que declare Odoo e cujo artefato não
  o traga é **falha** de verificação, não "meio backup". O artefato nasce com dono do **usuário de
  serviço** (`TRE_BACKUP_DONO`, padrão `tre-deploy`) mesmo quando a rotina é executada a mão por
  `root`, e a retenção **confere o `rm`** antes de dizer que removeu (rodada 2, runbook §7h).
