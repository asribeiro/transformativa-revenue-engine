# Runbook — massa mínima de smoke do Data Contract (dev)

**Card que criou isto:** `TRE-W1-E02-T01` (W1 · E02 · "Criar tabelas core").
**Massa:** `db/fixtures/smoke_dev.sql` (já existia, do `TRE-W0-E01-T03`) — **não** é dado de negócio:
existe para exercitar as 12 tabelas com contagem determinística.
**Regra que manda:** ADR-005 — nada nasce em produção. A massa nasce em **dev** (e, quando for o caso,
homologação); `prod` é recusado pelo script.

## 1. Por que existe este runbook

O card `TRE-W1-E02-T01` tem três critérios homologados: (1) as 12 tabelas existem com colunas/PK/FK/NOT NULL
conforme o contrato; (2) o fixture carrega sem erro em dev; (3) **a contagem por tabela confere com o esperado
do fixture**. Os critérios (1) e (2) já tinham ferramenta (`verificar_contrato_dados.py --banco`, runner de
migração). O critério (3) não tinha: este runbook e os dois scripts abaixo existem para ele — e o script de
teste existe para provar que a conferência **não é carimbo**.

## 2. Onde roda

Na **VPS do ambiente** (ADR-0008): o Hermes não tem rota de rede até o banco do TRE nem cliente `psql`.
O fixture entra no container por `docker cp` + `psql -f` (sem stdin — um `docker exec -i` comeria o stdin de
quem orquestra por SSH; aprendizado do `TRE-W1-E01-T01`).

## 3. Aplicar em dev (aplica + confere no mesmo passo)

```bash
# da VPS do TRE
cd /opt/tre/repo
bash scripts/db/aplicar_fixture_smoke.sh dev
```

Fecha com `RESULTADO: FIXTURE_APLICADO_OK (N itens, 0 falhas)`, exit 0. O script:

1. recusa `prod` (ADR-005) e ambiente desconhecido;
2. resolve o par ambiente → container/usuário/banco por `deploy/environments/<ambiente>.env`, com
   **precedência para a variável do operador** (`TRE_PG_SERVICO`, `TRE_PG_USER`, `TRE_PG_DB`);
3. confere que o container existe e que o PostgreSQL responde **duas vezes** (`SELECT 1` com intervalo — a
   imagem oficial sobe um servidor temporário na inicialização e `pg_isready` mente nesse momento);
4. aplica o fixture com `psql -v ON_ERROR_STOP=1` (erro de coluna/violação de contrato **falha**, não passa);
5. remove a cópia temporária de dentro do container;
6. chama o verificador de contagem (seção 4) e propaga o exit code.

O fixture é **idempotente** (`ON CONFLICT DO NOTHING`): rodar de novo não duplica massa.

## 4. Conferir a contagem por tabela (só leitura no alvo)

```bash
bash scripts/db/verificar_fixture_smoke.sh 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'
```

Como o **esperado** é obtido (e por que ele não envelhece): o script sobe um PostgreSQL **descartável**
(`postgres:16`), aplica a migration + o fixture e conta as 12 tabelas ali dentro; essa é a linha de base.
Depois conta as mesmas 12 tabelas no alvo e compara **tabela a tabela**, mais o total. Se o fixture mudar, o
esperado muda junto na mesma execução — não existe constante escrita à mão para desatualizar.
Fecha com `RESULTADO: FIXTURE_OK (20 itens, 0 falhas)` (ou `FIXTURE_FALHOU`, exit 1).

Contagem esperada do fixture (medida, 30/09/2026):

| tabela | linhas |
|---|---|
| organizations | 2 |
| contacts | 1 |
| signals | 1 |
| research_runs | 1 |
| pain_hypotheses | 1 |
| scores | 2 |
| interactions | 1 |
| recommendations | 1 |
| agent_runs | 1 |
| outbox_events | 1 |
| sync_events | 1 |
| human_approvals | 1 |
| **total** | **14** |

