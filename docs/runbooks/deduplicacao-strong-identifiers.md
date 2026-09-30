# Runbook — deduplicação de empresa por identificadores fortes (TRE-W1-E04-T01)

**Card:** `TRE-W1-E04-T01` · **Contrato:** Data Contract V1.0, seção 5 (`docs/data/DATA_CONTRACT_V1.md`) ·
**Motor:** `scripts/dedup/deduplicar_organizacoes.py` · **Ambiente:** dev (o motor recusa `prod` — ADR-005)

## 1. A regra que este motor implementa (não inventada aqui)

| Regra do contrato | Onde está no código |
|---|---|
| Fortes: **CNPJ → domínio → LinkedIn Company URL** (nessa prioridade) | `avaliar_par()` |
| Fracos: nome + cidade; nome + telefone; nome + endereço | `avaliar_par()` (só `nome + cidade` é implementável na V1 — ver D4) |
| Merge automático **somente** com confiança ≥ `auto_merge_threshold` (0,95) | `decidir_por_confianca()` + `gerar_sql_merge()` (recusa merge fora disso) |
| Abaixo do limiar: `REVIEW_REQUIRED`, fila humana, **nunca** merge silencioso | `gerar_sql_revisao()` / `executar_revisao()` → `human_approvals` (PENDING) |
| Ambiguidade não é resolvida por heurística: é reportada | `SEM_DUPLICIDADE` para par sem candidatura; conflito vai para revisão |

### Decisões de implementação (registradas no card; não mudam o contrato)

- **D1 — o limiar vem do contrato.** `limiar_merge()` lê `docs/data/data_contract_v1.json`
  (`dedup.auto_merge_threshold`) **a cada chamada**. Não existe parâmetro, constante ajustável nem
  variável de ambiente que mude o valor: mudar o limiar exige mudar o contrato (governança da seção 10).
  Isso é provado por teste (`governanca:` na suíte sintética).
- **D2 — evidência fraca nunca mergeia.** A confiança de um par só-fraco é limitada a `limiar − 0,01`
  (hoje **0,94**) e vai para a fila humana. Só identificador forte chega à faixa de merge.
- **D3 — identificador forte inválido.** CNPJ igual porém sem dígito verificador válido **detecta** a
  duplicidade e manda para revisão; nunca mergeia automático (`cnpj_invalido` na evidência).
- **D4 — lacuna declarada.** `organizations` não tem coluna de telefone nem de endereço na V1: os fracos
  "nome + telefone" e "nome + endereço" do contrato **não** são implementáveis hoje. Não inventamos
  coluna; fica registrado para o W1 decidir.
- **D5 — auditoria e fila sem schema novo.** Merge → `sync_events` (`operation=MERGE`, `entity_type=organization`,
  `idempotency_key` determinística, payload com a evidência completa). Revisão → `human_approvals`
  (`action_type=ORGANIZATION_MERGE_REVIEW`, `status=PENDING`). Criar coluna/tabela exigiria nova versão
  do contrato, então não criamos.
- **D6 — rollback executável.** A auditoria guarda sobrevivente, duplicado e evidência; `--desfazer-merge`
  devolve os vínculos, reativa o duplicado e grava um registro `UNMERGE`.

## 2. Uso (na VPS do ambiente — ADR-0008)

```bash
# limiar em vigor e a origem dele (não toca o banco)
python3 scripts/dedup/deduplicar_organizacoes.py --limiar

# suíte sintética: 0,94/0,95, cada identificador forte isolado e em conjunto, negativos,
# auditoria e governança do limiar (sem banco)
python3 scripts/dedup/deduplicar_organizacoes.py --autoteste

# prova negativa: sabota o alvo de propósito; a suíte TEM de reprovar (exit != 0)
python3 scripts/dedup/deduplicar_organizacoes.py --autoteste --sabotar limiar   # auditoria | identificadores | fraco

# varredura somente-leitura das organizações do ambiente
python3 scripts/dedup/deduplicar_organizacoes.py --detectar --ambiente dev

# cenário completo no ambiente (semeia massa marcada, mede tudo, limpa e confere o estado)
bash scripts/dedup/teste_dedup_ambiente.sh dev

# operações pontuais
python3 scripts/dedup/deduplicar_organizacoes.py --mesclar <SOBREVIVENTE_UUID> <DUPLICADO_UUID> --ambiente dev
python3 scripts/dedup/deduplicar_organizacoes.py --desfazer-merge <idempotency_key> --ambiente dev
```

Prova em um comando (o que o card exige): `bash scripts/dedup/teste_dedup_sintetico.sh` e
`bash scripts/dedup/teste_dedup_ambiente.sh dev`. Ambos imprimem `RESULTADO: ...` e devolvem exit code
confiável — suíte verde é exit 0 com itens efetivamente executados.

## 3. O que o merge faz, em uma transação

1. `UPDATE <tabela filha> SET organization_id = <sobrevivente> WHERE organization_id = <duplicado>`
   para **todas** as colunas que apontam para `organizations` — a lista é lida do próprio schema
   (`information_schema`), não fixada à mão;
2. `UPDATE organizations SET deleted_at = NOW()` no duplicado (**soft delete** — a linha não some);
3. `INSERT INTO sync_events (… operation='MERGE' …)` com confiança, identificadores decisivos, limiar
   vigente, política, hash do contrato e quem executou;
4. `COMMIT`. Merge fora do limiar é **recusado antes de gerar SQL** (`gerar_sql_merge` levanta erro).

Merge repetido é idempotente: a `idempotency_key` é `org-merge:<sobrevivente>:<duplicado>` (UNIQUE em
`sync_events`) e o motor recusa o segundo.

## 4. Como conferir depois (auditoria)

```sql
-- auditoria do merge: quem, quando, com que evidência e sob qual limiar
SELECT created_at, operation, source_version, idempotency_key, request_payload
FROM sales_intelligence.sync_events
WHERE operation = 'MERGE' AND entity_type = 'organization'
ORDER BY created_at DESC;

-- fila humana (o que ficou abaixo do limiar, sem merge)
SELECT requested_at, entity_id, proposed_action
FROM sales_intelligence.human_approvals
WHERE action_type = 'ORGANIZATION_MERGE_REVIEW' AND status = 'PENDING';
```

## 5. Rollback

- **Código:** reverter o commit do card (`git revert <sha>`); nada no schema mudou, então não há DDL a
  desfazer.
- **Dado já mesclado:** `--desfazer-merge <idempotency_key>` lê a auditoria, devolve os vínculos ao
  duplicado, limpa o `deleted_at` e registra `UNMERGE` — o motivo de o merge ser auditável. O par
  sobrevivente/duplicado sai do próprio registro, não da memória de quem opera.
- **Massa do cenário de teste:** `teste_dedup_ambiente.sh` limpa a massa marcada (`cenario=tre-w1-e04-t01`)
  e confere a contagem por tabela contra o estado anterior. A limpeza filtra pelo marcador do cenário —
  nunca por `idempotency_key LIKE`, para não apagar auditoria real.

## 6. Limites declarados

- O motor **não** decide o que fazer com as linhas filhas de negócio (score, sinal, recomendação) além de
  reapontá-las: nenhum dado é recalculado no merge.
- Propagação para o Odoo (evento de outbox) é do W2 — aqui o merge é só do lado PostgreSQL.
- `entity_match_confidence` como campo persistido é o card `TRE-W1-E04-T02`; este motor calcula a confiança
  em memória e a grava na evidência da auditoria.
