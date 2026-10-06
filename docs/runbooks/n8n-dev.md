# Runbook — n8n no ambiente DEV do TRE

Serviço de automação do projeto (workflows de ingestão, consumidor de outbox, reconciliação e
observabilidade). Este runbook cobre **o n8n provisionado como serviço do ambiente dev**, em
`/opt/tre/dev`, na VPS Contabo `vmi3619453` (169.58.24.102).

- Container: **`n8n-dev`** · imagem **`n8nio/n8n:2.41.5`** (digest `sha256:6f532d3b819c…`)
- Porta: **127.0.0.1:5680 → 5678** (loopback; quem entra de fora é a borda, por hostname)
- Estado: **`/opt/tre/dev/n8n/home`** (bind mount no `/home/node` do container, dono `1000:1000`, 700)
- Segredo: **`/etc/tre/n8n-dev/n8n.env`** (600 root; `N8N_ENCRYPTION_KEY` gerada na VPS)
- Rede interna: **`tre-odoo-dev`** (é por ela que o Odoo chama o webhook e o n8n chama a API)
- Artefatos versionados: `deploy/compose/dev/n8n.yml`, `deploy/environments/dev-n8n.env`,
  `scripts/provision/{instalar,verificar}-n8n-dev.sh`

## 1. Versão: por que 2.41.5 e não "latest"

`n8nio/n8n:latest` nesta VPS **é** 2.41.5 (medido: `docker run --rm --entrypoint n8n
n8nio/n8n:latest --version`) e é a versão que as suítes de n8n do projeto (`scripts/n8n/*`)
exercitaram. Tag flutuante não é aceita: a identidade do que está no ar é o **digest**, conferido
pelo verificador. A estável do npm é 2.41.7 — subir de versão é trocar `N8N_VERSAO` +
`N8N_DIGEST_ESPERADO` no par **depois** de re-rodar as suítes de n8n.

## 2. Instalar / subir

```bash
# na VPS
bash /opt/tre/dev/scripts/instalar-n8n-dev.sh
# reexecutar sobre instalação existente (não recria em silêncio):
TRE_N8N_RECRIAR=1 bash /opt/tre/dev/scripts/instalar-n8n-dev.sh
```

O instalador **recusa** (fail-closed) se: o daemon não responde; o compose não está sob
`/opt/tre/dev`; a rede `tre-odoo-dev` ou o container `odoo-dev` não existem (o n8n do dev depende
dos dois); a porta 5680 está ocupada sem container nosso; o digest baixado difere do registrado.

## 3. Verificar (aceite)

```bash
bash /opt/tre/dev/scripts/verificar-n8n-dev.sh              # 44 itens
bash /opt/tre/dev/scripts/verificar-n8n-dev.sh --prova-de-dente   # 3 mutações em cópia
```

Esperado: `RESULTADO: N8N_DEV_OK (44 itens, 0 falhas)` e `N8N_DEV_DENTE_OK (3 dentes, 0 ruins)`.

O que ele mede, além do óbvio: identidade da imagem em duas pontas (digest do registry do tag **e**
id da imagem que o container roda); porta só em loopback (`ss` + JSON do docker, sem 0.0.0.0);
`healthy` no healthcheck; variáveis que fazem os workflows funcionarem (`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`
para os nós Code lerem `$env`, `N8N_HOST=n8n-dev` para o webhook nascer com nome interno);
a chave existe, é 600 e **não** aparece no artefato publicado nem em argv; o estado está no bind
mount (`~/.n8n/database.sqlite`); os dois sentidos da rede (n8n→Odoo e Odoo→n8n, este último é o
caminho do webhook); e **contaminação**: nenhum n8n de outro ambiente na minha rede ou na minha porta.

## 4. Operação

```bash
docker logs --tail 100 n8n-dev
docker restart n8n-dev
docker inspect n8n-dev --format '{{.State.Health.Status}}'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5680/healthz/readiness   # 200
```

## 5. Rollback

```bash
docker compose --env-file /opt/tre/dev/compose/n8n.env -f /opt/tre/dev/compose/n8n.yml stop
docker compose --env-file /opt/tre/dev/compose/n8n.env -f /opt/tre/dev/compose/n8n.yml down
# estado e segredo ficam intactos: /opt/tre/dev/n8n/home e /etc/tre/n8n-dev/n8n.env
```

`down -v` **não** apaga nada aqui (não há volume nomeado — o estado é bind mount), mas evite: o
`down` simples já é o suficiente e reversível.

## 6. Armadilhas medidas (não repetir)

1. **HOME inteiro tem de ser montado.** Com `--user`, o n8n escreve `~/.n8n` **e** `~/.cache`;
   montar só o `.n8n` morre com `EACCES mkdir '/home/node/.cache'`. Por isso o bind é em `/home/node`.
2. **`install -d -o 1000:1000` não existe.** O `install` só aceita **nome** de usuário
   (`getpwnam`) e morre com `install: invalid user: '1000:1000'`. Use `mkdir -p` + `chown 1000:1000`.
3. **Cuidado ao concatenar uid/gid:** `chown -R "$UID":"$UID"` com `UID="1000:1000"` vira
   `1000:1000:1000:1000` → `chown: invalid group`.
4. **Segredo antes de validar o compose.** `docker compose config` resolve o `env_file` e **falha**
   enquanto o arquivo de segredo não existe — a ordem é segredo → validação. (Armadilha medida duas
   vezes neste projeto: dev e homolog.)
