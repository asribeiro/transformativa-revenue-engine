# Relatorio de teste — TRE-W1-E05-T01 (suite de teste do banco)

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
  em fixture descartavel (dente) e devolvido ao Analista de Requisitos.
