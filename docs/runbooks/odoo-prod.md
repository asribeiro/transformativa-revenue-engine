> **STATUS (06/10/2026): artefatos versionados, ambiente NAO provisionado.** Nenhum container, volume
> ou rede de producao existe na VPS: `/opt/tre/prod` segue esqueleto vazio. Este runbook descreve o que
> os artefatos fazem e onde estao as travas que exigem decisao do dono — nao afirma nada em execucao.

# Runbook — Odoo no ambiente PRODUCAO

Container `odoo-prod`, Odoo Community 19.0 no **mesmo digest** que dev e homolog rodam
(`sha256:77bac5cd…`).

## Valores deste ambiente

```
porta local ....... 127.0.0.1:8080 -> 8069/tcp   (dev 8069, homolog 8070, producao 8080)
banco ............. odoo_prod                    (cluster pg-odoo-prod, volume pgdata-odoo-prod)
filestore ......... odoo-data-prod
rede interna ...... tre-odoo-prod
borda ............. tre.transformativa.com.br    (borda unica; hoje responde 503)
codigo ............ /opt/tre/prod/repo (branch main, copia publicada por deploy/publicar.sh)
segredos .......... /etc/tre/odoo-prod/ (600, root)
```

## Artefatos

`deploy/compose/prod/odoo.yml`, `deploy/environments/prod-odoo.env` (+ `prod.env`),
`scripts/provision/instalar-odoo-prod.sh` e `verificar-odoo-prod.sh`.

Guardas do instalador: ele **nao toca** em dev nem em homolog — e prova isso no COMPOSE RESOLVIDO
(`NOMES_DE_OUTROS`: `odoo-dev`, `pg-odoo-dev`, `odoo-homolog`, `pg-odoo-homolog`). O verificador confere
que a rede tem so' os containers de producao, que o cluster nao tem banco de outro ambiente
(`odoo_dev`/`odoo_homolog`) e que ele publica apenas em loopback na porta 8080.

## Ordem (quando autorizado)

1. rede + segredos; 2. `instalar-odoo-prod.sh`; 3. `verificar-odoo-prod.sh`;
4. **migracao pelo portao** (`aplicar_migracoes.sh prod` exige `TRE_APROVACAO_HUMANA` — ADR-005);
5. copia publicada do branch **main**; 6. ligar na borda.

Detalhe de operacao e armadilhas: `docs/runbooks/odoo-homolog.md` e `docs/runbooks/odoo-dev.md`.