## 5. Provar que a conferência tem dente (teste negativo)

```bash
bash scripts/db/teste-fixture-smoke.sh
```

Roda tudo em container **descartável** (não toca ambiente real): migration + fixture + conferência → aprova;
**linha a mais** no alvo → reprova; **linha a menos** → reprova; reaplicar o fixture desfaz a remoção e a
contagem volta a aprovar; o rollback da massa zera as 12 tabelas e reaplicar o fixture traz as 14 de volta.
Fecha com `RESULTADO: TESTE_OK (12 itens, 0 falhas)`. Sem a parte negativa, o verificador seria carimbo.

## 6. Rollback

**Só a massa de smoke** (o schema fica) — SQL versionado em **`db/fixtures/smoke_dev_rollback.sql`**
(filhas antes das pais, senão as FKs barram; IDs determinísticos do fixture):

```bash
docker cp db/fixtures/smoke_dev_rollback.sql pg-sales-dev:/tmp/rollback_massa.sql
docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -f /tmp/rollback_massa.sql
docker exec pg-sales-dev rm -f /tmp/rollback_massa.sql
```

Exercitado de verdade (não só escrito): `teste-fixture-smoke.sh` aplica migration + fixture, roda o rollback
→ **0 linhas nas 12 tabelas** — e reaplica o fixture → **14 linhas de volta**. O rollback é reversível e o
alvo de dev segue com a massa carregada, como o critério de aceite pede.

**Rollback total da W1 em dev** (schema + rastro da migration): `docs/runbooks/aplicar-migracoes.md`, seção 5
(`DROP SCHEMA … CASCADE` + `DELETE FROM public.tre_schema_migrations WHERE versao='0001'` + reaplicação).
Ambientes superiores não são tocados por este rollback.

## 7. Evidência medida — 30/09/2026 (dev, VPS Contabo `vmi3619453`)

- `verificar_contrato_dados.py --banco …` → `RESULTADO: PASS (37 itens, 0 falhas)`, exit 0 (colunas, tipos,
  NOT NULL, PK, FK, vínculos sem FK e os 30 índices conferidos contra o contrato, no banco real).
- `aplicar_fixture_smoke.sh dev` → `FIXTURE_OK (20 itens)` + `FIXTURE_APLICADO_OK (7 itens)`, exit 0; 14 linhas
  nas 12 tabelas, contagem por tabela igual à linha de base do fixture.
- Idempotência: segunda aplicação com o schema já em 14 linhas → segue 14 (nada duplicado).
- `teste-fixture-smoke.sh` → `TESTE_OK (12 itens, 0 falhas)`: linha a mais e linha a menos **reprovam**, o
  rollback zera as 12 tabelas e a reaplicação do fixture restaura as 14 linhas.
- `aplicar_fixture_smoke.sh prod` → `FALHOU ADR-005: massa de smoke nao nasce em producao`, exit 1;
  `docker ps -a` só tem `pg-sales-dev`; `/opt/tre/{prod,homolog}` sem nenhum arquivo.

## 8. Achados de execução (corrigidos no mesmo card, por teste e não por leitura)

1. O padrão que confere "toda tabela tem INSERT no fixture" exigia o `(` na mesma linha do nome da tabela;
   como o fixture quebra linha antes das colunas, **as 12 tabelas foram reportadas como ausentes** (o
   verificador reprovou na primeira execução — o que provou que o item existe de verdade). Corrigido para
   casar o nome com fronteira (`organizations` ≠ `organizations_x`).
2. O teste negativo reaplicava o fixture só depois da mutação de "linha a mais"; a de "linha a menos" deixava
   o alvo mutado e a reverificação final reprovava por isso. Corrigido: o fixture é reaplicado antes da
   reverificação — e isso virou a prova de que reaplicar a massa restaura a contagem.
