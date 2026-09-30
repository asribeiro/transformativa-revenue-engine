# Runbook — suíte de teste do banco (Data Contract V1)

**Card que criou isto:** `TRE-W1-E05-T01` (W1 · E05) · **Perfil dono:** Analista de Teste
**Regra que manda:** ADR-0008 (o trabalho de banco roda **na VPS do ambiente**, via `docker exec`) ·
ADR-005 (nada nasce em produção) · doc 11 §2 (AC/TEST/ROLLBACK/RISK no card).

## 1. Um comando

```bash
# na VPS do ambiente (usuário tre-deploy, com docker)
bash /opt/tre/repo/scripts/db/suite_banco.sh dev
```

O alvo (container/usuário/banco) sai de `deploy/environments/<ambiente>.env`, e a variável de ambiente
do operador **vence** o arquivo (mesma precedência do runner de migrations):

```bash
TRE_PG_SERVICO=outro TRE_PG_USER=outro TRE_PG_DB=outro_banco bash scripts/db/suite_banco.sh dev
```

### 1.1 De onde vem o artefato que se está testando (o caminho de escrita da cópia)

`/opt/tre/repo` é a **cópia operacional publicada** e o alvo do `ExecStart` dos timers (`tre-backup.service`
etc.). Ela é escrita **só** pelo caminho versionado:

```bash
# publica um COMMIT (nunca a árvore de trabalho) — card t_091cfea9 (DEFEITO F3 do E06)
deploy/publicar.sh --commit <sha|ref>          # destino padrão: /opt/tre/repo
deploy/publicar.sh --conferir                   # confere a cópia contra o `.publicado` (exit 5 se divergir)
```

O `--conferir` roda **a partir de um checkout git** (a publicação sai do git, nunca da árvore de trabalho):
chamá-lo dentro da própria VPS falha com `nao estou num repositorio git`.

**Sincronizar a cópia com `tar -cz … | ssh … 'tar -xz'` é proibido:** foi esse padrão (usado na rodada 1
deste card) que sobrescreveu a cópia, ressuscitou rotina pré-correção e fez o backup diário voltar a mentir
— medido pelo card `t_1b2ab418` (DEFEITO F2) e causa-raiz do defeito `t_daca4bda` (dono `devops`). Para
testar um artefato **antes** de ele estar publicado, use um **destino isolado de ensaio** (decisão 2 do
`t_091cfea9`), que não é o alvo dos timers:

```bash
TRE_PUBLICAR_DESTINO=/opt/tre/.teste-publicacao-<card> deploy/publicar.sh --commit <sha> --card <card>
bash /opt/tre/.teste-publicacao-<card>/scripts/db/suite_banco.sh dev
```

## 2. Veredito: o exit code é a resposta

| exit | veredito | significado |
|---|---|---|
| 0 | `SUITE_OK` | todas as etapas passaram e **executaram itens** |
| 1 | `SUITE_FALHOU` | alguma etapa **reprovou** (o log aponta o item) |
| 2 | — | uso incorreto |
| 3 | `SUITE_NAO_TESTAVEL` | nenhuma reprovação, mas **critério homologado não é testável** contra o artefato — **não é verde** |

**Guarda de confiabilidade (AC3):** etapa que termina **sem a linha `RESULTADO:`** ou que executa
**0 item** reprova a suíte. "Sem output" nunca vale como verde. A guarda tem prova negativa: em
`--prova-de-dente` a suíte é rodada com uma etapa sabotada (`TRE_SUITE_SABOTAGEM=sem-saida|zero-itens`)
e **tem** de sair com exit 1.

## 3. Etapas (o alvo é o mesmo para todas — resolvido uma vez, no início)

| # | etapa | o que prova | ferramenta reusada |
|---|---|---|---|
| 0 | ambiente | identidade do alvo (`usuario@banco`), estado do schema e sha da migration registrada × repo | `estado_do_ambiente.sh` + consultas read-only |
| 1 | contrato | colunas, tipos, PK, FK, NOT NULL contra o Data Contract V1.0 | `verificar_contrato_dados.py --banco` |
| 2 | constraints/índices | os 30 índices e PK/FK/UNIQUE item a item | `db/verificar_constraints_indices.py --banco` |
| 3 | dedup sintético | identificadores fortes, limite **0,94 × 0,95**, auditoria e governança do limiar | `dedup/teste_dedup_sintetico.sh` |
| 4 | dedup no ambiente | cenário real: detecta, mergeia, audita, desfaz, limpa e **volta ao estado anterior** | `dedup/deduplicar_organizacoes.py --cenario-ambiente` |
| 5 | isolamento | **um cliente por base** — "não existem dois clientes no mesmo banco": 0 dimensão de cliente no schema, 1 base de aplicação na instância do alvo, 1 base provisionada (`pg-*`) servindo o schema no host | `db/teste_isolamento_clientes.sh` |

