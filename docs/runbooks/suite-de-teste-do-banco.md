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
| 5 | tenant/RLS | consulta **sem filtro de tenant** devolve vazio ou erro — nunca material de outro cliente | `db/teste_tenant_rls.sh` |

`--somente-leitura` não escreve no alvo (a etapa 4 vira varredura `--detectar`) e é o único modo
permitido em `prod`. Sem ele, `prod` é **recusado** (ADR-005).

## 4. Isolamento entre clientes (etapa 5) — como se lê o resultado

A convenção que a suíte assume: o cliente da sessão vem do GUC `app.tenant_id` e a policy usa
`current_setting('app.tenant_id', true)` (fail-closed). Ambiente com outro nome passa `--guc-tenant`.

O veredito sai de medição, não de prosa:

1. **dimensão de cliente** — colunas casando `(^|_)(tenant|cliente|client)(_id)?$`;
2. **RLS** — `pg_class.relrowsecurity` por tabela e `pg_policies`;
3. **papel da aplicação** — `rolsuper`, `rolbypassrls` e se é dono de tabela com RLS **sem**
   `FORCE ROW LEVEL SECURITY` (dono contorna a policy; `superuser=true`/`bypassrls=true` = a policy não
   se aplica a ele);
4. **prova funcional** — como o papel da aplicação: sessão **sem** tenant → a consulta sem filtro tem de
   devolver **vazio ou erro**; sessão **com** o cliente X → a mesma consulta sem filtro não pode devolver
   nenhuma linha de outro cliente (`vazado = total − material do cliente X`).

**Sem dimensão de cliente o critério é indecidível, e a suíte diz isso com exit 3** — nunca com verde.
Estado medido em **dev** (`pg-sales-dev`, Data Contract V1.0, 30/09/2026): 0 coluna de cliente/tenant em
12 tabelas, RLS desabilitada nas 12, 0 policy, papel `sales_ai` com `superuser=true` e `bypassrls=true`.

`teste_tenant_rls.sh --prova-de-dente` prova que esse veredito **tem dente**, em container descartável
com fixture multi-cliente: alvo sem dimensão → exit 3; alvo protegido → exit 0; e **reprova** (exit 1)
quando alguém (i) escreve policy permissiva (`USING (true)`), (ii) liga `BYPASSRLS` no papel da aplicação
ou (iii) deixa `DISABLE ROW LEVEL SECURITY` em tabela com coluna de cliente — cada mutação desfeita volta a
aprovar.

## 5. Prova de dente da própria suíte (AC1)

```bash
bash scripts/db/suite_banco.sh --prova-de-dente
```

Sobe um PostgreSQL descartável, aplica a migration congelada e roda a suíte contra ele **quatro vezes**:
alvo íntegro → exit 3 (nada reprovado, critério de tenant não testável); coluna do contrato removida →
exit 1 apontando o item; índice a mais → exit 1 apontando o item; cada divergência desfeita → volta ao
veredito sem reprovação. Sem isso a suíte poderia estar passando por construção.

Nada disso toca ambiente real: o container é criado e removido pelo próprio teste.

## 6. Rollback

Reverter o commit da suíte. Os dois scripts são aditivos; a suíte **não** executa DDL/DML no schema do
contrato (escritas só em container descartável), e a única escrita no ambiente é a massa sintética da
etapa 4, que se auto-limpa e é conferida contra a contagem anterior (`estado ANTES == DEPOIS`, item da
própria etapa).

## 7. Quando rodar

- **dev**: a cada mudança de schema/migration, junto do runner (`aplicar_migracoes.sh dev`).
- **homologação**: depois de provisionada e migrada (mesmo comando, `homolog`).
- **produção**: `--somente-leitura`, como verificação pós-deploy (ADR-005: a suíte nunca escreve em prod).

## 8. Limites conhecidos (declarados, não escondidos)

- O critério de tenant/RLS **não é provável** contra o Data Contract V1.0: a V1 não tem dimensão de
  cliente. Enquanto o contrato não mudar (nova versão + aprovação humana — `DATA_CONTRACT_V1.md` §10) ou o
  critério não for reescrito pelo Analista de Requisitos, a suíte fecha em **exit 3** para esse critério.
- Homologação **não está provisionada** (não existe container `pg-homolog`): `suite_banco.sh homolog` sai
  com exit 1 e aponta o alvo inexistente — falha visível, nunca silenciosa.
- A etapa 0 acusa quando a migration registrada pelo runner **diverge** do arquivo do repo. Hoje isso
  acontece em dev (arquivo `0484a370…` × registro `bc766a81…`): divergência aberta como defeito no board.
