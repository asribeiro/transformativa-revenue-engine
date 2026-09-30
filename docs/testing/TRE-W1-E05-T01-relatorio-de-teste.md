# Relatorio de teste — TRE-W1-E05-T01 (suite de teste do banco)

> **RODADA 3 (30/09/2026) — leia o §10: é o estado vigente.** Ele responde item a item à revisão
> independente (que reprovou 3 itens em rodada própria, com a bateria AC1/AC2/AC3 já PASS e dente
> reproduzido por ela) e traz a medição nova com a prova de que o verde falso medido por ela foi fechado.
> **RODADA 2 (30/09/2026) — leia o §9 antes do §1.** O §1 mede o AC2 na **forma antiga** do critério
> ("consulta sem filtro de tenant"), que o dono **reformulou** em 30/09/2026 (opção A: isolamento físico, um
> banco por cliente — card `t_e340c29b`) e o §1 fechou em `NAO_TESTAVEL`. A **forma vigente** do critério é
> "não existem dois clientes no mesmo banco", medida no §9, com a suíte em **verde**. O §1–§8 continuam
> válidos como registro da rodada 1 (AC1, AC3 e proveniência do dente).

Card: `t_c7281fce` · W1 · E05 · dono: `analista-teste` · commit entregue: `d2a2640` (origin/develop, head de develop na medicao)
Ambiente medido: VPS do TRE `vmi3619453` (169.58.24.102), container `pg-sales-dev`, base `sales_intelligence`,
usuario `sales_ai` — o ambiente de **dev**, conforme ADR-0008 (acesso por SSH). Nada foi medido em stub:
as saidas abaixo sao de execucao real contra o Postgres do dev e contra containers descartaveis criados
pelos proprios testes.

## 1. O que o card pediu e o que foi medido

| Criterio de aceitacao (homologado por Anderson em 29/09/2026) | Teste que prova | Teste negativo (o caminho proibido) | Fronteira | Veredito |
|---|---|---|---|---|
| **AC1** — o schema do alvo tem de bater com o Data Contract V1.0 (tabelas, colunas, tipos, constraints, indices) | `suite_banco.sh dev` etapa `contrato` (37 itens) + `constraints` (16 itens) | `--prova-de-dente` muta alvo descartavel: `DROP COLUMN organizations.cnpj` -> exit 1 (`contrato`); `CREATE INDEX idx_intruso_suite` -> exit 1 (`constraints`) | desfazer cada mutation volta ao veredito sem reprovacao (exit 3) | **PASS** no alvo integro; a suite reprova quando deve |
| **AC2** — duas organizacoes/clientes nao se enxergam (isolamento por cliente) | `teste_tenant_rls.sh dev` — le a dimensao de cliente do propio contrato | fixture descartavel multi-cliente (A/B) + RLS: policy permissiva `USING (true)` -> exit 1 (vazamento); `ALTER ROLE app_cliente BYPASSRLS` -> exit 1; `DISABLE ROW LEVEL SECURITY` -> exit 1 | sessao sem cliente -> **vazio** (`current_setting`, `true`); na variante estrita -> **erro** (`unrecognized configuration parameter`) | **NAO TESTAVEL (exit 3)** contra o contrato V1.0: nao ha coluna de cliente. O teste tem dente contra um fixture que tem a dimensao |
| **AC3** — a suite nunca fica verde com etapa que nao rodou ou sem saida | guarda de exit code em `suite_banco.sh` | `TRE_SUITE_SABOTAGEM=sem-saida` -> exit 1 (`etapa terminou SEM linha RESULTADO`); `TRE_SUITE_SABOTAGEM=zero-itens` -> exit 1 (`nao executou item nenhum`) | "sem output" e "0 item" nunca viram verde — provado por dois cenarios de sabotagem | **PASS** |

## 2. Numero, comando e exit code (nada "passou" sem os tres)