`--somente-leitura` não escreve no alvo (a etapa 4 vira varredura `--detectar`) e é o único modo
permitido em `prod`. Sem ele, `prod` é **recusado** (ADR-005).

## 4. Isolamento entre clientes (etapa 5) — como se lê o resultado

**O critério foi reformulado pela decisão do dono de 30/09/2026 (opção A — isolamento físico, um banco por
cliente; card `t_e340c29b`, registrado em `docs/operations/registro-de-aprovacoes.md` e em
`docs/data/DATA_CONTRACT_V1.md`):**

| | |
|---|---|
| **forma antiga** | "consulta sem filtro de tenant devolve vazio ou erro — nunca material de outro cliente" |
| **forma nova (medida)** | "**não existem dois clientes no mesmo banco**" |

A forma antiga **não é decidível** contra o Data Contract V1.0 (sem dimensão de cliente, RLS desligada e
papel `superuser`+`bypassrls`): não há o que filtrar. O dono decidiu que a barreira passa a ser de
**provisionamento** — cada cliente tem banco próprio — e o critério virou a forma nova, que **é** medível.

O veredito sai de medição, não de prosa (`db/teste_isolamento_clientes.sh`):

1. **alvo responde e tem o schema** — sem alvo não há veredito (fail-closed, exit 1);
2. **dimensão de cliente no schema** — coluna cujo nome tenha o token `tenant|cliente|client` (e plurais)
   no início, no fim ou entre `_`, **case-insensitive** (`tenant_id`, `tenant_uuid`, `conta_cliente`,
   `conta_Cliente`). Uma coluna dessas significa base que **pode** co-locar dois clientes: reprova (o V2
   exige decisão nova). **Leitura do catálogo que falha/vem vazia não vale como "0 coluna"**: sai
   `NAO_TESTAVEL` (exit 3, nunca verde) — foi o defeito medido na revisão independente, com caso de dente
   próprio (item e, abaixo);
3. **bases de aplicação na instância do alvo** — `pg_database` sem templates: duas bases = dois clientes no
   mesmo servidor → reprova; leitura que falha → reprovação (sem medição não há cumprimento);
4. **base provisionada no host** — containers `pg-<cliente>-<amb>` de pé que servem o schema: mais de uma
   → reprova; nenhuma → **não medido** (exit 3, nunca verde); sem docker no host → exit 3, nunca verde.
   **Limite declarado (§8):** a medição deste item é **por convenção de nome `pg-*`**; container de pé fora
   da convenção que sirva o schema **não** conta como base provisionada — ele é impresso como
   *informativo* na saída, nunca escondido.

Estado medido em **dev** (`pg-sales-dev`, 30/09/2026): 0 coluna de cliente/tenant em 12 tabelas, 1 base de
aplicação na instância, 1 base provisionada (`pg-sales-dev`) → `ISOLAMENTO_OK (5 itens, 0 falhas)`, exit 0.

`teste_isolamento_clientes.sh --prova-de-dente` prova que a medição **tem dente**, em containers
descartáveis: base íntegra → exit 0; **dimensão de cliente com linhas de 2 clientes** (`tenant_id`) → exit 1;
**a mesma co-locação na grafia `tenant_uuid`** → exit 1; **a mesma co-locação na grafia mista
`conta_Cliente`** → exit 1; **segunda base na mesma instância** → exit 1; **segundo serviço `pg-*` servindo o
schema no host** → exit 1; **docker ausente** → exit 3 e **catálogo ilegível** (o alvo responde, a leitura do
catálogo falha) → exit 3 (nenhum dos dois vira verde); cada mutação desfeita volta a aprovar.

> **Instrumento do V2 (não wired na suíte):** `db/teste_tenant_rls.sh` mede a **forma antiga** do critério
> (tenant/RLS de primeira classe) e continua versionado para o dia em que houver multi-cliente no mesmo
> banco — nesse cenário ele é o teste que prova o fail-closed. Enquanto o V2 não for decidido, o critério
> vigente é o da etapa 5. Ele usa a **mesma superfície de detector** do teste vigente (token
> `tenant|cliente|client` em qualquer posição, `~*`), para não haver dois critérios de "coluna de cliente"
> no repo.

## 5. Prova de dente da própria suíte (AC1 e AC3)

```bash
bash scripts/db/suite_banco.sh --prova-de-dente
```

