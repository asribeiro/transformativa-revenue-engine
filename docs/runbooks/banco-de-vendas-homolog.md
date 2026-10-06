# Runbook — banco de VENDAS (`sales_intelligence`) no ambiente HOMOLOG

Serviço `pg-sales-homolog`: o banco que sustenta o ciclo E2E de Homolog — fila (`outbox_events`),
trilha (`sync_events`) e as tabelas do contrato, criadas por `scripts/db/aplicar_migracoes.sh homolog`.

## 1. O que é e de onde vem

- Artefatos versionados: `deploy/compose/homolog/pg-sales.yml`, par não-secreto
  `deploy/environments/homolog-sales.env`, instalador e verificador em `scripts/provision/`.
- Imagem **PostgreSQL 16** no **mesmo digest do banco de vendas do dev**
  (`sha256:1a6ab3f5…`): ambiente que roda imagem diferente não homologa nada.
- Volume próprio `pgdata-sales-homolog`; **nenhuma porta publicada** — o banco fala só pela rede
  interna `tre-odoo-homolog`, onde vivem `odoo-homolog`, `n8n-homolog` e ele.
- Segredo em `/etc/tre/homolog-sales/pg.env` (600, root, na VPS): usuário `sales_ai`, banco
  `sales_intelligence` e senha gerada **na VPS**. O instalador **não reescreve** senha de cluster já
  inicializado — reescrever deixa o banco inacessível.

O par `deploy/environments/homolog.env` carrega o trio `TRE_PG_SERVICO/USER/DB` apontando para
**este** container (nunca para o do dev): é o que o runner de migração e o backup leem.

## 2. Instalar / subir

```bash
bash scripts/provision/instalar-pg-sales-homolog.sh --ensaio   # não escreve nada
bash scripts/provision/instalar-pg-sales-homolog.sh            # idempotente
```

Ordem obrigatória (armadilha medida duas vezes neste projeto): o **segredo primeiro**, a validação
do compose depois — `docker compose config` resolve o `env_file` e falha com "env file not found".

## 3. Verificar (aceite)

```bash
bash scripts/provision/verificar-pg-sales-homolog.sh                  # 20 itens
bash scripts/provision/verificar-pg-sales-homolog.sh --prova-de-dente # + dente
```

Cobre: par declarando imagem/digest; container e healthcheck; tag **e** digest; id da imagem que o
container roda; ausência de porta publicada; volume próprio e não alheio; rede só a do ambiente sem
container de outro ambiente; banco e dono (`sales_ai`); cluster de vendas sem banco do Odoo;
alcançável por `odoo-homolog` e `n8n-homolog`; e nenhum segredo **atribuído** no artefato.

## 4. Migração

```bash
cd /opt/tre/homolog/repo && bash scripts/db/aplicar_migracoes.sh homolog --somente-checar
cd /opt/tre/homolog/repo && bash scripts/db/aplicar_migracoes.sh homolog
```

A versão `0001` aplicada em Homolog tem o **mesmo sha256 do dev** (`0484a370…`) — paridade de schema
é o que permite comparar os dois ambientes.

## 5. O ciclo E2E em Homolog (medido em 06/10/2026)

| sentido | estado |
|---|---|
| CRM → outbox → n8n → PostgreSQL | **funciona e é autônomo**: fatos pelo ORM → `SENT: 8, DEAD_LETTER: 0` → 8 linhas `COMPLETED` na trilha; cron do módulo ativo (1 min) entregou uma mudança de etapa sozinho |
| PostgreSQL → n8n → API controlada → CRM | **recusado no portão, com motivo nomeado** (`recusa_da_api:ambiente_nao_permitido`) |

A recusa **não é defeito**: a política do módulo (`api/politica_api.json`) declara
`ambientes_permitidos: ["dev"]` e o motor trata `homologacao`/`producao` como ambientes que exigem
**aprovação humana** (`AMBIENTES_COM_APROVACAO` em `api/motor.py`). Escrita em Homolog está trancada
por desenho — destrancar exige duas coisas, ambas decisão do dono: uma política que permita
`homologacao` e uma aprovação válida em `tf.api.aprovacao`.

Parâmetros corretos deste ambiente (o nome canônico é **`homologacao`**, não `homolog`):

```
tf.api.ambiente  = homologacao
tf.api.aprovacao = (vazio — nenhuma aprovação registrada)
tf.api.politica  = (vazio — usa a política do módulo, que só permite dev)
```

## 6. Armadilhas medidas (não repetir)

1. **Credencial do n8n: o ID vem do contrato, não do ambiente.** Os workflows versionados referenciam
   `tre-dev-postgres`, `tre-dev-api-controlada` e `tre-dev-ingest-token` (ids declarados em
   `n8n/contracts/*.json`). Importar credenciais com outro id (`tre-homolog-*`) faz o webhook
   responder **500** e o nó de banco falhar por credencial inexistente. O **nome** da credencial é
   livre (e deve dizer a verdade sobre o ambiente); o **id** tem de ser o do contrato.
2. **Ativação de workflow no n8n 2.41 é `publish:workflow --id=<id>`**, não
   `update:workflow --active=true` (que responde "Please use: publish:workflow"). Nos dois casos,
   reinicie o serviço: as mudanças só valem no processo novo. Sintoma de workflow não publicado:
   o webhook responde **404**.
3. **Nome do ambiente é vocabulário fechado:** `dev`, `homologacao`, `producao`. `homolog` cai em
   `ambiente_nao_declarado` — a API recusa TUDO, inclusive leitura.
4. **`psql` sem `-d` tenta o banco com o nome do usuário** (`sales_ai`) e morre com
   "database does not exist" — todo `psql` aqui aponta o banco explicitamente.
5. **Falso positivo de porta publicada:** `.NetworkSettings.Ports` mostra a porta `EXPOSE` do
   Dockerfile como `{"5432/tcp":null}` mesmo sem publicação. O que prova publicação é
   `.HostConfig.PortBindings`.
6. **`set -euo pipefail` + `grep` sem casamento = script morto em silêncio.** Um verificador meu
   morreu no meio e não imprimiu nem o resultado; a contagem por pipeline leva `|| true`.
7. **Sem CLI para apagar credencial** nesta versão: limpeza de credencial criada por engano foi feita
   com o serviço parado e `sqlite3` sobre `~/.n8n/database.sqlite` (com backup antes).
