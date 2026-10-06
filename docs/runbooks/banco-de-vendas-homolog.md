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

| sentido | estado medido |
|---|---|
| CRM → outbox → n8n → PostgreSQL | **funciona e é autônomo**: fatos pelo ORM → `SENT: 8, DEAD_LETTER: 0` → 8 linhas `COMPLETED` na trilha; cron do módulo ativo (1 min) entregou uma mudança de etapa sozinho |
| PostgreSQL → n8n → API controlada → CRM | **funciona sob aprovação registrada** — e **fecha sozinho** quando a aprovação vence (medido nos dois estados, abaixo) |

### 5.1 Os dois estados, medidos em 06/10/2026

**Com aprovação válida** (`validade=2026-10-15`, liberada pelo dono — registro de aprovações,
Autorização 4): `COMPANY_QUALIFIED` → fila `PROCESSED` → trilha `COMPLETED` → parceiro **id 8
"Ciclo E2E Homolog Ltda"** com `tf_company_id`, `tf_domain` e `tf_priority_score=91` em `odoo_homolog`.
Tudo **pela agenda do consumidor**, sem intervenção: evento devolvido à fila e nada mais.

**Replay do mesmo evento** (devolvido à fila de novo): `PROCESSED`, `attempts=0`, **zero** parceiro
novo — a idempotência por `tf_company_id` segura a duplicidade.

**Com aprovação vencida** (`validade=2026-10-05`) e um evento **novo**: fila `DEAD_LETTER` com
`recusa_da_api:aprovacao_ausente`, trilha `REFUSED`, e **nenhum** parceiro no CRM. É o dente do
portão: a recusa tem nome, fica registrada e não escreve nada.

**Par antes/depois com o MESMO evento do dente:** restaurada a aprovação de 15/10 (gravação pelo ORM +
restart), o evento voltou à fila e passou sozinho — fila `PROCESSED` e parceiro **id 9
"Dente do portao Homolog Ltda"** (`dente-portao.example`, score 42) criado no CRM. Só a validade mudou;
o resto do caminho é o mesmo. O parceiro 8 continua único (dedup por `tf_company_id`).

### 5.2 Parâmetros deste ambiente (o nome canônico é **`homologacao`**, não `homolog`)

```
tf.api.ambiente  = homologacao
tf.api.politica  = /mnt/extra-addons/transformativa_sales_ai/api/politica_homologacao.json
tf.api.aprovacao = card=t_e0489efc,aprovador=Anderson Ribeiro,validade=2026-10-15
```

A política de `homologacao` é **derivada e versionada** (`api/politica_homologacao.json`, variante da
1.4.0 com `ambientes_permitidos: ["homologacao"]` — privilégio mínimo; `dev` é recusado nela). A
**validade é o mecanismo de fechamento**: passada a data, `aprovacao_valida` devolve falso, a escrita
recusa com `aprovacao_ausente` e nada precisa ser desligado à mão. Para renovar, basta gravar nova
validade — e a renovação é decisão do dono, não ato de operador.

Para trocar qualquer um desses parâmetros, **reinicie o `odoo-homolog`**: o Odoo serve
`ir.config_parameter` de cache, e `update` por SQL direto não invalida o cache do servidor no ar.

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
7. **Evento devolvido à fila pode ser REPLAY, não chamada nova.** Ao reenfileirar um evento **já
   processado**, o consumidor registra o replay e **não chama a API** — então ele passa como
   `PROCESSED` mesmo com o portão fechado, e um teste de portão feito assim não mede nada. Para provar
   portão, use **evento novo** (UUID novo); para provar dedup/replay, reenfileire o mesmo.
8. **Parâmetro trocado por SQL não chega ao servidor no ar:** `ir.config_parameter` é servido de cache.
   Troque pelo ORM (`set_param` + `commit`) **e reinicie** o Odoo — o próprio módulo não invalida o
   cache de um processo que já está rodando.
9. **Sem CLI para apagar credencial** nesta versão: limpeza de credencial criada por engano foi feita
   com o serviço parado e `sqlite3` sobre `~/.n8n/database.sqlite` (com backup antes).
