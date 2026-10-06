> **STATUS (06/10/2026): artefatos versionados, ambiente NAO provisionado.** Nenhum container, volume
> ou rede de producao existe na VPS: `/opt/tre/prod` segue esqueleto vazio. Este runbook descreve o que
> os artefatos fazem e onde estao as travas que exigem decisao do dono — nao afirma nada em execucao.

# Runbook — n8n no ambiente PRODUCAO

Container `n8n-prod`, imagem `n8nio/n8n` **pinada por digest** (mesma versao de dev e homolog:
`2.41.5` = `sha256:6f532d3b…`, nao `latest`).

## Valores deste ambiente

```
porta ............. 127.0.0.1:5682 -> 5678/tcp  (dev 5680, homolog 5681, producao 5682)
segredo ........... /etc/tre/n8n-prod/n8n.env (600, root; chave de criptografia nasce NA VPS)
rede interna ...... tre-odoo-prod
UI ................ NAO exposta na borda (a borda serve os nomes do Odoo)
```

## Artefatos

`deploy/compose/prod/n8n.yml`, `deploy/environments/prod-n8n.env`,
`scripts/provision/instalar-n8n-prod.sh` e `verificar-n8n-prod.sh`.

Licao que ja custou duas vezes (gravada nos dois scripts): o **ID da credencial vem do contrato**
(`tre-dev-postgres`, `tre-dev-api-controlada`, `tre-dev-ingest-token`) — criar credencial com id de
ambiente faz o webhook responder **500**. O nome da credencial e' livre e deve dizer a verdade sobre o
ambiente. E ativacao de workflow nesta versao e' `publish:workflow --id=<id>`, sempre com restart:
workflow nao publicado = webhook respondendo **404**.

`TRE_API_BASE=http://odoo-prod:8069` — nenhum host fica escrito no artefato do workflow.

## Ordem (quando autorizado)

1. rede + segredo; 2. `instalar-n8n-prod.sh`; 3. `verificar-n8n-prod.sh` (44 itens + dentes em dev e
homolog; em producao ainda nao medido); 4. importar credenciais/workflows **so' depois** de a API de
producao existir; 5. publicar os workflows e ligar as agendas.

Portao de escrita em producao: a politica do modulo declara `ambientes_permitidos` e `producao` exige
**aprovacao humana** (`AMBIENTES_COM_APROVACAO`). Producao **nao** tem politica nem aprovacao hoje:
escrita em producao esta fechada por desenho — o que faz o ciclo E2E de escrita rodar so' em dev e
homolog (este ultimo com a aprovacao de 15/10/2026).

## Credenciais, workflows e ativação (medido em 06/10/2026)

- **O bind do cofre é `<home> → /home/node`** (não `/home/node/.n8n`): os arquivos a importar vão em
  `/opt/tre/prod/n8n/home/.n8n/`. Gravar em `.../home/` direto dá `ENOENT` no container.
- **Credenciais** (ids do contrato, nomes honestos de produção): `tre-dev-postgres` →
  `pg-sales-prod`/`sales_intelligence`/`sales_ai`; `tre-dev-api-controlada` → `Bearer` da chave de API
  do Odoo de produção; `tre-dev-ingest-token` → header `X-Tre-Ingest-Token`. O `credenciais.json` é
  montado **na VPS**, lendo `/etc/tre/prod-sales/pg.env`, `/etc/tre/odoo-prod/chave-api.txt` e
  `/etc/tre/odoo-prod/ingest-token` (600) — nenhum valor passa por stdout, log, chat ou argv.
- **Ativação exige o serviço PARADO.** `publish:workflow --id=<id>` com o n8n rodando **não** tem efeito
  (o próprio CLI avisa: *"Changes will not take effect if n8n is running"*). Ordem que funciona:
  `stop` → `publish` (num `docker run` efêmero montando o mesmo cofre) → `start`. Depois disso o webhook
  responde **403 sem token válido**.
- Os arquivos com segredo são removidos do cofre logo após o `import` (a credencial fica só no
  `database.sqlite` cifrado).
- `docker exec` roda como o usuário do container (`node`/`odoo`): arquivo copiado com `docker cp` fica
  `root:root 600` e **não é legível** — dar `chown` do usuário do serviço na cópia temporária, ou ler
  dentro do processo (regra de segredo do projeto).
