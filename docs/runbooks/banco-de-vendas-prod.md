> **STATUS (06/10/2026): artefatos versionados, ambiente NAO provisionado.** Nenhum container, volume
> ou rede de producao existe na VPS: `/opt/tre/prod` segue esqueleto vazio. Este runbook descreve o que
> os artefatos fazem e onde estao as travas que exigem decisao do dono — nao afirma nada em execucao.

# Runbook — banco de VENDAS (`sales_intelligence`) no ambiente PRODUCAO

Servico `pg-sales-prod`. E' o banco que sustenta o ciclo E2E de producao: fila (`outbox_events`),
trilha (`sync_events`) e as tabelas do contrato.

## Artefatos

- `deploy/compose/prod/pg-sales.yml` + par nao-secreto `deploy/environments/prod-sales.env`.
- `scripts/provision/instalar-pg-sales-prod.sh` e `verificar-pg-sales-prod.sh` (derivados de homolog
  com a inversao de isolamento revisada: as listas recusam volumes de **dev e homolog**, nunca de si).
- **PostgreSQL 16 no mesmo digest de dev e homolog** — ambiente que roda imagem diferente nao homologa.
- Volume proprio `pgdata-sales-prod`; **sem porta publicada**; rede `tre-odoo-prod`.
- Segredo em `/etc/tre/prod-sales/pg.env` (600, root, gerado NA VPS; o instalador nao reescreve senha
  de cluster ja inicializado).

## A trava deste ambiente (nao e' detalhe)

```bash
cd /opt/tre/prod/repo && bash scripts/db/aplicar_migracoes.sh prod --somente-checar
```

O runner **recusa `prod` por padrao** (ADR-005, nenhuma DDL nasce em producao). Para aplicar exige:
(i) a sequencia ja registrada em homolog e (ii) `TRE_APROVACAO_HUMANA=<caminho do registro>`. Ou seja:
schema de producao **nao nasce de execucao automatica** — nasce de aprovacao registrada. Nada aqui
foi aplicado ainda.

## Ordem de provisionamento (quando autorizado)

1. rede `tre-odoo-prod` e segredo do cluster;
2. `instalar-pg-sales-prod.sh` (idempotente; `--ensaio` nao escreve nada);
3. `verificar-pg-sales-prod.sh` (aceite com prova de dente);
4. migracao — **parada obrigatoria no portao acima** (decisao do dono).

Receita detalhada, armadilhas e o ciclo E2E: `docs/runbooks/banco-de-vendas-homolog.md` (o texto de
producao muda so' no nome dos recursos e no portao da migracao).
