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

## Migração aplicada (06/10/2026)

- **Portão (ADR-005) satisfeito e medido:** arquivo de aprovação existente (`docs/operations/registro-de-aprovacoes.md`,
  **Autorização 5**), container de Homolog presente (`pg-sales-homolog`) e **toda** versão pendente já registrada em
  Homolog. `--somente-checar`: `MIGRACAO_OK (8 itens, 0 falhas)`. Apply: `MIGRACAO_OK (aplicar; 1 aplicada, 0 falhas, 9 itens)`.
- **Rastro:** `public.tre_schema_migrations` = `0001 | 0001_sales_intelligence_v1.sql | 0484a3701b8c8524… | 2026-10-06`
  — **mesmo sha** de dev e homolog.
- **Paridade:** 12 tabelas no schema `sales_intelligence`; lista de tabelas **idêntica** à de Homolog (diff vazio).
- **Comando (na cópia de produção na VPS):**
  `TRE_APROVACAO_HUMANA=/opt/tre/prod/repo/docs/operations/registro-de-aprovacoes.md bash scripts/db/aplicar_migracoes.sh prod`
- **Dois defeitos do runner consertados na raiz** (ambos medidos aqui, nenhum afrouxou o portão):
  1. a leitura do rastro de Homolog descartava o `stderr` (`2>/dev/null`) — leitura que **falhava** virava
     "versão ainda não registrada em homolog" (recusa certa, **causa falsa**). Agora o `stderr` é guardado, há
     prova de leitura (`SELECT 1`) antes do laço e o aborto traz a **causa real**;
  2. os defaults do alvo de Homolog eram `pg-homolog` / `tre`, que **nunca existiram** nesta VPS — a conferência
     reprovava por não conseguir ler. Agora são `pg-sales-homolog` / `sales_ai`, o layout real.

## O ciclo ponta a ponta em Produção (medido em 06/10/2026)

| etapa | medida |
|---|---|
| Odoo/cópia/módulo | `odoo-prod` em `127.0.0.1:8071` (sequência do desenho), `transformativa_sales_ai` **installed** (`tf_evento_outbox`, `tf_process_opportunity`), política `politica_producao.json` visível no container |
| parâmetros | `tf.api.ambiente=producao`, `tf.api.politica=…/politica_producao.json`, `tf.api.aprovacao=card=t_ba84b412,aprovador=Anderson Ribeiro,validade=2026-10-13`, `transformativa_sales_ai.ingest_url=http://n8n-prod:5678` + `ingest_token` (64 bytes, gravado de arquivo 600) |
| agenda do Odoo | `ir_cron` **18** (`Sales AI: entregar eventos Odoo -> PostgreSQL`) **ativa**, 1 min |
| **direção 1** (Odoo → n8n → PostgreSQL) | `TF_RESUMO {"SENT": 8, "DEAD_LETTER": 0, "RETRY": 0, "duplicados_no_destino": 0, "erro": false}` → 8 linhas `COMPLETED` na trilha (`odoo → postgres`) |
| **direção 2** (fila → consumidor → API → CRM) | evento `COMPANY_QUALIFIED` novo → fila `PROCESSED` (1 tentativa) → trilha `outbox:<id>:COMPANY_QUALIFIED | COMPLETED | UPSERT | postgres → odoo` → parceiro **id 8 "Ciclo E2E Prod Ltda"** (score 77) criado **pela API** |
| **replay** | mesmo evento reenfileirado → `PROCESSED` com `attempts` **inalterado (1)** e **sem** segunda linha de trilha nem segundo parceiro |
| **dente do portão** | aprovação vencida (05/10) + evento novo → fila **`DEAD_LETTER`** com `recusa_da_api:aprovacao_ausente`, trilha **`REFUSED`**, **zero** escrita no CRM (9 parceiros antes e depois) |
| **par antes/depois** | restaurada a validade (13/10), o **mesmo** evento saiu `PROCESSED` (2 tentativas) e o parceiro **id 11 "Dente real do portao Prod Ltda"** (score 11) nasceu no CRM — só depois da aprovação voltar |

### Armadilha medida: o parâmetro é CACHEADO (e o dente dá falso passe sem restart)

A gravação de `tf.api.aprovacao` **pelo ORM** (`set_param`) não basta para o Odoo **em execução**: sem
**reiniciar o serviço**, a API continua servindo a validade antiga. Medido aqui: a primeira tentativa do
dente criou o parceiro **id 9 "Dente do portao Prod Ltda"** com a aprovação vencida já gravada no banco —
o portão parecia aberto por defeito, e era só cache. **Regra:** mudou aprovação/política ⇒ `restart` do
Odoo **antes** de concluir qualquer medição do portão. Depois do restart, a sonda direta confirmou o
fechamento: `HTTP 503`, `codigo=aprovacao_ausente`, `ambiente=producao`, CRM intacto.

Sonda reutilizável: `/tmp/sonda-portao-prod.sh` (chave lida do arquivo 600 e entregue ao curl por
**arquivo de configuração**, nunca por argv; imprime só status HTTP e `codigo`).

### Limpeza da massa de aceite no CRM (06/10/2026, opção A do dono)

A massa de aceite **cria registros de verdade no CRM de produção** (o gerador de fatos usa o ORM e a API
escreve parceiros). Antes de qualquer demonstração, prever o par **dump + limpeza**:

1. **Dump primeiro** (é o que torna a limpeza reversível): `docker exec pg-odoo-prod pg_dump -U odoo -Fc
   odoo_prod > /opt/tre/backups/odoo_prod-<UTC>.dump` (+ o banco de vendas, que guarda a trilha). Modo 600.
2. **Conferir se o módulo reage a exclusão** antes de apagar: se houvesse detector de `unlink`, o evento novo
   chegaria à fila e a API **recriaria** o parceiro. Aqui **não** há (só `@api.model_create_multi` em
   `mail_activity`/`calendar_event`/`tf_process_opportunity`).
3. **Apagar pelo ORM (`unlink`), nunca por SQL**: o Odoo limpa mensagens, seguidores e atividades ligados ao
   parceiro. Sobraram os ids de sistema `[1, 3, 6]`.
4. **Medir depois**: fila inalterada (3 `PROCESSED`), trilha inalterada (11 `COMPLETED` — é a evidência que
   fica), `tre` → 200.
5. **Resíduo conhecido:** o `crm.lead` do aceite fica órfão (`partner_id = 0`) e precisa de decisão própria.

Backups resultantes: `odoo_prod-20261006T142318Z.dump` (sha256 `2236a5d4…`) e
`sales_intelligence-20261006T142318Z.dump` (sha256 `ab90cbd3…`), em `/opt/tre/backups` (700, arquivos 600).