| # | Comando (na VPS, repo sincronizado) | Resultado medido | Exit |
|---|---|---|---|
| 1 | `bash /opt/tre/repo/scripts/db/suite_banco.sh dev` | `RESULTADO: SUITE_FALHOU (85 itens, 1 falha(s), 1 nao testavel(is))` | **1** |
| 2 | `... suite_banco.sh dev --somente-leitura` | `RESULTADO: SUITE_FALHOU (65 itens, 1 falha(s), 1 nao testavel(is))` | **1** |
| 3 | `... suite_banco.sh prod` | `FALHOU ADR-005: a suite escreve no alvo (etapa 4)` — recusado antes de tocar em qualquer coisa | **1** |
| 4 | `... suite_banco.sh homolog` | `FALHOU ambiente: alvo nao respondeu (No such container: pg-homolog)` | **1** |
| 5 | `... aplicar_migracoes.sh dev --somente-checar` | `FALHOU versao 0001 … migration aplicada e IMUTAVEL` / `MIGRACAO_FALHOU` | **1** |
| 6 | `... teste_tenant_rls.sh dev` | `TENANT_RLS_NAO_TESTAVEL (2 itens, 0 reprovacoes, 1 nao testavel(is))` | **3** |
| 7 | `... suite_banco.sh --prova-de-dente` | `SUITE_DENTE_OK (14 itens, 0 falhas)` | **0** |
| 8 | `... teste_tenant_rls.sh --prova-de-dente` | `TENANT_RLS_DENTE_OK (18 itens, 0 falhas)` | **0** |
| 9 | `... suite_banco.sh dev` (repetido ao fim) | mesmo veredito do item 1, alvo no mesmo estado | **1** |

Etapas do item 1 (cada uma com contagem propria): ambiente OK · contrato 37 itens · constraints/indices 16 itens ·
dedup sintetico 7 itens · dedup no ambiente 21 itens (`CENARIO_OK`, estado restaurado) · tenant/RLS
`NAO_TESTAVEL` (2 itens).

## 3. Por que AC2 fecha em NAO TESTAVEL (e nao em falha)

