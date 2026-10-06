> **STATUS (06/10/2026): artefatos versionados, ambiente NAO provisionado.** Nenhum container, volume
> ou rede de producao existe na VPS: `/opt/tre/prod` segue esqueleto vazio. Este runbook descreve o que
> os artefatos fazem e onde estao as travas que exigem decisao do dono — nao afirma nada em execucao.

# Runbook — Odoo no ambiente PRODUCAO

Container `odoo-prod`, Odoo Community 19.0 no **mesmo digest** que dev e homolog rodam
(`sha256:77bac5cd…`).

## Valores deste ambiente

```
porta local ....... 127.0.0.1:8071 -> 8069/tcp   (dev 8069, homolog 8070, producao 8071)
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
(`odoo_dev`/`odoo_homolog`) e que ele publica apenas em loopback na porta 8071.

## Ordem (quando autorizado)

1. rede + segredos; 2. `instalar-odoo-prod.sh`; 3. `verificar-odoo-prod.sh`;
4. **migracao pelo portao** (`aplicar_migracoes.sh prod` exige `TRE_APROVACAO_HUMANA` — ADR-005);
5. copia publicada do branch **main**; 6. ligar na borda.

Detalhe de operacao e armadilhas: `docs/runbooks/odoo-homolog.md` e `docs/runbooks/odoo-dev.md`.

## Porta e borda (medido em 06/10/2026)

- **A porta do Odoo de produção é `8071`** — a sequência declarada no desenho
  (`deploy/environments/edge-proxy.env`: 8069 dev, 8070 homolog, **8071 produção**). A derivação dos
  artefatos tinha publicado `8080`, e a borda continuava apontando para `8071`: com a stack no ar, o nome
  público devolvia **503** e parecia stack ausente. Tudo alinhado em `8071` (par + 6 scripts + runbook).
- **A borda servia `tre` como placeholder de propósito** (`respond "PRODUCAO ainda nao provisionada." 503`).
  Com a produção no ar, o bloco virou `reverse_proxy 127.0.0.1:{$TRE_PORTA_ODOO_PROD}`, com os mesmos
  cabeçalhos e log dos demais. A borda roda com **`admin off`**: aplicar mudança **exige restart** do
  container (`docker restart proxy-edge`), não `caddy reload`.
- **Medições finais:** `https://tre.transformativa.com.br/web/login` → **200** (Odoo servido pela borda),
  `dev.tre` → 401, `homolog.tre` → 401, qualquer outro Host → 404. Certificados Let's Encrypt válidos.
