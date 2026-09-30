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
  por construção não vale (D04 do TRE-W0-E04-T01).
  Runbook `docs/runbooks/publicacao-da-copia-operacional.md` revisão 1.1 (§5 enforcement, §9 destino
  isolado).

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
- **A publicação versionada falhava de forma intermitente no manifesto do staging e culpava o lado errado**
  (`t_0f74266d`, defeito registrado pelo card `t_1b2ab418` e reproduzido pelo `tester` no `t_c9a44f85`): o
  staging era o **caminho fixo** `/opt/tre/.publicacao-staging`, compartilhado por toda publicação de todo
  card — duas publicações simultâneas se misturavam (o `find` de uma listava o que o `rm -rf`/`tar -x` da
  outra apagava, ~280 linhas de `sha256sum: … No such file or directory`), a falha era intermitente e a
  mensagem culpava "a cópia transferida" quando o manifesto incompleto era o do **staging** (o diff ainda
  saía truncado em `head -30` e a falha não deixava linha no log). Corrigido em `deploy/publicar.sh`
  (`44e0d13`): staging **único por publicação** (`mktemp -d` no diretório pai do destino, removido no fim e
  no trap), mapa de modos irmão do staging (era o fixo `/opt/tre/.publicacao-modos`, que ficava para trás no
  `exit 6`), manifesto **reprovado como INCOMPLETO antes de comparar** (exit 7 — contar linhas não bastava:
  o defeito real mantinha a contagem e zerava o campo do hash), cada falha nomeando a **fase** e o
  **arquivo** (staging/local/antes/depois/conferência) com `PUBLICACAO_INDETERMINADA` para o caso em que não
  dá para afirmar divergência, diff **sem truncar** (arquivo completo em `TRE_PUBLICAR_DIFF_DIR` + `head
  -200`), `PUBLICACAO_ABORTADA` no log append-only e duas guardas fail-closed da mesma família ("artefato
  compartilhado em caminho fixo"): lock isolado com destino compartilhado é **recusado** (exit 2) e destino
  isolado com o artefato padrão do watchdog é **recusado** (o watchdog repararia a produção para o commit do
  ensaio). `PRODUCAO` passou a ser recalculado **depois** do parse dos argumentos (com `--destino` para um
  ensaio, o cálculo antigo fazia o destino isolado passar por produção e pedia `--producao`). Teste local sem
  VPS: `deploy/teste-staging-unico.sh` (39 verificações, 0 falhas em duas execuções — roda o `publicar.sh`
  real contra um `ssh` de mentira e reproduz o defeito na versão de `3bf5e07` antes de provar o conserto).