Sobe um PostgreSQL descartável, aplica a migration congelada e roda a suíte contra ele **nove vezes**:
alvo íntegro → exit 0 (`SUITE_OK`); duas sabotagens de saída (`TRE_SUITE_SABOTAGEM=sem-saida|zero-itens`)
→ exit 1; **registro da migration divergido** → exit 1 com o `RESUMO` declarando
`ambiente ... FALHOU` (guarda do rótulo, defeito do card de D01) e volta a verde quando o registro é
removido; coluna do contrato removida → exit 1 apontando o item; índice a mais → exit 1 apontando o item;
cada divergência desfeita → volta ao verde. Sem isso a suíte poderia estar passando por construção.

Nada disso toca ambiente real: o container é criado e removido pelo próprio teste.

## 6. Rollback

Reverter o commit da suíte. Os três scripts (`suite_banco.sh`, `teste_isolamento_clientes.sh` e o
instrumento do V2 `teste_tenant_rls.sh`) são aditivos; a suíte **não** executa DDL/DML no schema do
contrato (escritas só em container descartável), e a única escrita no ambiente é a massa sintética da
etapa 4, que se auto-limpa e é conferida contra a contagem anterior (`estado ANTES == DEPOIS`, item da
própria etapa). A etapa 5 é **somente leitura** no alvo (catálogo, `pg_database` e `docker ps`).

## 7. Quando rodar

- **dev**: a cada mudança de schema/migration, junto do runner (`aplicar_migracoes.sh dev`).
- **homologação**: depois de provisionada e migrada (mesmo comando, `homolog`).
- **produção**: `--somente-leitura`, como verificação pós-deploy (ADR-005: a suíte nunca escreve em prod).

## 8. Limites conhecidos (declarados, não escondidos)

- **A barreira do AC2 é de PROVISIONAMENTO, não de schema** — por decisão do dono (opção A, 30/09/2026).
  A suíte mede o que é medível (0 dimensão de cliente, 1 base por instância, 1 base provisionada por host)
  e **não** finge provar o que não é distinguível: em um schema sem dimensão de cliente, material de um
  segundo cliente seria indistinguível do primeiro (não há rótulo a comparar). Quem "esquecer o filtro" não
  é o risco nesta opção; o risco é co-locar dois clientes no mesmo banco, e é isso que os itens 2–4 medem.
- **Medição impossível nunca vira verde:** sem docker no host, sem nenhuma base `pg-*` servindo o schema, ou
  com o **catálogo ilegível** (a leitura do item 3 falha/vem vazia), a etapa 5 sai em **exit 3**
  (`ISOLAMENTO_NAO_TESTAVEL`) — e a suíte, com ela, em `SUITE_NAO_TESTAVEL`.
- **Superfície do detector de coluna de cliente (o que a etapa 5 pega — e o que ela não pega):** a etapa 5
  reprova (exit 1) coluna cujo nome tenha o token `tenant|cliente|client` (ou plural) no início, no fim ou
  entre `_`, **case-insensitive**: `tenant_id`, `tenant_uuid`, `conta_cliente`, `conta_Cliente` — três casos
  de dente provam (`tenant_id`, `tenant_uuid`, `conta_Cliente`). Coluna de cliente com **outra grafia**
  (ex. `customer_id`, `conta_id`) fica **fora** desta superfície e **não** é pega pelo item 3; quem a pega é a
  **etapa 1** (`contrato`), que exige das colunas do banco exatamente as do contrato e aponta
  `sobram=[...]` — foi o que a revisão independente mediu (com `organizations.tenant_uuid` a suíte reprova
  pela etapa 1, mesmo quando o item 3 não casa o nome). Defesa em profundidade declarada; a etapa 5 **não** é
  o único controle.
- **O item 5 mede por convenção de nome (`pg-*`):** o controle de provisionamento é o padrão
  `pg-<cliente>-<amb>`; um container de pé **fora** da convenção que sirva o schema **não** conta como base
  provisionada — a própria saída imprime quantos desses existem (informativo, nunca escondido). Um
  provisionamento feito fora da convenção **não é medido** por esta suíte (o critério do AC2 é de
  provisionamento, decidido pelo dono). Limite declarado, não verde implícito.
- Homologação **não está provisionada** (não existe container `pg-homolog`): `suite_banco.sh homolog` sai
  com exit 1 e aponta o alvo inexistente — falha visível, nunca silenciosa.
- A etapa 0 acusa quando a migration registrada pelo runner **diverge** do arquivo do repo, e o `RESUMO`
  declara `ambiente ... FALHOU` (guarda do rótulo). O defeito D01 (registro `bc766a81…` × arquivo
  `0484a370…`) foi fechado em 30/09/2026 (card `t_39838c5b`): em dev as três fontes estão alinhadas em
  `0484a370…` e a etapa 0 sai `OK`.