Medido no dev: 12 tabelas no schema, **0 coluna de cliente/tenant**, `relrowsecurity=false` nas 12,
**0 policy** em `pg_policies`, papel `sales_ai` com `superuser=true` e `bypassrls=true` (superuser contorna RLS
por definicao do PostgreSQL). Sem dimensao de cliente, a consulta do caminho proibido ("ler sem filtrar por
cliente") **nao e nem expressavel**: nao ha o que vazar nem o que filtrar. O criterio e por isso devolvido ao
Analista de Requisitos como **nao testavel contra a versao congelada do contrato** — o risco que ele deveria
cobrir (dois clientes no mesmo banco) nao esta no V1.0.

O teste nao se limita a dizer "nao da": ele tem **dente** contra um fixture descartavel que **tem** a dimensao
(2 clientes, RLS ligada, papel sem bypass) e ali reprova policy permissiva, `BYPASSRLS` e RLS desabilitada —
ou seja, no dia em que o contrato ganhar a dimensao de cliente, o teste ja esta pronto e ja foi provado.

## 4. Achado que o card abriu (defeito real, nao corrigido aqui)

A etapa de ambiente da suite acusa: a migration **registrada** pelo runner em dev
(`public.tre_schema_migrations.sha256 = bc766a818943…`) diverge do arquivo do repo
(`db/migrations/0001_sales_intelligence_v1.sql` = `0484a3701b8c…`).

- Causa medida: o commit `c795677` (decisao do dono, opcao 3 — trio canonico de banco) acrescentou 7 linhas de
  **comentario** ao fim do arquivo **depois** de a versao ja estar aplicada em dev
  (`aplicada_em 2026-09-30 17:29:22+00`). O diff entre os dois pontos e so o bloco de comentario: **nenhuma DDL muda**.
- Impacto medido (nao teorico): `aplicar_migracoes.sh dev --somente-checar` -> `MIGRACAO_FALHOU`, exit 1 —
  a regra da migration imutavel (BRANCHING.md) **para o runner de dev** para qualquer versao nova.
- Registrado como card de defeito no board, com este card (`t_c7281fce`) como pai. O E05 **nao** corrige:
  corrigir exige decisao de dono (re-aplicar/realinhar o registro, ou aceitar o sha novo), e o guard de
  comandos do harness nao autoriza mutacao de dado do runner nesta sessao.

Cards abertos por este card (o E05 fica **esperando** os dois — sao pre-requisitos, nao filhos):

| card | tipo | o que decide |
|---|---|---|
| `t_39838c5b` | defeito (D01) | migration registrada x arquivo: realinhar o registro ou reverter o comentario (decisao do dono) |
| `t_e340c29b` | requisito (D02) | AC2: dimensao de cliente entra no contrato ou fica fora de escopo com motivo |

## 5. Dois defeitos do proprio roteiro de prova (achados pelo dente, corrigidos no card)

1. Desfazer `DROP COLUMN cnpj` recriava a coluna mas **nao** o indice `idx_organizations_cnpj` — o PostgreSQL
   derruba o indice junto com a coluna. A suite continuava reprovando depois da "restauracao": o defeito era
   do **roteiro**, nao do schema.
2. A checagem da guarda de 0 item esperava a string `0 item`, que **nao** e prefixo de `0 itens` — a prova
   dizia "a guarda falhou" quando a guarda estava funcionando (`grep` correto: `nao executou item nenhum`).

Primeira execucao do dente: `SUITE_DENTE_FALHOU (12 itens, 3 falhas)`. Depois das correcoes:
`SUITE_DENTE_OK (14 itens, 0 falhas)`. **Nenhum artefato de teste foi aceito sem que a propria prova o
tivesse reprovado primeiro.**

## 6. Proveniencia da evidencia (o que garante que e do artefato real)

- Copia operacional `/opt/tre/repo` **ressincronizada** e shas conferidos nos dois lados: `suite_banco.sh f7cbcfa7…`,
  `teste_tenant_rls.sh 1f870a76…`, `estado_do_ambiente.sh 7f9a12a5…`, `deduplicar_organizacoes.py e5805846…`,
  `verificar_contrato_dados.py dfb8ad79…`, `verificar_constraints_indices.py 68cc57cb…`.
- **Correcao de rota registrada:** na primeira bateria o motor de dedup da copia operacional estava uma versao
  atras (`b009a8b9…`, sem as mudancas do E04-T02). A bateria inteira foi **refeita** depois da ressincronizacao
  para que a saida venha do artefato atual — evidencia colhida no artefato errado nao vale.
- Alvo dos testes destrutivos: **container descartavel** criado e destruido pelo proprio teste
  (`tre-suite-banco-*`, `tre-tenant-rls-*`). `docker ps -a` ao fim: so `pg-sales-dev`.
- Estado do dev **antes x depois**: `12 tabelas | 30 indices` nas duas pontas; a massa sintetica da etapa de
  dedup e limpa e conferida pela etapa.
- Producao **nao provisionada** (nao existe container/arquivo) e `--somente-leitura` obrigatorio ali.
  Homologacao **nao provisionada**: a suite falha de forma visivel (exit 1), nunca silenciosa.

## 7. Onde estao os arquivos

- `scripts/db/suite_banco.sh` — suite (um comando por ambiente, exit 0/1/3, resumo com contagem de itens).
- `scripts/db/teste_tenant_rls.sh` — isolamento entre clientes (le a dimensao do contrato; `--prova-de-dente`).
- `docs/runbooks/suite-de-teste-do-banco.md` — runbook: comando, vocabulario de exit, etapas, limites.
- `scripts/verificar_estrutura.sh` — passa a exigir os 3 artefatos versionados **e** executaveis.
- `CHANGELOG.md` e `docs/operations/registro-de-execucoes.md` — a medicao completa acima.

## 8. O que NAO foi feito (declarado, nao escondido)

- **Corrigir o defeito da migration registrada** — exige decisao de dono; virou card de defeito.
- **Executar em homologacao/producao** — ambientes nao provisionados; a suite recusa prod (ADR-005) e
  reporta homolog sem alvo (exit 1).
- **`shellcheck`** — nao existe no ambiente (nem no dev, nem na VPS); a checagem foi `bash -n` nos dois
  scripts (bash 5.2.37 no repo, 5.3.9 na VPS).
- **Provar AC2 em verde contra o contrato atual** — impossivel por ausencia da dimensao de cliente; provado
  em fixture descartavel (dente) e devolvido ao Analista de Requisitos. _(RESOLVIDO na rodada 2: o dono
  reformulou o criterio — ver §9.)_

## 9. RODADA 2 (30/09/2026) — AC2 na forma reformulada e defeito do rótulo do RESUMO

**O que destravou esta rodada (os dois pré-requisitos fecharam):**

| pré-requisito | decisão | efeito no E05 |
|---|---|---|
| `t_39838c5b` (defeito D01) | dono: opção 1 — realinhar o registro da migration 0001 em dev ao sha do arquivo | etapa `ambiente` da suíte passa a **OK**; `aplicar_migracoes.sh dev --somente-checar` → `MIGRACAO_OK`, exit 0 |
| `t_e340c29b` (requisito D02) | dono: **opção A**, isolamento **físico** (um banco por cliente); AC2 reformulado para **"não existem dois clientes no mesmo banco"** | a etapa 5 deixa de ser `tenant/RLS` (não decidível no V1.0) e passa a medir o que é medível |

### 9.1 AC2 × teste (forma vigente), com o caminho proibido e a fronteira

| critério (forma vigente) | teste positivo | teste negativo (caminho proibido) | fronteira | veredito |
|---|---|---|---|---|
| **AC2** — "não existem dois clientes no mesmo banco" | `teste_isolamento_clientes.sh dev` → `ISOLAMENTO_OK (5 itens, 0 falhas)`, exit 0: alvo responde, schema presente, **0** dimensão de cliente, **1** base de aplicação na instância, **1** base `pg-*` servindo o schema no host | `--prova-de-dente` (containers descartáveis): **dimensão de cliente com linhas de 2 clientes** → exit 1; **segunda base na mesma instância** → exit 1; **segundo serviço `pg-*` no host** → exit 1 | cada mutação desfeita → volta a `ISOLAMENTO_OK` (exit 0); **docker ausente → exit 3** (medição impossível nunca é verde) | **PASS** em dev (`ISOLAMENTO_OK`, exit 0) |

O instrumento antigo (`teste_tenant_rls.sh`) **saiu da suíte** e continua versionado como **instrumento do
V2** (mede a forma antiga do critério para o dia em que houver multi-cliente no mesmo banco). Medido nesta
rodada, ele segue em `TENANT_RLS_NAO_TESTAVEL`, exit 3 — **fora da conta do E05**.

### 9.2 Número, comando e exit code (nada "passou" sem os três) — bateria na VPS do dev

Os comandos foram executados no artefato **publicado pelo caminho versionado em destino isolado de ensaio**
(`deploy/publicar.sh --commit e74ec02` → `/opt/tre/.teste-publicacao-t_c7281fce`, `PUBLICACAO_OK`, digest
`3b431df7…`, 307 arquivos) — ver §9.6 para a correção de rota e por que a cópia operacional não foi tocada.

| # | comando (VPS, destino de ensaio `/opt/tre/.teste-publicacao-t_c7281fce`) | resultado medido | exit |
|---|---|---|---|
| 1 | `bash scripts/db/suite_banco.sh dev` | `SUITE_OK (89 itens, 0 falhas)` — ambiente 3 · contrato 37 · constraints 16 · dedup sintético 7 · dedup no ambiente 21 · isolamento 5 | **0** |
| 2 | `... dev --somente-leitura` | `SUITE_OK (69 itens, 0 falhas)` (etapa 4 vira varredura `--detectar`), sem escrever no alvo | **0** |
| 3 | `... prod` | `FALHOU ADR-005` (recusado antes de tocar no alvo) | **1** |
| 4 | `... homolog` | `FALHOU ambiente: alvo nao respondeu (No such container: pg-homolog)` | **1** |
| 5 | `teste_isolamento_clientes.sh dev` | `ISOLAMENTO_OK (5 itens, 0 falhas)` | **0** |
| 6 | `TRE_ISOLAMENTO_SEM_DOCKER=1 teste_isolamento_clientes.sh dev` | `ISOLAMENTO_NAO_TESTAVEL (5 itens, 0 reprovações, 1 item não medido)` | **3** |
| 7 | `teste_tenant_rls.sh dev` (instrumento do V2, fora da suíte) | `TENANT_RLS_NAO_TESTAVEL (2 itens)` | **3** |
| 8 | `aplicar_migracoes.sh dev --somente-checar` | `MIGRACAO_OK (0 aplicada/pendente, 1 pulada, 4 itens, 0 falhas)` | **0** |
| 9 | `suite_banco.sh --prova-de-dente` | `SUITE_DENTE_OK (19 itens, 0 falhas)` | **0** |
| 10 | `teste_isolamento_clientes.sh --prova-de-dente` | `ISOLAMENTO_DENTE_OK (17 itens, 0 falhas)` | **0** |

### 9.3 Defeito do rótulo do RESUMO (achado pelo card de D01) — corrigido e provado

- **Reprodução (antes):** a etapa `ambiente` reprovava (`FALHOU … migration do alvo DIVERGE`) e o bloco final
  imprimia `ambiente ............. OK` — a linha era **texto fixo** no script. Contagem e exit code estavam
  certos; o rótulo mentia. Quem lê só o resumo conclui "ambiente OK" com a suíte falhando por causa exatamente
  daquela etapa.
- **Correção:** a linha do RESUMO passa a sair do veredito **contado** (`FALHOU (N itens)` / `OK (N itens)`) e
  entrou a **guarda de consistência do RESUMO** (nº de linhas `FALHOU` no resumo ≥ nº de etapas reprovadas;
  desvio reprova a suíte e imprime `resumo ............... INCONSISTENTE`).
- **Prova negativa (tem dente):** no alvo descartável, o registro da migration é divergido
  (`INSERT … sha256='000…'` na tabela de controle) → suíte **exit 1**, aponta `FALHOU ambiente: migration do
  alvo`, o resumo declara `ambiente ... FALHOU` e **não** existe linha `ambiente ... OK`; removido o registro,
  a suíte volta ao verde (exit 0). Tudo medido no `--prova-de-dente` (itens 6–10 do `SUITE_DENTE_OK`).

### 9.4 Fronteiras medidas nesta rodada

- **Verde ↔ não-verde:** `ISOLAMENTO_OK` (exit 0) quando todos os itens medem; `ISOLAMENTO_NAO_TESTAVEL`
  (exit 3) quando a medição de provisionamento **não pode** ser feita; `ISOLAMENTO_FALHOU` (exit 1) quando
  dois clientes aparecem no mesmo banco/servidor/host. A suíte propaga: 0 → `SUITE_OK`, 3 → `SUITE_NAO_TESTAVEL`,
  1 → `SUITE_FALHOU`.
- **Sabotagem de saída (AC3):** `TRE_SUITE_SABOTAGEM=sem-saida` → exit 1 (`SEM linha RESULTADO`);
  `=zero-itens` → exit 1 (`nao executou item nenhum`) — segue com dente.
- **Mutações de schema (AC1):** `DROP COLUMN organizations.cnpj` → exit 1 apontando `contrato`; índice a mais
  → exit 1 apontando `constraints`; cada uma desfeita → volta a `SUITE_OK` (exit 0).

### 9.5 Proveniência (a evidência é do artefato real, não de stub)

- **O artefato foi publicado pelo caminho versionado, em destino isolado de ensaio (decisão 2 do
  `t_091cfea9`):** `TRE_PUBLICAR_DESTINO=/opt/tre/.teste-publicacao-t_c7281fce deploy/publicar.sh --commit
  e74ec02 --card t_c7281fce` → `PUBLICACAO_OK commit=e74ec02… digest=3b431df75535… arquivos=307`. O script
  publica o **commit**, nunca a árvore de trabalho, e grava `.publicado`/`.publicado.manifest` no destino.
- **Shas dos artefatos sob teste no destino de ensaio** (conferidos também no repo local, iguais):
  `suite_banco.sh 5fb644a2375e…`, `teste_isolamento_clientes.sh f3586c102e30…`,
  `teste_tenant_rls.sh 217bfd050a14…`; e dos reusados (artefatos executados, não documentação): `estado_do_ambiente.sh 7f9a12a50481…`, `deduplicar_organizacoes.py e58058469a06…`,
  `verificar_contrato_dados.py dfb8ad79…`, `verificar_constraints_indices.py 68cc57cb…`,
  `0001_sales_intelligence_v1.sql 0484a3701b8c…` (inalterado).
- **A cópia operacional `/opt/tre/repo` não foi tocada por esta rodada** e foi **conferida** no fim:
  `deploy/publicar.sh --conferir` → `PUBLICACAO_OK commit=c7972ca… digest=89729f5d… arquivos=310`, exit 0.
  (O `--conferir` sai do **git**, não da árvore de trabalho: roda a partir de um checkout git — o modo
  `--conferir` na própria VPS falha com `nao estou num repositorio git`, e é assim que tem de ser.)
- **Alvo dos testes destrutivos:** containers **descartáveis** criados e destruídos pelo próprio teste
  (`tre-suite-banco-*`, `tre-isolamento-*`, e o `pg-cliente-b-*` do item de co-locação no host, removido no
  fim). `docker ps -a` ao fim da bateria: **só `pg-sales-dev`**.
- **Estado do dev antes × depois:** `12 tabelas | 30 índices` nas duas pontas; a massa sintética da etapa 4
  é limpa e conferida pela própria etapa (`ambiente volta ao estado anterior`).
- **Produção e homologação intocadas:** `prod` recusado (ADR-005, exit 1); `/opt/tre/prod` e
  `/opt/tre/homolog` com **0 arquivo**; nenhum container de homologação.
- **Logs brutos na VPS:** `/tmp/e05r2_bateria.log` (itens 1–10 + estado final, com `### EXIT=` por comando).

### 9.6 Correção de rota registrada — como o artefato chegou à VPS

A **primeira** bateria desta rodada rodou contra a cópia operacional `/opt/tre/repo` sincronizada com
`tar -cz … | ssh … 'tar -xz'` — o mesmo padrão usado na rodada 1 deste card. Esse padrão **não é o caminho
de publicação aceito** (`deploy/publicar.sh`, card `t_091cfea9`): a sincronização sobrescreveu a árvore
publicada (`c7972ca`, `fix/TRE-W1-E06-T01-F2`, publicada 21:34:32Z) com a árvore de `develop`, e o card
`t_1b2ab418` (DEFEITO F2) **mediu** o dano às 21:43:17Z (`deploy/publicar.sh --conferir` →
`PUBLICACAO_DIVERGENTE`, exit 5; `scripts/backup/backup-tre.sh` de volta à versão pré-correção; rotina de
backup voltando a imprimir `BACKUP_OK` sem cobrir ambiente). O dano foi **reparado pelo próprio caminho
versionado** (republicação às 21:52:24Z, conferida nesta rodada: `PUBLICACAO_OK … arquivos=310`).

O que este card fez com isso, na ordem:

1. **Parou** a bateria que estava rodando contra a cópia (os resultados dela **não** foram usados — evidência
   colhida no artefato errado não vale);
2. **Publicou o commit entregue pelo caminho aceito**, em **destino isolado de ensaio**
   (`/opt/tre/.teste-publicacao-t_c7281fce`), que não é alvo de `ExecStart` de timer nenhum;
3. **Rodou a bateria inteira de novo** nesse destino (§9.2) — é esta a evidência do card;
4. **Conferiu a cópia operacional ao fim** (`PUBLICACAO_OK`, `c7972ca`) e **não a escreveu**;
5. **Corrigiu o runbook** (§1.1): `tar` para a cópia é proibido; artefato de card vai por
   `deploy/publicar.sh` em destino isolado de ensaio. O padrão da rodada 1 não deve ser repetido por
   nenhum card.

O instrumento do V2 (`teste_tenant_rls.sh`) segue no repo, fora da suíte, sem mudança de comportamento.

### 9.7 O que esta rodada NÃO fecha (declarado, não escondido)

- **Homologação** continua não provisionada — o critério não pôde ser medido lá (o TEST PLAN pedia dev e
  homolog); `homolog` sai exit 1 apontando o alvo inexistente.
- **A barreira do AC2 é de provisionamento** (decisão do dono): o teste prova o que é medível e **não**
  afirma que material de um cliente é indistinguível de outro em uma base sem dimensão de cliente — isso
  está declarado no runbook §8 e no contrato.
- **`shellcheck`** não existe no ambiente (nem no dev, nem na VPS): a checagem de sintaxe foi `bash -n` nos
  quatro scripts alterados.

---

## 10. RODADA 3 (30/09/2026) — os 3 itens da revisão independente, corrigidos e medidos

A revisão independente (`revisor`, rodada 1) **reproduziu** `SUITE_OK (89)`, `ISOLAMENTO_OK (5)`, os dois
dentes e a guarda ADR-005 na VPS do dev, deu **AC1/AC2/AC3 = PASS** — e **reprovou 3 itens** que não eram de
medição, e sim de **texto × medição**. Esta rodada fecha os três e prova, com o mesmo instrumento, que o
verde falso que ela mediu deixou de existir.

Artefato desta rodada: commit **`21ed325`** (origin/develop).
`sha256` dos scripts sob teste (destino de ensaio publicado, iguais ao commit):
`teste_isolamento_clientes.sh a69e08d1…` · `teste_tenant_rls.sh d4ade211…` · `suite_banco.sh 5fb644a2…`
(não alterado nesta rodada) · `estado_do_ambiente.sh 7f9a12a5…` · `aplicar_migracoes.sh d0baf1e1…`.

### 10.1 Item 1 — a régua de aceite continuava com o AC2 antigo

- **O que a revisão mediu:** `docs/kanban/criterios-de-aceitacao.md` (linha 141) ainda trazia "consulta sem
  filtro de tenant devolve vazio ou erro", **sem nota** da reformulação; o cabeçalho do próprio arquivo diz
  *"card cujo critério não bater não fecha"*. O entregue media a forma nova e a régua, a antiga.
- **Correção:** **nota datada (30/09/2026)** na seção `TRE-W1-E05-T01` da régua, registrando a decisão do dono
  (opção A, card `t_e340c29b`, `docs/operations/registro-de-aprovacoes.md`), **com o texto novo** — "não
  existem dois clientes no mesmo banco" —, e declarando que a forma antiga foi medida e devolvida ao
  requisito (`NAO_TESTAVEL`) por não ser decidível contra o V1. A régua e o entregue não se contradizem mais.
  A medição que prova a forma nova é a do §10.4 (item 5 e item 10).

### 10.2 Item 2 — o item 3 falhava ABERTO e a superfície do detector era estreita

**O defeito, medido pela revisão e reproduzido por mim nos dois artefatos** (container descartável
`postgres:16`, migration congelada aplicada, mutação `ALTER TABLE … ADD COLUMN tenant_uuid uuid`, medição
por `--prefixo`):

| medição (mesmo alvo mutado) | artefato da RODADA 2 (`f3586c10…`) | artefato desta rodada (`a69e08d1…`) |
|---|---|---|
| `organizations.tenant_uuid` presente | `-- dimensao de cliente/tenant no schema: 0 ((nenhuma))` → `OK` → `ISOLAMENTO_OK (5 itens)` **exit 0** (verde falso) | `FALHOU dimensao de cliente no schema: 1 coluna(s) … (organizations.tenant_uuid )` → `ISOLAMENTO_FALHOU` **exit 1** |
| `contacts."conta_Cliente"` (grafia mista, token no meio) | `ISOLAMENTO_OK (5 itens)` **exit 0** (verde falso) | `FALHOU … (contacts.conta_Cliente )` **exit 1** |
| catálogo ilegível (o alvo responde; a leitura de `information_schema.columns` falha) | `ISOLAMENTO_OK (5 itens)` **exit 0** (verde falso) | `NAO_TESTAVEL nao consegui medir a dimensao de cliente no schema (leitura vazia/erro NAO e '0 coluna')` → `ISOLAMENTO_NAO_TESTAVEL (5 itens, 1 item não medido)` **exit 3** |
| dente do próprio teste | `ISOLAMENTO_DENTE_OK (17 itens)` — **nenhum** caso cobria essas grafias/catálogo mudo | `ISOLAMENTO_DENTE_OK (26 itens, 0 falhas)` com os casos novos |

**Correções:**

1. **fail-closed na leitura:** `leitura()` passou a devolver `exit != 0` quando a consulta falha e o item 3
   exige **número** (`numero()`); leitura vazia/erro → `NAO_TESTAVEL` (exit 3, **nunca verde**) com a causa
   impressa. O item 4 ganhou o mesmo cuidado (leitura que falha → **reprovação**, não "0 bases").
   *Vocabulário declarado:* leitura impossível é **não medível** (exit 3, mesma família do "docker ausente"),
   **não** "critério violado" (exit 1) — reprovar sem ter medido seria afirmar violação que não foi observada;
   os dois são não-verdes e o dente prova o exit 3.
2. **superfície do detector:** regex passou a `(^|_)(tenant|tenants|cliente|clientes|client|clients)(_|$)`
   aplicada com **`~*`** (case-insensitive, token em qualquer posição) — pega `tenant_id`, `tenant_uuid`,
   `conta_cliente`, `conta_Cliente`. Não há falso positivo no contrato: **0** colunas casam na base dev
   (mesmo resultado da regex antiga), e as 203 colunas do schema foram conferidas.
3. **casos de dente novos (3):** `tenant_uuid` → exit 1; `conta_Cliente` (maiúscula, token no meio) → exit 1;
   **catálogo ilegível** → exit 3. Cada mutação desfeita volta a `ISOLAMENTO_OK`.
4. **superfície declarada (o que fica fora):** coluna de cliente com **outra grafia** (ex. `customer_id`) não
   é pega pelo item 3 — quem a pega é a **etapa 1** (`contrato`), que exige as colunas exatamente como no
   contrato e aponta `sobram=[...]` (foi o que a própria revisão mediu). Declarado no runbook §8; a etapa 5
   não é o único controle.
5. `scripts/db/teste_tenant_rls.sh` (instrumento do V2, fora da suíte) passou a usar **a mesma** regex/`~*`,
   para não haver duas definições de "coluna de cliente" no repo; comportamento preservado:
   `TENANT_RLS_NAO_TESTAVEL` (exit 3) no dev e `TENANT_RLS_DENTE_OK (18 itens)` no dente.

### 10.3 Item 3 — provisionamento: o texto dizia mais do que a medição cobria

- **O que a revisão mediu:** o item 5 mede `docker ps | grep '^pg-'` — **convenção de nome**; e o texto
  afirmava "o provisionamento nao co-loca clientes". O item 4 só enxerga a instância do alvo.
- **Correção:** o texto do veredito passou a dizer **exatamente** o que é medido ("uma base provisionada pela
  convenção de nome `pg-*` serve o schema … nenhuma SEGUNDA base provisionada; provisionamento fora da
  convenção não é medido por este item") e a saída ganhou a linha **informativa** com os containers de pé
  **fora** da convenção que servem o schema — nunca escondidos, nunca contados. Medido: no dente, o container
  `e05r3-probe` (fora da convenção, servindo o schema) aparece no informativo e o item permanece `OK`; no dev,
  o informativo saiu `nenhum`. O runbook §8 ganhou o bullet do limite.

### 10.4 Bateria na VPS do dev contra o artefato publicado (11 itens)

Destino de ensaio isolado `/opt/tre/.teste-publicacao-t_c7281fce`, publicado pelo caminho versionado
(`deploy/publicar.sh --commit 21ed325 --card t_c7281fce` → `PUBLICACAO_OK … digest=c35ecba3… arquivos=307`).
A cópia operacional `/opt/tre/repo` **não** foi escrita nesta rodada.

| # | comando (no destino publicado) | resultado medido | exit |
|---|---|---|---|
| 1 | `bash scripts/db/suite_banco.sh dev` | `SUITE_OK (89 itens, 0 falhas)` | **0** |
| 2 | `... suite_banco.sh dev --somente-leitura` | `SUITE_OK (69 itens, 0 falhas)` | **0** |
| 3 | `... suite_banco.sh prod` | `FALHOU ADR-005` (recusado antes de tocar no alvo) | **1** |
| 4 | `... suite_banco.sh homolog` | `FALHOU ambiente` (`pg-homolog` não existe) | **1** |
| 5 | `teste_isolamento_clientes.sh dev` | `ISOLAMENTO_OK (5 itens, 0 falhas)` | **0** |
| 6 | `env TRE_ISOLAMENTO_SEM_DOCKER=1 teste_isolamento_clientes.sh dev` | `ISOLAMENTO_NAO_TESTAVEL (5 itens, 1 não medido)` | **3** |
| 7 | `teste_tenant_rls.sh dev` (instrumento do V2) | `TENANT_RLS_NAO_TESTAVEL (2 itens)` | **3** |
| 8 | `aplicar_migracoes.sh dev --somente-checar` | `MIGRACAO_OK (4 itens, 0 falhas)` | **0** |
| 9 | `suite_banco.sh --prova-de-dente` | `SUITE_DENTE_OK (19 itens, 0 falhas)` | **0** |
| 10 | `teste_isolamento_clientes.sh --prova-de-dente` | `ISOLAMENTO_DENTE_OK (26 itens, 0 falhas)` | **0** |
| 11 | `teste_tenant_rls.sh --prova-de-dente` | `TENANT_RLS_DENTE_OK (18 itens, 0 falhas)` | **0** |

Etapas do item 1: ambiente `OK` · contrato 37 itens · constraints 16 itens · dedup sintético (48 itens) ·
dedup no ambiente 21 itens (`CENARIO_OK`, estado restaurado) · isolamento 5 itens.

### 10.5 Proveniência, estado do ambiente e uma diferença explicada

- **Estado do dev antes × depois:** `12 tabelas | 30 índices`; contagens `organizations=2 / contacts=1 /
  interactions=1`; registro da migration `0484a370…` == arquivo do repo; `pg-sales-dev` de pé e dev intacto.
- **ADR-005:** `/opt/tre/{prod,homolog}` com **0 arquivo**; `prod` recusado antes de tocar no alvo.
- **Sobra de outro card (registrada, não é minha):** no fim da bateria, `docker ps -a` mostrou
  `tre-restore-637931-27223` (teste de backup/restore, card de E06 rodando em paralelo) ao lado de
  `pg-sales-dev`; ela estava fora da convenção `pg-*` e já havia sido removida quando fui inspecioná-la — é o
  caso concreto do limite declarado no §10.3. Meus containers descartáveis (`tre-isolamento-*`) foram todos
  removidos pelos próprios testes.
- **Diferença explicada (47 → 48 itens no dedup sintético, em relação à rodada 2):** não é mudança deste
  card — `git diff e74ec02 21ed325 -- scripts/` mostra que o motor de dedup
  (`scripts/dedup/deduplicar_organizacoes.py`) e `teste_entity_match_confidence.sh` foram alterados pelo card
  **TRE-W1-E04-T02** (commit `7a6a270`, defeito D02), que entrou em `develop` entre as rodadas. O
  commit desta rodada toca **4 arquivos**: os dois scripts de teste e dois documentos.
- **Evidência bruta:** `EVIDENCIA_t_c7281fce_rodada3.txt`, `e05r3_bateria_final.log` (11 itens, com
  `### EXIT=` por comando) e `e05r3_prova_dente.log` (antigo × novo, lado a lado) — anexos do card.

### 10.6 O que esta rodada NÃO fecha (declarado)

- **Homologação** segue não provisionada (`suite_banco.sh homolog` → exit 1 apontando o alvo inexistente); o
  TEST PLAN pede dev **e** homolog — o segundo ambiente é provisionamento do dono, não deste card.
- **Homologação humana (estágio 7) é do Anderson** — eu não homologo o meu próprio trabalho.
- **`shellcheck`** não existe no ambiente: a checagem foi `bash -n` nos scripts alterados.
- **Duas escolhas declaradas, não escondidas:** (a) leitura de catálogo que falha vira **não medível**
  (exit 3) e não "critério violado" (exit 1) — §10.2.1; (b) coluna de cliente com grafia fora da superfície
  do item 3 é pega pela **etapa 1**, não pela etapa 5 — §10.2.4.