5. **Digest do registry ≠ manifesto por-plataforma.** `RepoDigests` do tag é o manifesto
   multi-arquitetura (o que o par registra); `.ImageManifestDescriptor.Digest` do container é o
   manifesto da plataforma (outro valor, por desenho). Comparar os dois dá falso positivo; a prova
   de que o container roda o tag é o **ID da imagem**.
6. **Porta no JSON do docker não é `"ip:porta"`:** é `{"5678/tcp":[{"HostIp":"127.0.0.1","HostPort":"5680"}]}`.
7. **Chave que já existe não se reescreve:** trocar `N8N_ENCRYPTION_KEY` invalida as credenciais
   já cifradas nos workflows. O instalador preserva.
8. **O uid 1000 do host é o usuário `ubuntu`** (a VPS não tem o `node` do container). O diretório
   é 700 e a chave vive em `/etc/tre/n8n-dev` (600 root) — quem lesse o `database.sqlite` não
   teria como decifrar as credenciais.

## 7. O ciclo ponta a ponta, ligado (medido em 06/10/2026)

O outbox do CRM e a API controlada estão ligados **neste** n8n: credenciais no cofre do próprio
serviço (nunca em arquivo), workflows importados dos JSONs versionados em `n8n/workflows/`.

| peça | identificador | estado |
|---|---|---|
| workflow ingestor (webhook) | `TREodooEventos1` | **ativo** — `POST /webhook/tre/odoo-eventos` |
| workflow consumidor (poll de 1 min) | `TREOUTBOXCONSUM1` | **ativo** |
| cron do módulo no Odoo (`cron_tf_entregar_eventos`) | `ir_cron` id 22 | **ativo** (1 min) |
| credenciais | `tre-dev-postgres`, `tre-dev-api-controlada`, `tre-dev-ingest-token` | no cofre do n8n |

Os dois sentidos foram exercitados com evidência crua:

- **CRM → outbox → n8n → PostgreSQL.** Fatos gerados pelo ORM (`scripts/odoo/gerar_fatos_e_enviar.py`,
  `TRE_FASE=fatos`) → entrega pela porta única com `{"SENT": 8, "DEAD_LETTER": 0, "RETRY": 0}` → 8
  linhas `COMPLETED` na trilha (`crm.lead`, `mail.activity`, `calendar.event`).
- **Fila → n8n → API controlada → CRM.** Evento `COMPANY_QUALIFIED` produzido na fila → status
  `PROCESSED`; resposta da API `{"ok": true, "acao": "upsert", "acao_efetiva": "criar",
  "ambiente": "dev", "operacao": "empresa_upsert"}`; `res.partner` id 15 criado com os `tf_*` do fato
  (`tf_company_id`, `tf_domain`, `tf_cnpj`, `tf_priority_score`).
- **Dedup por chave (replay).** O mesmo evento devolvido à fila → último nó executado
  `Registrar replay (outbox)`, `attempts` permaneceu **1**, trilha com **1 linha**, CRM com
  **1 parceiro** por `tf_company_id` (zero duplicata).
- **Recusa com nome.** Evento fora do contrato (`organization.enriched`) → `DEAD_LETTER` com
  `envelope_sem_event_version` e linha `REFUSED` na trilha: recusa explícita, nunca silêncio.
- **Autonomia.** Com as duas agendas ligadas, um evento novo na fila (`COMPANY_UPDATED` → `PROCESSED`)
  e uma mudança de etapa no CRM feita pelo ORM (`crm.lead STAGE_CHANGED COMPLETED`) chegaram à trilha
  sozinhos em ~1 min, sem ninguém executar nada.

### Ligar / desligar as agendas

```bash
# consumidor (poll de 1 min) — o update:workflow exige o serviço reiniciado depois
docker exec --user 1000:1000 n8n-dev n8n update:workflow --id=TREOUTBOXCONSUM1 --active=true
docker compose --env-file /opt/tre/dev/compose/n8n.env -f /opt/tre/dev/compose/n8n.yml restart n8n-dev
# desligar: --active=false + restart

# ingestor (webhook) — precisa estar ativo para o webhook existir
docker exec --user 1000:1000 n8n-dev n8n update:workflow --id=TREodooEventos1 --active=true

# cron do módulo no Odoo (CRM -> n8n)
#   odoo shell -d odoo_dev --no-http  →  env.ref("transformativa_sales_ai.cron_tf_entregar_eventos").sudo().active = False
```

### Dependências que o ciclo exige (todas medidas)

- **Rede.** O `pg-sales-dev` estava **só na rede `bridge`** — o n8n não o alcançava (`EAI_AGAIN`).
  Resolvido com `docker network connect tre-odoo-dev pg-sales-dev`. **Pendência:** esse container não
  tem rótulos de compose (nasceu avulso) — trazê-lo para um compose versionado é card próprio.
- **Cópia de código própria.** O `odoo-dev` montava `/opt/tre/repo`, a cópia **compartilhada** (hoje
  protegida como produção pelo `publicar.sh`): o Dev executava código que não era o dele. Agora monta
  `/opt/tre/dev/repo`, publicado do `develop` com destino, artefato e trava isolados.
- **`n8n execute` não roda com o servidor de pé** (conflito no task broker, porta 5679). Ciclo manual:
  `stop` → `run --rm --entrypoint n8n execute --id=... --rawOutput` → `start`.

## 8. Pendências declaradas (não são surpresa)

- **UI não exposta publicamente.** A borda hoje serve só os nomes do Odoo (`dev.tre`, `homolog.tre`,
  `tre`). Publicar o n8n por hostname (com autenticação) é **card próprio** — não entrou aqui.
- **`pg-sales-dev` fora do compose** (ver acima).
- **Sem runner externo de tarefas** (o interno basta para os Code nodes do TRE).
