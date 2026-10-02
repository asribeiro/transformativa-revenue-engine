# Runbook — Next Best Action v1 (`nba-v1`)

Card **TRE-W5-E07-T01**. Componente: `hermes/scores/nba/nba.py`. Política (a tabela de decisão):
`hermes/scores/nba/politica-nba-v1.json`. Contrato do componente:
`hermes/scores/nba/next-best-action-v1.json`. Modelo e critérios:
`docs/architecture/next-best-action-v1.md`.

## 1. Onde roda

O banco vive na **VPS do ambiente** (`169.58.24.102`, Contabo): o container do Hermes não alcança
PostgreSQL nem tem `psql` (ADR-0008). A porta de banco é passada em `--prefixo` — a mesma forma dos
cards irmãos:

```
python3 hermes/scores/nba/nba.py --ambiente dev --organizacao <uuid> \
    --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
    --relatorio /tmp/nba-dev.json
```

`--ambiente` aceita `dev` e `homolog`. **`prod` é recusado com exit 4** (ADR-005) — nada nasce em
produção: a promoção exige card próprio com aprovação humana registrada.

## 2. Operação

| Ação | Comando |
|---|---|
| Simular (sem banco) | `python3 hermes/scores/nba/nba.py --planejar` |
| Ver a tabela de decisão | `python3 hermes/scores/nba/nba.py --regras` |
| Rodar uma empresa | `... --ambiente dev --organizacao <uuid> --prefixo "<porta>"` |
| Rodar uma lista | `... --ambiente dev --jsonl hermes/scores/nba/exemplos/empresas-exemplo.jsonl --prefixo "<porta>"` |
| Desfazer uma rodada | `... --ambiente dev --prefixo "<porta>" --desfazer <correlation_id> [--confirmo]` |

Saída: por empresa, uma linha `tier=… acao=… regra=… veredito=… gravados=…` e, no fim, o relatório
JSON (também em `--relatorio`). Vereditos: `RECOMENDADA`, `JA_RECOMENDADA` (replay), `RECUSADA`
(`SEM_TIER`, `ORGANIZACAO_NAO_ENCONTRADA`), `ABSTEVE` (`SEM_REGRA`), `ERRO`.

- A rodada **só escreve** em `sales_intelligence.recommendations` e `sales_intelligence.agent_runs`.
- A recomendação anterior `OPEN` da empresa volta para `SUPERSEDED` quando a evidência muda —
  histórico preservado.
- Falha de banco/porta devolve `ERRO` e exit 1 (nada é gravado fora da auditoria, quando a porta
  responde).

## 3. Rotina de verificação

```
python3 scripts/scores/verificar_nba.py --raiz "$PWD"              # suíte offline (69 itens)
python3 scripts/scores/verificar_nba.py --raiz "$PWD" --autoteste  # 7 mutações, cada uma pelo item esperado
bash scripts/scores/teste_nba_aceite.sh --raiz "$PWD" --prova-de-dente   # aceite E2E, na VPS
bash scripts/verificar_estrutura.sh                                # artefatos versionados
```

O aceite E2E sobe `postgres:16` descartável (`pg-nba-acc`, e `pg-nba-dente` para as mutações),
aplica a migration 0001, mede e **remove o container** no fim. Ele **aborta** se o container já
existir — nunca mexe em `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`.

## 4. Quando a rodada recusa

| Veredito/motivo | O que significa | O que fazer |
|---|---|---|
| `SEM_TIER` | a empresa não tem registro TIER (`sync_events`, `operation='TIER'`) | rodar o tiering (`TRE-W5-E06-T01`) antes: o NBA lê a prioridade, não a inventa |
| `ORGANIZACAO_NAO_ENCONTRADA` | uuid errado ou empresa removida (`deleted_at`) | conferir o uuid |
| `SEM_REGRA` | nenhuma regra da política casou com a evidência | é abstinência deliberada: revisar a política (nova versão), nunca o default |
| `POLITICA_INCOERENTE` (exit 3) | ação fora do vocabulário do contrato, ação do contrato sem regra, operador/fato desconhecido, regra sem campo | corrigir a política; o contrato é a régua |
| `ERRO` / exit 1 | porta de banco ausente ou SQL falhou | conferir o container/`--prefixo`; a mensagem real vem no stderr |

## 5. Rollback

1. **Desfazer a rodada**: `--desfazer <correlation_id>` mostra quantas recomendações seriam apagadas;
   `--confirmo` apaga **só** as daquela correlação. A auditoria em `agent_runs` permanece.
2. **Voltar o componente**: reverter o commit. Nenhuma DDL, nenhuma coluna nova — `recommendations` e
   `agent_runs` são do Data Contract V1.0.
3. **Política**: mudar limiar/ordem/prazos é **edição da política** (nova versão), nunca do código.

## 6. Segredos

A conexão do aceite é pelo container descartável, sem senha em argumento de linha de comando, arquivo
de log ou repositório. Nenhum valor de credencial neste runbook.
